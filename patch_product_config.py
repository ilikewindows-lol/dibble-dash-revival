"""Point Dibble Dash at your own server by rewriting its built-in server URLs.

Files patched (all inside unpacked_dibble_dash_stuff/assets/bin/Data/):
  3c9505fe...  ProductConfig: the *.api.jumpstart.com web-service URLs
  8b35c1ae...  table of media.jumpstart.com content base URLs (dev/qa/staging/live)
  c47d5c3a...  Unibill (in-app purchase) config URL on media.jumpstart.com

These are serialized Unity TextAssets with length prefixes, so every URL keeps
EXACTLY the same character count: it becomes http://<label>.<domain>[:port]/...
with the label padded or trimmed to fit (https:// is downgraded to http://).
The server ignores the Host header, so the label is cosmetic.

It also extracts the ProductConfig XML to www/DWAPoMRunner.xml, because the game
downloads that file from the content server at startup ("Prod config").

Usage:
  python patch_product_config.py --domain lvh.me --port 8080 --dry-run
  python patch_product_config.py --domain lvh.me --port 8080
  python patch_product_config.py --restore
"""
import argparse, os, re, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "unpacked_dibble_dash_stuff", "assets", "bin", "Data")
CONFIG = "3c9505fe0ad6642cba4289a4c1e9d018"
TARGETS = [CONFIG, "8b35c1aec3a668d419cbf97b3aaf0771", "c47d5c3a58db54614b3a4b556fc179dc"]
URL = re.compile(rb"(https?)://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*?)\.jumpstart\.com(?=/)")

def new_url(m, domain, port=None):
    sub = m.group(2).decode()
    total = len(m.group(0))
    prefix = "http://"
    tail = f":{port}" if port else ""
    room = total - len(prefix) - 1 - len(domain) - len(tail)   # chars left for the label
    if room < 1:
        raise SystemExit(f"domain '{domain}' is too long for {m.group(0).decode()} "
                         f"(domain + port must be <= {total - len(prefix) - 2} chars here)")
    label = (sub.replace(".", "-") + "x" * 64)[:room]
    if label.endswith("-"):
        label = label[:-1] + "x"
    out = f"{prefix}{label}.{domain}{tail}"
    assert len(out) == total
    return out.encode()

REL = re.compile(rb"<(ContentDataURL|DataURL|SharedDataURL|SoundURL|MoviesURL)>\./([^<]+)</\1>")

def extract_config(data_dir, out_path, domain, port=None):
    """Write the bundled ProductConfig to www/DWAPoMRunner.xml, the file the game downloads at
    startup. The bundled copy has relative data folders (./Data ...) which the game cannot
    use as web addresses ("no protocol"), so they are made absolute here. This file is
    served over HTTP, so its length is free to change."""
    raw = open(os.path.join(data_dir, CONFIG), "rb").read()
    a = raw.find(b"<?xml")
    end = raw.rfind(b"</ProductConfig>")
    if a < 0 or end < 0:
        print("could not find the ProductConfig XML inside", CONFIG); return
    xml = raw[a:end + len(b"</ProductConfig>")]
    base = f"http://mediaxx.{domain}" + (f":{port}" if port else "") + "/DWAMobile/PoMRunner/Android/1.0.1/"
    xml, n = REL.subn(lambda m: b"<%s>%s%s</%s>" % (m.group(1), base.encode(), m.group(2), m.group(1)), xml)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "wb").write(xml + b"\r\n")
    print(f"wrote {out_path} ({n} relative data folders made absolute)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=DATA)
    ap.add_argument("--domain", help="wildcard domain, e.g. lvh.me")
    ap.add_argument("--port", type=int, help="append :PORT to every URL (e.g. 8080 for adb reverse)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    if a.restore:
        for t in TARGETS:
            p = os.path.join(a.data_dir, t)
            if os.path.exists(p + ".orig"):
                shutil.copyfile(p + ".orig", p); print("restored", t)
        return
    if not a.domain:
        ap.error("--domain is required")
    for t in TARGETS:
        p = os.path.join(a.data_dir, t)
        if not os.path.exists(p):
            print(f"== {t}: not found, skipped"); continue
        bak = p + ".orig"
        cur = open(p, "rb").read()
        src = open(bak, "rb").read() if os.path.exists(bak) else cur   # always patch from the pristine copy
        n = 0
        def sub(m):
            nonlocal n; n += 1
            r = new_url(m, a.domain, a.port)
            print(f"  {m.group(0).decode():55s} -> {r.decode()}")
            return r
        print(f"== {t}")
        out = URL.sub(sub, src)
        assert len(out) == len(src), "size changed - refusing to write"
        if n == 0:
            print("  no jumpstart.com URLs found (already patched?) - left alone"); continue
        print(f"  {n} URLs, size unchanged ({len(out)} bytes)")
        if a.dry_run: continue
        if not os.path.exists(bak): shutil.copyfile(p, bak)
        open(p, "wb").write(out)
    if not a.dry_run:
        extract_config(a.data_dir, os.path.join(HERE, "www", "DWAPoMRunner.xml"), a.domain, a.port)

if __name__ == "__main__":
    main()
