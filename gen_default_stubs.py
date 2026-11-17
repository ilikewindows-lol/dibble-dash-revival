"""Generate a well-formed default reply for every web-service call the game can make.

The game decodes each reply into a typed object, so an empty reply fails. This script reads the game's own
classes (Assembly-CSharp-firstpass.dll) and writes, for each call, XML in the exact shape the game expects
(names and XML namespaces from its XML attributes, safe default values) to stubs/_any/<Method>.xml, replacing
older hand-made stubs for the same call. Encrypted replies (make_stubs.py) and replies the server computes
itself are left alone. It also writes stubs/_templates/GetUserProfile.xml, which the server completes.

Usage:  python gen_default_stubs.py
"""

import struct

class DN:
    def __init__(self, path):
        self.d = d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        nsec = struct.unpack_from('<H', d, pe + 6)[0]; optsz = struct.unpack_from('<H', d, pe + 20)[0]
        opt = pe + 24; magic = struct.unpack_from('<H', d, opt)[0]
        dd = opt + (96 if magic == 0x10b else 112)
        clr_rva, _ = struct.unpack_from('<II', d, dd + 14 * 8)
        self.secs = []
        st = opt + optsz
        for i in range(nsec):
            n, vs, va, rs, rp = struct.unpack_from('<8sIIII', d, st + i * 40); self.secs.append((va, max(vs, rs), rp))
        off = self.r2o(clr_rva)
        mdrva = struct.unpack_from('<I', d, off + 8)[0]
        mo = self.r2o(mdrva)
        vl = struct.unpack_from('<I', d, mo + 12)[0]
        p = mo + 16 + vl + 2; ns = struct.unpack_from('<H', d, p)[0]; p += 2
        self.heap = {}
        for _ in range(ns):
            o, s = struct.unpack_from('<II', d, p); p += 8
            e = d.index(b'\0', p); nm = d[p:e].decode(); p += (len(nm) + 1 + 3) & ~3
            self.heap[nm] = (mo + o, s)
        self.parse_tables()

    def r2o(self, r):
        for va, sz, rp in self.secs:
            if va <= r < va + sz: return r - va + rp
        return None

    # ---- heaps
    def string(self, i):
        o, s = self.heap['#Strings']; e = self.d.index(b'\0', o + i); return self.d[o + i:e].decode('utf-8', 'replace')
    def us(self, i):
        o, s = self.heap['#US']; p = o + i; b = self.d[p]
        if b & 0x80 == 0: ln = b; p += 1
        elif b & 0xC0 == 0x80: ln = ((b & 0x3f) << 8) | self.d[p + 1]; p += 2
        else: ln = ((b & 0x1f) << 24) | (self.d[p+1] << 16) | (self.d[p+2] << 8) | self.d[p+3]; p += 4
        return self.d[p:p + max(ln - 1, 0)].decode('utf-16le', 'replace')
    def blob(self, i):
        o, s = self.heap['#Blob']; p = o + i; b = self.d[p]
        if b & 0x80 == 0: ln = b; p += 1
        elif b & 0xC0 == 0x80: ln = ((b & 0x3f) << 8) | self.d[p + 1]; p += 2
        else: ln = ((b & 0x1f) << 24) | (self.d[p+1] << 16) | (self.d[p+2] << 8) | self.d[p+3]; p += 4
        return self.d[p:p + ln]

    # ---- tables
    def parse_tables(self):
        d = self.d; o, _ = self.heap['#~']
        heapsz = d[o + 6]; valid = struct.unpack_from('<Q', d, o + 8)[0]
        p = o + 24; rows = {}
        for t in range(64):
            if valid >> t & 1: rows[t] = struct.unpack_from('<I', d, p)[0]; p += 4
        self.rows = rows
        S = 4 if heapsz & 1 else 2; G = 4 if heapsz & 2 else 2; B = 4 if heapsz & 4 else 2
        def idx(t): return 4 if rows.get(t, 0) >= 65536 else 2
        def coded(bits, tabs):
            m = max(rows.get(t, 0) for t in tabs if t is not None)
            return 4 if m >= (1 << (16 - bits)) else 2
        TDR = coded(2, [2, 1, 27]); HC = coded(2, [4, 8, 23]); HCA = coded(5, [6,4,1,2,8,9,10,0,14,23,20,17,26,27,32,35,38,39,40,42,44,43])
        HFM = coded(1, [4, 8]); HDS = coded(2, [2, 6, 32]); MRP = coded(3, [2, 1, 26, 6, 27]); HS = coded(1, [20, 23])
        MDR = coded(1, [6, 10]); MF = coded(1, [4, 6]); IMP = coded(2, [38, 35, 39]); CAT = coded(3, [None, None, 6, 10, None]) if False else coded(3, [6, 10])
        RS = coded(2, [0, 26, 35, 1]); TMD = coded(1, [2, 6])
        sch = {
            0: [2, S, G, G, G], 1: [RS, S, S], 2: [4, S, S, TDR, idx(4), idx(6)], 3: [idx(4)], 4: [2, S, B], 5: [idx(6)],
            6: [4, 2, 2, S, B, idx(8)], 7: [idx(8)], 8: [2, 2, S], 9: [idx(2), TDR], 10: [MRP, S, B], 11: [2, HC, B],
            12: [HCA, CAT, B], 13: [HFM, B], 14: [2, HDS, B], 15: [2, 4, idx(2)], 16: [4, idx(4)], 17: [B], 18: [idx(2), idx(20)],
            19: [idx(20)], 20: [2, S, TDR], 21: [idx(2), idx(23)], 22: [idx(23)], 23: [2, S, B], 24: [2, idx(6), HS],
            25: [idx(2), MDR, MDR], 26: [S], 27: [B], 28: [2, MF, S, idx(26)], 29: [4, idx(4)], 30: [4, 4], 31: [4],
            32: [4, 2, 2, 2, 2, 4, B, S, S], 33: [4], 34: [4, 4, 4], 35: [2, 2, 2, 2, 4, B, S, S, B], 36: [4, idx(35)],
            37: [4, 4, 4, idx(35)], 38: [4, S, B], 39: [4, 4, S, S, IMP], 40: [4, 4, S, IMP], 41: [idx(2), idx(2)],
            42: [2, 2, TMD, S], 43: [MDR, B], 44: [idx(42), TDR]}
        self.tab = {}
        for t in sorted(rows):
            cols = sch[t]; rl = sum(cols); lst = []
            for r in range(rows[t]):
                row = []
                for c in cols:
                    row.append(int.from_bytes(d[p:p + c], 'little')); p += c
                lst.append(row)
            self.tab[t] = lst
        self.TDR = TDR

    def typename(self, t, i):  # i is 0-based row index
        r = self.tab[t][i]
        return (self.string(r[2]) + '.' if self.string(r[2]) else '') + self.string(r[1])

    def memberref(self, i):  # 0-based
        r = self.tab[10][i]; tag = r[0] & 7; ri = (r[0] >> 3) - 1
        cls = '?'
        if tag == 1: cls = self.typename(1, ri)
        elif tag == 0: cls = self.typename(2, ri)
        elif tag == 4: cls = 'TypeSpec'
        return cls, self.string(r[1])

    def method_body(self, mi):  # 0-based MethodDef index
        rva = self.tab[6][mi][0]
        if not rva: return None
        o = self.r2o(rva); b = self.d[o]
        if b & 3 == 2: size = b >> 2; return self.d[o + 1:o + 1 + size]
        flags = struct.unpack_from('<H', self.d, o)[0]; hdr = (flags >> 12) * 4
        size = struct.unpack_from('<I', self.d, o + 4)[0]
        return self.d[o + hdr:o + hdr + size]

    def method_owner(self):
        tds = self.tab[2]; n = len(self.tab[6]); owner = [None] * n
        for ti, r in enumerate(tds):
            start = r[5] - 1; end = (tds[ti + 1][5] - 1) if ti + 1 < len(tds) else n
            for m in range(start, end): owner[m] = ti
        return owner

