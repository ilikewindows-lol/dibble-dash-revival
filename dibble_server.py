"""Stand-in backend for Dibble Dash (Penguins of Madagascar runner), stdlib only.

What it does
  * Serves RS_DATA/* (ProductVersionPoM.xml, PrefetchListPoM.xml, ...) from the
    captured  files/  folder, plus anything you drop in  www/ .
  * Answers the Jumpstart *.asmx web-service calls from  stubs/<Service>/<Method>.xml
    when a stub exists, otherwise with an empty <string/> reply.
  * Logs EVERY request (method, path, headers, body) to requests.log. The real
    protocol is not documented, so the workflow is: run the game, read the log,
    add stubs for the calls it makes, repeat.

Usage:  python dibble_server.py [--port 80] [--bind 0.0.0.0]
"""
import argparse, email.utils, json, os, re, sys, threading, time, urllib.parse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as esc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, "files")
WWW = os.path.join(HERE, "www")
STUBS = os.path.join(HERE, "stubs")
LOG = os.path.join(HERE, "requests.log")
EMPTY = ('<?xml version="1.0" encoding="utf-8"?>\n'
         '<string xmlns="http://api.jumpstart.com/"></string>').encode()

CACHE = os.path.join(FILES, "UnityCache", "Shared")
LOCALE = os.path.join(HERE, "unpacked_dibble_dash_stuff", "assets", "Locale")   # bundled DWAPoMRunnerRes.unity3d etc.

# web-service method -> captured cache file whose content is that call's reply.
# The cached files are the game's own saved copies of the server replies.
AUTO = {"GetAssetVersions": "AssetVersionsPoMRunner.xml"}
WRAP = ('<?xml version="1.0" encoding="utf-8"?>\n<string xmlns="http://api.jumpstart.com/">', "</string>")

def _index(*roots):
    idx = {}
    for root in roots:
        for d, _, fs in os.walk(root):
            for f in fs:
                idx.setdefault(f.lower(), os.path.join(d, f))
    return idx

def find_static(path):
    """Look the requested file up by name (case-insensitive) in files/, www/ and the
    Unity cache, ignoring any RS_DATA/... folder prefix. Traversal is impossible
    because only basenames from a fixed index are served."""
    name = os.path.basename(urllib.parse.unquote(path.split("?", 1)[0]).rstrip("/"))
    if not name:
        return None
    return _index(WWW, LOCALE, FILES, CACHE).get(name.lower())

def listing(path):
    rel = urllib.parse.unquote(path.split("?", 1)[0]).strip("/")
    if rel.lower() not in ("", "rs_data"):
        return None
    names = sorted(f for f in os.listdir(FILES) if os.path.isfile(os.path.join(FILES, f)))
    names += sorted(os.path.relpath(p, HERE) for p in _index(WWW).values())
    return ("<html><body><h3>%s</h3><ul>%s</ul></body></html>" % (
        rel or "/", "".join("<li><a href='/RS_DATA/%s' download>%s</a></li>" % (n, n) for n in names))).encode()

class Server(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets us silently share a port that another program
    # (e.g. nginx) already owns, so requests never reach us. Fail loudly instead.
    allow_reuse_address = False

# ---- account state kept between runs (currency). Starting balance: 100,000 of each. ----
STATE_FILE = os.path.join(HERE, "state.json")
STATE_LOCK = threading.Lock()
START = {"GameCurrency": 100000, "CashCurrency": 100000}
USER_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"

def load_state():
    st = dict(START); st["Inventory"] = {}; st["NextCID"] = 1000; st["Pairs"] = {}; st["Redeemed"] = []
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        for k in START:
            st[k] = int(s.get(k, START[k]))
        inv = s.get("Inventory", {})
        if isinstance(inv, dict):
            st["Inventory"] = {str(k): {"cid": int(v["cid"]), "q": int(v["q"])} for k, v in inv.items()}
        st["NextCID"] = int(s.get("NextCID", 1000))
        st["Redeemed"] = [str(t) for t in s.get("Redeemed", [])][-300:]
        pr = s.get("Pairs", {})
        if isinstance(pr, dict):
            st["Pairs"] = {str(k): {str(a): [str(b[0]), str(b[1])] for a, b in v.items()} for k, v in pr.items()}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return st

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f)