# ---- IL opcode operand sizes
def _sizes():
    s1 = {i: 0 for i in range(0x100)}
    for i in range(0x0e, 0x14): s1[i] = 1
    s1[0x1f] = 1; s1[0x20] = 4; s1[0x21] = 8; s1[0x22] = 4; s1[0x23] = 8
    for i in (0x27, 0x28, 0x29): s1[i] = 4
    for i in range(0x2b, 0x38): s1[i] = 1
    for i in range(0x38, 0x45): s1[i] = 4
    s1[0x45] = 'switch'
    for i in (0x6f, 0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x79, 0x7b, 0x7c, 0x7d, 0x7e, 0x7f, 0x80, 0x81, 0x8c, 0x8d, 0x8f, 0xa3, 0xa4, 0xa5, 0xc2, 0xc6, 0xd0, 0xdd): s1[i] = 4
    s1[0xde] = 1
    s2 = {i: 0 for i in range(0x20)}
    for i in (0x06, 0x07, 0x15, 0x16, 0x1c): s2[i] = 4
    for i in (0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e): s2[i] = 2
    s2[0x12] = 1
    return s1, s2
S1, S2 = _sizes()

def il(code):
    """yield (offset, opcode, operand_int_or_None)"""
    p = 0; n = len(code)
    while p < n:
        st = p; op = code[p]; p += 1
        if op == 0xfe:
            op = 0xfe00 | code[p]; p += 1; sz = S2.get(op & 0xff, 0)
        else: sz = S1[op]
        if sz == 'switch':
            cnt = struct.unpack_from('<I', code, p)[0]; p += 4 + 4 * cnt; yield st, op, None; continue
        val = int.from_bytes(code[p:p + sz], 'little') if sz else None; p += sz
        yield st, op, val