STORE_FILE = os.path.join(HERE, "www", "StoreDataPoM.xml")
FREE_SHOP = False       # True: nothing costs anything
TOP_UP_BELOW = 1000     # a balance under this is refilled to the starting amount (None = never)
_STORE = {"mtime": None, "items": {}, "apps": {}}

def store_data():
    """item prices and pack sizes, read from the game's own (decrypted) store list"""
    try:
        mt = os.path.getmtime(STORE_FILE)
    except OSError:
        return {}, {}
    if _STORE["mtime"] != mt:
        items, apps = {}, {}
        try:
            for it in ET.parse(STORE_FILE).getroot().iter("ItemData"):
                iid = (it.findtext("ItemID") or "").strip()
                if not iid or iid in items: continue
                pi = it.find("PriceInfo")
                ptype = ((pi.findtext("PriceType") if pi is not None else "") or "").strip()
                price = float((pi.findtext("Price") if pi is not None else "0") or 0)
                info = {"price": 0 if ptype == "IAP" else int(round(price)), "ptype": ptype,
                        "itype": (it.findtext("InventoryType") or "").strip(),
                        "qty": int(float(it.findtext("Quantity") or 1)),
                        "app": ((pi.findtext("AppStoreID") if pi is not None else "") or "").strip()}
                items[iid] = info
                if info["app"]: apps[info["app"]] = info
        except (ET.ParseError, ValueError):
            pass
        _STORE.update(mtime=mt, items=items, apps=apps)
    return _STORE["items"], _STORE["apps"]

def payment_reply(method, body):
    """Gold-bar packs bought with (fake) real money: credit the gold bars once per receipt."""
    if method != "RedeemReceipt":
        return None
    req = urllib.parse.parse_qs(body.decode("utf-8", "replace")).get("request", [""])[0]
    mi = re.search(r"<ItemID>\s*([^<]*?)\s*</ItemID>", req)
    mq = re.search(r"<Qty>\s*(\d+)\s*</Qty>", req)
    mt = re.search(r"<TransactionID>\s*([^<]*?)\s*</TransactionID>", req)
    app = mi.group(1) if mi else ""; qty = int(mq.group(1)) if mq else 1; tx = mt.group(1) if mt else ""
    items, apps = store_data()
    note = f"{app} x{qty}"
    with STATE_LOCK:
        st = load_state(); done = st["Redeemed"]
        if tx and tx in done:
            note += " (already credited)"
        else:
            info = apps.get(app)
            if info:
                st["CashCurrency"] += info["qty"] * qty
                note += f": +{info['qty'] * qty} gold bars"
            if tx:
                done.append(tx); st["Redeemed"] = done[-300:]
            save_state(st)
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n<ReceiptRedemptionResult>\n  <Success>true</Success>\n'
           '  <Status>1</Status>\n</ReceiptRedemptionResult>\n')
    return xml.encode("utf-8"), note

def pairs_reply(method, body):
    """The game saves its own progress flags as key/value pairs on the server; keep them between launches."""
    if method not in ("GetKeyValuePair", "SetKeyValuePair"):
        return None
    form = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
    pid = form.get("pairId", ["0"])[0]
    head = '<?xml version="1.0" encoding="utf-8"?>\n'
    with STATE_LOCK:
        st = load_state(); store = st["Pairs"].setdefault(pid, {})
        if method == "SetKeyValuePair":
            n = 0
            try:
                root = ET.fromstring(form.get("contentXML", [""])[0].encode("utf-8"))
                for p in root.iter("Pair"):
                    k = p.findtext("PairKey") or ""
                    if not k: continue
                    store[k] = [p.findtext("PairValue") or "", p.findtext("UpdateDate") or "0001-01-01T00:00:00"]
                    n += 1
            except ET.ParseError:
                pass
            save_state(st)
            return (head + "<boolean>true</boolean>\n").encode("utf-8"), f"pairId {pid}: saved {n}, total {len(store)}"
        items = "".join("\n  <Pair>\n    <PairKey>%s</PairKey>\n    <PairValue>%s</PairValue>\n    <UpdateDate>%s</UpdateDate>\n  </Pair>"
                        % (esc(k), esc(v[0]), esc(v[1])) for k, v in store.items())
    return (head + ("<Pairs>%s\n</Pairs>\n" % items if items else "<Pairs />\n")).encode("utf-8"), f"pairId {pid}: {len(store)} values"