# ============================ generator ============================
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANAGED = os.path.join(HERE, "unpacked_dibble_dash_stuff", "assets", "bin", "Data", "Managed")
STUBS = os.path.join(HERE, "stubs")
USER_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
PREFER = ("Success", "Ok", "OK", "TokenValid", "Valid", "None")

def readc(b, p):
    v = b[p]
    if v & 0x80 == 0: return v, p + 1
    if v & 0xC0 == 0x80: return ((v & 0x3f) << 8) | b[p + 1], p + 2
    return ((v & 0x1f) << 24) | (b[p+1] << 16) | (b[p+2] << 8) | b[p+3], p + 4

class Model:
    def __init__(self, path):
        self.x = DN(path); x = self.x
        self.tds = x.tab[2]
        self.idx = {x.typename(2, i): i for i in range(len(self.tds))}
        self.owner = x.method_owner()
        self.include_nullables = False

    # ---- types: ('prim',name) ('def',typedef_row) ('ref',name) ('array',t) ('gen',name,[args])
    def tname(self, c):
        tag = c & 3; row = (c >> 2) - 1
        if tag == 0: return ('def', row)
        if tag == 1: return ('ref', self.x.typename(1, row))
        return ('ref', 'TypeSpec')
    def ptype(self, b, p):
        t = b[p]; p += 1
        prim = {0x02: 'bool', 0x03: 'char', 0x04: 'sbyte', 0x05: 'byte', 0x06: 'short', 0x07: 'ushort', 0x08: 'int',
                0x09: 'uint', 0x0a: 'long', 0x0b: 'ulong', 0x0c: 'float', 0x0d: 'double', 0x0e: 'string', 0x1c: 'object'}
        if t in prim: return ('prim', prim[t]), p
        if t in (0x11, 0x12):
            c, p = readc(b, p); return self.tname(c), p
        if t in (0x1d,):
            n, p = self.ptype(b, p); return ('array', n), p
        if t == 0x15:
            g, p = self.ptype(b, p); cnt, p = readc(b, p); a = []
            for _ in range(cnt):
                q, p = self.ptype(b, p); a.append(q)
            return ('gen', g, a), p
        return ('prim', 'object'), p

    # ---- custom attributes
    def attrs(self, tag, row):
        x = self.x; out = []
        for r in x.tab[12]:
            parent, ctor, val = r
            if (parent & 0x1f) != tag or (parent >> 5) - 1 != row: continue
            ct = ctor & 7; cr = (ctor >> 3) - 1
            if ct == 3:
                cls = x.memberref(cr)[0].split('.')[-1]; sig = x.blob(x.tab[10][cr][2])
            elif ct == 2:
                cls = self.x.typename(2, self.owner[cr]).split('.')[-1]; sig = x.blob(x.tab[6][cr][4])
            else: continue
            out.append((cls, self.decode_attr(sig, x.blob(val))))
        return out
    def decode_attr(self, sig, blob):
        res = {}; p = 2
        try:
            n, q = readc(sig, 1); q2 = q + 1  # skip return type (void)
            ptypes = []
            for _ in range(n):
                t = sig[q2]; ptypes.append(t)
                q2 += 1
                if t in (0x11, 0x12): _, q2 = readc(sig, q2)
            def serstr(p):
                if blob[p] == 0xff: return None, p + 1
                l, p = readc(blob, p); return blob[p:p + l].decode('utf-8', 'replace'), p + l
            for t in ptypes:
                if t == 0x0e or t == 0x12: v, p = serstr(p); res.setdefault('_fixed', []).append(v)
                elif t == 0x02: res.setdefault('_fixed', []).append(bool(blob[p])); p += 1
                elif t in (0x08, 0x11): res.setdefault('_fixed', []).append(int.from_bytes(blob[p:p+4], 'little', signed=True)); p += 4
                else: return res
            cnt = int.from_bytes(blob[p:p + 2], 'little'); p += 2
            for _ in range(cnt):
                kind = blob[p]; t = blob[p + 1]; p += 2
                if t == 0x55: _, p = serstr(p)
                nm, p = serstr(p)
                if t in (0x0e, 0x50): v, p = serstr(p)
                elif t == 0x02: v = bool(blob[p]); p += 1
                elif t in (0x08, 0x55): v = int.from_bytes(blob[p:p+4], 'little', signed=True); p += 4
                else: break
                res[nm] = v
        except Exception:
            pass
        return res

    # ---- structure
    def is_enum(self, row):
        r = self.tds[row][3]; tag = r & 3; rr = (r >> 2) - 1
        return tag == 1 and self.x.typename(1, rr) == 'System.Enum'
    def base_row(self, row):
        r = self.tds[row][3]
        return (r >> 2) - 1 if (r & 3) == 0 else None
    def fields(self, row):
        x = self.x; tds = self.tds
        s = tds[row][4] - 1; e = (tds[row + 1][4] - 1) if row + 1 < len(tds) else len(x.tab[4])
        out = []
        base = self.base_row(row)
        if base is not None and not self.is_enum(row): out += self.fields(base)
        for fi in range(s, e):
            fl, nm, sg = x.tab[4][fi]; name = x.string(nm)
            if fl & 0x10 or (fl & 7) != 6 or name == 'value__': continue
            ty = self.ptype(x.blob(sg), 1)[0]
            at = dict(self.attrs(1, fi))
            out.append((name, ty, at, fi))
        return out
    def enum_members(self, row):
        x = self.x; tds = self.tds
        s = tds[row][4] - 1; e = (tds[row + 1][4] - 1) if row + 1 < len(tds) else len(x.tab[4])
        out = []
        for fi in range(s, e):
            nm = x.string(x.tab[4][fi][1])
            if nm == 'value__': continue
            at = dict(self.attrs(1, fi)).get('XmlEnumAttribute', {})
            fx = at.get('_fixed', [None])[0] if at else None
            out.append((nm, fx))
        return out

    def enum_value(self, row):
        mem = self.enum_members(row)
        for pref in PREFER:
            for nm, fx in mem:
                if nm == pref: return fx if fx is not None else nm
        nm, fx = mem[0] if mem else ('0', None)
        return fx if fx is not None else nm

    def simple(self, ty, fname=''):
        """default text for a leaf type, or None if not a leaf"""
        if ty[0] == 'prim':
            n = ty[1]
            if n == 'bool': return 'false' if re.search(r'un(auth|valid)|banned|error|fail|locked', fname, re.I) else 'true'
            if n in ('int', 'long', 'short', 'byte', 'uint', 'ulong', 'ushort', 'sbyte', 'float', 'double'): return '0'
            if n == 'string': return ''
            return ''
        if ty[0] == 'ref':
            if ty[1] == 'System.DateTime': return '2014-01-01T00:00:00'
            if ty[1] == 'System.Guid': return USER_ID
            if ty[1] == 'System.TimeSpan': return 'PT0S'
            return None
        if ty[0] == 'def' and self.is_enum(ty[1]): return self.enum_value(ty[1])
        return None

    def xml_for(self, ty, name, depth, fattrs=None, ns=''):
        """element text for a value of type ty; None => omit.
        ns is the XML namespace in force from the parent; xmlns is written only where it changes."""
        fattrs = fattrs or {}
        if ty[0] == 'gen' and ty[1][0] == 'ref' and ty[1][1].startswith('System.Nullable'):
            if self.include_nullables:
                t = self.simple(ty[2][0], name)
                if t is not None:
                    xa = self.xa(fattrs, ns)
                    return f"<{name}{xa}>{t}</{name}>" if t != '' else f"<{name}{xa} />"
            return None
        if ty[0] == 'array' or (ty[0] == 'gen' and ty[1][0] == 'ref' and 'List' in ty[1][1]):
            if 'XmlArrayAttribute' in fattrs:
                en = fattrs['XmlArrayAttribute'].get('ElementName') or (fattrs['XmlArrayAttribute'].get('_fixed') or [name])[0] or name
                return f"<{en}{self.xa(fattrs, ns, 'XmlArrayAttribute')} />"
            return None
        xa = self.xa(fattrs, ns)
        t = self.simple(ty, name)
        if t is not None:
            return f"<{name}{xa}>{t}</{name}>" if t != '' else f"<{name}{xa} />"
        if ty[0] == 'def' and depth < 2:
            child_ns = self.field_ns(fattrs)
            body = self.body(ty[1], depth + 1, child_ns)
            return f"<{name}{xa}>{body}</{name}>" if body else f"<{name}{xa} />"
        return None

    def field_ns(self, fattrs, key='XmlElementAttribute'):
        return (fattrs.get(key) or {}).get('Namespace') or ''
    def xa(self, fattrs, ns, key='XmlElementAttribute'):
        mine = self.field_ns(fattrs, key)
        return f' xmlns="{mine}"' if mine != ns else ''

    def body(self, row, depth, ns=''):
        parts = []
        for name, ty, at, fi in self.fields(row):
            if 'XmlIgnoreAttribute' in at: continue
            if 'XmlAttributeAttribute' in at: continue
            el = at.get('XmlElementAttribute')
            en = name
            if el:
                en = el.get('ElementName') or (el.get('_fixed') or [None])[0] or name
            x = self.xml_for(ty, en, depth, at, ns)
            if x: parts.append(x)
        return ''.join('\n  ' * 1 + p for p in parts) + ('\n' if parts else '')

    def root_xml(self, ty):
        if ty[0] == 'prim':
            n = {'bool': 'boolean', 'int': 'int', 'string': 'string', 'long': 'long', 'float': 'float', 'double': 'double',
                 'short': 'short', 'byte': 'unsignedByte'}.get(ty[1], 'string')
            v = self.simple(ty, '') if ty[1] != 'bool' else 'true'
            return f"<{n}>{v}</{n}>" if v != '' else f"<{n} />"
        if ty[0] == 'array' or (ty[0] == 'gen' and 'List' in str(ty[1])):
            inner = ty[1] if ty[0] == 'array' else ty[2][0]
            nm = self.tname_str(inner)
            return f"<ArrayOf{nm.split('.')[-1][:1].upper()+nm.split('.')[-1][1:]} />"
        if ty[0] == 'def':
            row = ty[1]
            if self.is_enum(row):
                nm = self.x.typename(2, row).split('.')[-1]
                return f"<{nm}>{self.enum_value(row)}</{nm}>"
            ra = dict(self.attrs(3, row)).get('XmlRootAttribute', {})
            root = ra.get('ElementName') or self.x.typename(2, row).split('.')[-1]
            rns = ra.get('Namespace') or ''
            return f"<{root}{' xmlns=\"%s\"' % rns if rns else ''}>{self.body(row, 0, rns)}</{root}>"
        return "<string />"
    def tname_str(self, ty):
        if ty[0] == 'prim': return ty[1]
        if ty[0] == 'def': return self.x.typename(2, ty[1])
        if ty[0] == 'ref': return ty[1]
        return 'Item'

    # ---- find every web-service call: (wrapper, method name, reply type, encrypted?)
    def service_calls(self):
        x = self.x; out = []
        for mi in range(len(x.tab[6])):
            b = x.method_body(mi)
            if not b: continue
            T = None; name = None; enc = False; last_url = False
            for st, op, v in il(b):
                if op == 0x28 and (v >> 24) == 0x0a:
                    r = x.tab[10][(v & 0xffffff) - 1]
                    if x.string(r[1]) == 'Create' and (r[0] & 7) == 4:
                        gt = self.ptype(x.blob(x.tab[27][(r[0] >> 3) - 1][0]), 0)[0]
                        if gt[0] == 'gen': T = gt[2][0]
                if op == 0x7e:
                    last_url = x.string(x.tab[4][(v & 0xffffff) - 1][1]).endswith('URL')
                elif op == 0x72 and last_url:
                    name = x.us(v & 0xffffff); last_url = False
                if op == 0xfe06 and (v >> 24) == 0x0a:
                    try:
                        if 'Decrypt' in x.memberref((v & 0xffffff) - 1)[1]: enc = True
                    except Exception: pass
            if T and name:
                out.append((x.typename(2, self.owner[mi]) + '::' + x.string(x.tab[6][mi][3]), name, T, enc))
        return out