def _cids(pairs):
    return "".join("\n  <cids>\n    <iid>%d</iid>\n    <cid>%d</cid>\n  </cids>" % (i, c) for i, c in pairs)

def inventory_reply(method, body):
    """The server keeps the player's inventory, so bought items survive the next login."""
    if method not in ("PurchaseItems", "SetCommonInventory", "GetCommonInventory", "GetItem"):
        return None
    form = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
    head = '<?xml version="1.0" encoding="utf-8"?>\n'
    with STATE_LOCK:
        st = load_state(); inv = st["Inventory"]
        if method == "GetItem":
            tpl = os.path.join(STUBS, "_any", "GetItem.xml")
            iid = form.get("itemId", ["0"])[0]
            if not os.path.isfile(tpl): return None
            with open(tpl, "r", encoding="utf-8") as f:
                xml = f.read().replace("<id>0</id>", "<id>%s</id>" % iid, 1).replace("<itn />", "<itn>Item %s</itn>" % iid, 1)
            return xml.encode("utf-8"), f"item {iid}"
        if method == "GetCommonInventory":
            items = "".join("\n  <i>\n    <uiid>%d</uiid>\n    <q>%d</q>\n    <u>0</u>\n    <i>\n      <id>%s</id>\n    </i>\n  </i>"
                            % (v["cid"], v["q"], k) for k, v in sorted(inv.items(), key=lambda kv: kv[1]["cid"]))
            xml = head + "<CI>\n  <uid>%s</uid>%s\n  <mSaveWaitingForItems>false</mSaveWaitingForItems>\n</CI>\n" % (USER_ID, items)
            return xml.encode("utf-8"), f"{len(inv)} items"
        pairs = []
        if method == "PurchaseItems":
            ids = re.findall(r"<int>\s*(\d+)\s*</int>", form.get("itemIDArrayXml", [""])[0])
            items_info, _apps = store_data()
            ctype = form.get("currencyType", ["0"])[0]          # 1 = Cheesy Dibbles, 2 = Gold Bars
            for iid in ids:
                info = items_info.get(iid)
                if info and not FREE_SHOP:
                    key = "CashCurrency" if ctype == "2" else "GameCurrency" if ctype == "1" else None
                    if key: st[key] = max(0, st[key] - info["price"])
                if info and info["itype"] == "GC":                # a pack of Cheesy Dibbles: credit the dibbles
                    st["GameCurrency"] += info["qty"]
                    pairs.append((int(iid), st["NextCID"])); st["NextCID"] += 1
                    continue
                it = inv.get(iid)
                if it: it["q"] += 1
                else:
                    inv[iid] = it = {"cid": st["NextCID"], "q": 1}; st["NextCID"] += 1
                pairs.append((int(iid), it["cid"]))
            note = "bought " + ",".join(ids)
        else:                                      # SetCommonInventory: the game sets quantities
            req = form.get("commonInventoryRequestXml", [""])[0]
            for iid, q in re.findall(r"<iid>\s*(\d+)\s*</iid>\s*<q>\s*(-?\d+)\s*</q>", req):
                q = int(q)
                if q <= 0:
                    inv.pop(iid, None); continue
                it = inv.get(iid)
                if it: it["q"] = q
                else:
                    inv[iid] = it = {"cid": st["NextCID"], "q": q}; st["NextCID"] += 1
                pairs.append((int(iid), it["cid"]))
            note = "set " + ",".join(str(p[0]) for p in pairs)
        save_state(st)
    return (head + "<CIRS>\n  <s>true</s>" + _cids(pairs) + "\n</CIRS>\n").encode("utf-8"), note

MAIL_DIR = os.path.join(HERE, "mailbox")

def write_mail(to, subject, text):
    """The 'emails' the game asks the server to send are saved as .eml files in mailbox/."""
    os.makedirs(MAIL_DIR, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^A-Za-z0-9]+", "_", subject)[:40] + ".eml"
    msg = ("From: Dibble Dash <noreply@dibbledash.local>\r\nTo: %s\r\nSubject: %s\r\nDate: %s\r\n"
           "MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n%s\r\n"
           % (to, subject, email.utils.formatdate(localtime=True), text))
    with open(os.path.join(MAIL_DIR, name), "w", encoding="utf-8", newline="") as f:
        f.write(msg)
    return name

def dynamic_reply(method, body):
    """Replies that depend on saved state. Returns (xml_bytes, note) or None."""
    r = inventory_reply(method, body) or pairs_reply(method, body) or payment_reply(method, body)
    if r is not None:
        return r
    if method in ("RecoverPassword", "SendAccountActivationReminder"):
        form = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
        head = '<?xml version="1.0" encoding="utf-8"?>\n'
        if method == "RecoverPassword":
            to = form.get("email", [""])[0] or "(address not sent)"
            user = form.get("username", [""])[0] or "(not sent)"
            name = write_mail(to, "Dibble Dash: password reset",
                "Hello,\r\n\r\nA password reset was requested for user name: %s.\r\n\r\n"
                "This is a private revival server and it does not check passwords: any password works.\r\n" % user)
            return (head + "<MembershipUserStatus>Success</MembershipUserStatus>\n").encode(), f"mail -> mailbox/{name}"
        name = write_mail("(your account email)", "Dibble Dash: activate your account",
            "Hello,\r\n\r\nThis is your account activation reminder.\r\n\r\n"
            "Nothing needs to be activated on this private revival server: your account is already active.\r\n")
        return (head + "<ActivationReminderResponse>1</ActivationReminderResponse>\n").encode(), f"mail -> mailbox/{name}"
    if method not in ("GetUserGameCurrency", "GetGameCurrency", "SetGameCurrency", "GetUserProfile"):
        return None
    with STATE_LOCK:
        st = load_state()
        if TOP_UP_BELOW and method in ("GetUserGameCurrency", "GetGameCurrency", "GetUserProfile"):
            low = [k for k in ("GameCurrency", "CashCurrency") if st[k] < TOP_UP_BELOW]
            for k in low: st[k] = START[k]
            if low: save_state(st)
        note = f"game={st['GameCurrency']} cash={st['CashCurrency']}"
        if method == "SetGameCurrency":
            # the game sends the CHANGE since its last sync as 'amount'; the reply is the new total
            try:
                delta = int(urllib.parse.parse_qs(body.decode("utf-8", "replace")).get("amount", ["0"])[0])
            except ValueError:
                delta = 0
            st["GameCurrency"] += delta
            save_state(st)
            note = f"{delta:+d} -> game={st['GameCurrency']}"
            xml = '<?xml version="1.0" encoding="utf-8"?>\n<int>%d</int>\n' % st["GameCurrency"]
        elif method == "GetUserProfile":
            if not os.path.exists(STATE_FILE): save_state(st)
            xml = ('<?xml version="1.0" encoding="utf-8"?>\n<UserProfileDisplayData>\n'
                   '  <ID>%s</ID>\n  <Ach>0</Ach>\n  <Mth>0</Mth>\n'
                   '  <gc>%d</gc>\n  <cc>%d</cc>\n  <BuddyCount>0</BuddyCount>\n'
                   '</UserProfileDisplayData>\n') % (USER_ID, st["GameCurrency"], st["CashCurrency"])
            tpl = os.path.join(STUBS, "_templates", "GetUserProfile.xml")
            if os.path.isfile(tpl):
                with open(tpl, "r", encoding="utf-8") as f:
                    xml = f.read().replace("{{GC}}", str(st["GameCurrency"])).replace("{{CC}}", str(st["CashCurrency"]))
        elif method == "GetGameCurrency":
            xml = '<?xml version="1.0" encoding="utf-8"?>\n<int>%d</int>\n' % st["GameCurrency"]
        else:
            if not os.path.exists(STATE_FILE): save_state(st)
            xml = ('<?xml version="1.0" encoding="utf-8"?>\n<UserGameCurrency>\n'
                   '  <UserGameCurrencyID>1</UserGameCurrencyID>\n  <UserID>%s</UserID>\n'
                   '  <GameCurrency>%d</GameCurrency>\n  <CashCurrency>%d</CashCurrency>\n'
                   '</UserGameCurrency>\n') % (USER_ID, st["GameCurrency"], st["CashCurrency"])
    return xml.encode("utf-8"), note