def main():
    p = os.path.join(MANAGED, "Assembly-CSharp-firstpass.dll")
    if not os.path.exists(p): sys.exit(f"missing {p}")
    m = Model(p)
    calls = m.service_calls()
    dynamic = {"GetUserGameCurrency", "GetGameCurrency", "SetGameCurrency", "GetUserProfile",
               "RecoverPassword", "SendAccountActivationReminder"}      # answered by dibble_server.py itself
    os.makedirs(os.path.join(STUBS, "_any"), exist_ok=True)
    made = removed = enc = 0; seen = set()
    for wrapper, name, T, e in calls:
        if e: enc += 1; continue                                     # encrypted replies: make_stubs.py
        if name in dynamic or name in seen: continue
        seen.add(name)
        # a stub inside a service folder would shadow stubs/_any, so drop older hand-made ones
        for d in os.listdir(STUBS):
            p = os.path.join(STUBS, d, name + ".xml")
            if d != "_any" and os.path.isfile(p): os.remove(p); removed += 1
        xml = '<?xml version="1.0" encoding="utf-8"?>\n' + m.root_xml(T) + '\n'
        with open(os.path.join(STUBS, "_any", name + ".xml"), "w", encoding="utf-8", newline="\n") as f: f.write(xml)
        made += 1
    # GetUserProfile needs EVERY nested object present (the game dereferences them), with the money filled
    # in by the server: write a template that dibble_server.py completes.
    for wrapper, name, T, e in calls:
        if name == "GetUserProfile":
            m.include_nullables = True
            xml = m.root_xml(T)
            m.include_nullables = False
            xml = re.sub(r"<gc([^>]*)>0</gc>", r"<gc\1>{{GC}}</gc>", xml)
            xml = re.sub(r"<cc([^>]*)>0</cc>", r"<cc\1>{{CC}}</cc>", xml)
            xml = xml.replace("<ID />", "<ID>" + USER_ID + "</ID>", 1)
            for tag in ("Username", "FirstName", "DisplayName"):
                xml = re.sub(rf"<{tag}((?: [^>]*?)?) />", rf"<{tag}\1>Player</{tag}>", xml)
            for tag in ("MembershipID", "UserID"):
                xml = re.sub(rf"<{tag}((?: [^>]*?)?) />", rf"<{tag}\1>{USER_ID}</{tag}>", xml)
            os.makedirs(os.path.join(STUBS, "_templates"), exist_ok=True)
            with open(os.path.join(STUBS, "_templates", "GetUserProfile.xml"), "w", encoding="utf-8", newline="\n") as f:
                f.write('<?xml version="1.0" encoding="utf-8"?>\n' + xml + '\n')
            print("wrote stubs/_templates/GetUserProfile.xml (profile with every nested object present)")
            break
    print(f"{len(calls)} service calls found: wrote {made} replies into stubs/_any/ "
          f"(replaced {removed} older hand-made stubs); {enc} encrypted ones come from make_stubs.py, "
          f"{len(dynamic)} are answered by the server itself")

if __name__ == "__main__":
    main()