class H(BaseHTTPRequestHandler):
    server_version = "DibbleServer/0.1"

    def log_message(self, *a):
        pass

    def _record(self, body, status, note):
        line = f"{time.strftime('%H:%M:%S')} {self.command} {self.path} -> {status} {note}"
        print(line)
        with open(LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
            f.write("  Host: %s\n" % self.headers.get("Host"))
            for k in ("Content-Type", "SOAPAction", "User-Agent"):
                if self.headers.get(k):
                    f.write(f"  {k}: {self.headers[k]}\n")
            if body:
                f.write("  BODY: " + body[:65536].decode("utf-8", "replace") + "\n")

    def _send(self, status, data, ctype, head=False, disposition=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        head = self.command == "HEAD"
        path = self.path.split("?", 1)[0]
        parts = [p for p in path.split("/") if p]

        page = listing(path)
        if page is not None:
            self._record(body, 200, "directory listing")
            return self._send(200, page, "text/html", head)

        static = find_static(path)
        if static:
            data = open(static, "rb").read()
            ctype = "text/xml" if static.endswith(".xml") else "application/octet-stream"
            disp = None
            if "text/html" in (self.headers.get("Accept") or ""):   # a browser, not the game
                ctype = "application/octet-stream"
                disp = 'attachment; filename="%s"' % os.path.basename(static)
            self._record(body, 200, "static " + os.path.relpath(static, HERE))
            return self._send(200, data, ctype, head, disp)

        asmx = next((i for i, p in enumerate(parts) if p.lower().endswith(".asmx")), None)
        if asmx is not None:
            svc = parts[asmx][:-5]
            method = parts[asmx + 1] if len(parts) > asmx + 1 else ""
            dyn = dynamic_reply(method, body)
            if dyn is not None:
                self._record(body, 200, f"dynamic {svc}/{method} ({dyn[1]})")
                return self._send(200, dyn[0], "text/xml; charset=utf-8", head)
            stub = os.path.join(STUBS, svc, method + ".xml")
            if not os.path.isfile(stub):
                stub = os.path.join(STUBS, "_any", method + ".xml")
            if method and os.path.isfile(stub):
                self._record(body, 200, f"stub {svc}/{method}")
                return self._send(200, open(stub, "rb").read(), "text/xml; charset=utf-8", head)
            auto = AUTO.get(method)
            if auto and os.path.isfile(os.path.join(FILES, auto)):
                payload = open(os.path.join(FILES, auto), "rb").read().strip()
                self._record(body, 200, f"auto {svc}/{method} <- files/{auto} ({len(payload)} bytes)")
                return self._send(200, WRAP[0].encode() + payload + WRAP[1].encode(),
                                  "text/xml; charset=utf-8", head)
            self._record(body, 200, f"EMPTY reply (add stubs/{svc}/{method}.xml)")
            return self._send(200, EMPTY, "text/xml; charset=utf-8", head)

        self._record(body, 404, "NOT FOUND (no file named %r in files/, www/ or UnityCache)" % os.path.basename(path))
        self._send(404, b"not found", "text/plain", head)

    do_GET = do_POST = do_HEAD = do_PUT = _handle

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--bind", default="0.0.0.0")
    a = ap.parse_args()
    for d in (WWW, STUBS):
        os.makedirs(d, exist_ok=True)
    print(f"serving on {a.bind}:{a.port}  files={FILES}  log={LOG}")
    try:
        Server((a.bind, a.port), H).serve_forever()
    except OSError as e:
        sys.exit(f"cannot listen on port {a.port}: {e}\nSomething else is already using it. Find it with:  netstat -ano | findstr :{a.port}")
