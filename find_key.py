"""Find the AES key the game uses for its cached config files, then decrypt them.

Evidence: files/*.xml are base64 of AES-ECB data (repeated 16-byte blocks). The key is
somewhere in the game's code, either as a string or as a raw byte array. This script
tries (1) every string in the game's DLLs and a set of common derivations of it
(raw, MD5, SHA1, SHA256, hex/base64-decoded, GUID forms), then (2) every 16/24/32-byte
window of the DLLs as a raw key. The decrypted text may be UTF-8 or UTF-16 (.NET style).
A key is accepted only if it turns the whole cached file into readable text.

Needs:  pip install pycryptodome      (or: pip install cryptography)
Usage:  python find_key.py            (add --no-windows to skip the slower stage 2)
On success it writes the decrypted files into www/ (the server prefers www/ over files/).
"""
import argparse, base64, glob, hashlib, os, re, sys, time

try:
    from Crypto.Cipher import AES as _AES
    def ecb(key, data): return _AES.new(key, _AES.MODE_ECB).decrypt(data)
except ImportError:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        def ecb(key, data):
            d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
            return d.update(data) + d.finalize()
    except ImportError:
        sys.exit("Install one of:  pip install pycryptodome   |   pip install cryptography")

HERE = os.path.dirname(os.path.abspath(__file__))
MANAGED = os.path.join(HERE, "unpacked_dibble_dash_stuff", "assets", "bin", "Data", "Managed")
FILES = os.path.join(HERE, "files")
WWW = os.path.join(HERE, "www")
DLLS = ["Assembly-CSharp.dll", "Assembly-CSharp-firstpass.dll"]

def printable(b):
    return all(c in (9, 10, 13) or 32 <= c < 127 for c in b)

def as_text(b):
    """Decode plaintext that may be UTF-8, UTF-8 with BOM, or UTF-16 (LE/BE, .NET style)."""
    try:
        if b.startswith(b"\xff\xfe"): return b[2:].decode("utf-16-le")
        if b.startswith(b"\xfe\xff"): return b[2:].decode("utf-16-be")
        if b.startswith(b"\xef\xbb\xbf"): return b[3:].decode("utf-8")
        if len(b) >= 4 and b[1] == 0 and b[3] == 0: return b.decode("utf-16-le")
        if len(b) >= 4 and b[0] == 0 and b[2] == 0: return b.decode("utf-16-be")
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return None

def good_text(b):
    t = as_text(b) if b else None
    if not t: return False
    ok = sum(1 for c in t if c in "\t\r\n" or 32 <= ord(c) < 127)
    return ok / len(t) > 0.97 and "<" in t

def quick_ok(first):
    """first decrypted block must look like the start of an XML document in any encoding."""
    f = first[2:] if first[:2] in (b"\xff\xfe", b"\xfe\xff") else first[3:] if first[:3] == b"\xef\xbb\xbf" else first
    if f[:1] == b"<" and printable(f): return True                       # UTF-8
    if f[:2] == b"<\x00" and all(x == 0 for x in f[1::2]) and printable(f[0::2]): return True   # UTF-16LE
    if f[:2] == b"\x00<" and all(x == 0 for x in f[0::2]) and printable(f[1::2]): return True   # UTF-16BE
    return False

def unpad(b):
    n = b[-1] if b else 0
    return b[:-n] if 1 <= n <= 16 and b.endswith(bytes([n]) * n) else b

def load_cipher(path):
    raw = open(path, "rb").read().strip()
    d = base64.b64decode(raw)
    if len(d) % 16: d = d[:len(d) - len(d) % 16]
    return d

def try_key(key, ct):
    """cheap filter on the first block, then full check."""
    if not quick_ok(ecb(key, ct[:16])):
        return None
    full = ecb(key, ct)
    return unpad(full) if good_text(unpad(full)) else None

def strings_from(data):
    out = set()
    for m in re.finditer(rb"[\x20-\x7e]{4,100}", data):
        out.add(m.group().decode())
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,100}", data):
        out.add(m.group().decode("utf-16le"))
    return out

def derive(s):
    b, u = s.encode("utf-8"), s.encode("utf-16le")
    keys = [b[:n].ljust(n, b"\0") for n in (16, 24, 32)]
    for h in (hashlib.md5, hashlib.sha1, hashlib.sha256):
        for enc in (b, u):
            dg = h(enc).digest()
            keys += [dg[:n] for n in (16, 24, 32) if len(dg) >= n]
    if re.fullmatch(r"[0-9a-fA-F]+", s) and len(s) in (32, 48, 64):
        keys.append(bytes.fromhex(s))
    g = s.strip("{}")
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", g):
        h = bytes.fromhex(g.replace("-", ""))
        keys.append(h)                                                   # big-endian order
        keys.append(h[3::-1] + h[5:3:-1] + h[7:5:-1] + h[8:])            # .NET Guid.ToByteArray()
    for h in (hashlib.md5, hashlib.sha1, hashlib.sha256):                # hex-digest text used as key
        hd = h(b).hexdigest().encode()
        keys += [hd[:n] for n in (16, 24, 32) if len(hd) >= n]
    if re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", s) and len(s) % 4 == 0:
        try:
            r = base64.b64decode(s)
            if len(r) in (16, 24, 32): keys.append(r)
        except Exception:
            pass
    return keys

SEEDS = ["com.KnowledgeAdventure.PoMRunner", "KnowledgeAdventure", "PoMRunner", "PoM", "DWAPoMRunner",
         "DWAMobile", "Dibble Dash", "DibbleDash", "Jumpstart", "JumpStart", "186",
         "1908BA66-49C5-4D43-BBCC-C798DD43A368", "6e9b6ab7-6e10-4b82-8daf-5f1073dbf1ed"]

def extra_strings():
    out = set(SEEDS)
    root = os.path.join(HERE, "unpacked_dibble_dash_stuff")
    cand = [os.path.join(root, "AndroidManifest.xml"), os.path.join(root, "apktool.json")]
    data_dir = os.path.join(root, "assets", "bin", "Data")
    if os.path.isdir(data_dir):
        for f in glob.glob(os.path.join(data_dir, "*")):
            if os.path.isfile(f) and os.path.getsize(f) < 300000 and not f.endswith((".orig", ".png")):
                cand.append(f)
    for p in cand:
        try: out |= strings_from(open(p, "rb").read())
        except OSError: pass
    return out

def report(key, how, plain_first):
    print(f"\n*** KEY FOUND ({how}) ***\n  hex  : {key.hex()}\n  ascii: {key!r}")
    print("  starts with:", (as_text(plain_first) or "")[:80].encode("unicode_escape"))

def decrypt_all(key):
    os.makedirs(WWW, exist_ok=True)
    n = 0
    for p in sorted(glob.glob(os.path.join(FILES, "*.xml"))):
        try:
            ct = load_cipher(p)
            full = ecb(key, ct)
        except Exception:
            continue
        if not good_text(unpad(full)):
            print(f"  skipped (does not decrypt to text): {os.path.basename(p)}"); continue
        out = os.path.join(WWW, os.path.basename(p))
        text = as_text(unpad(full))
        open(out, "wb").write(text.encode("utf-8")); n += 1
        print(f"  decrypted {os.path.basename(p)} -> www/ ({len(text)} chars)")
    print(f"{n} files written to {WWW}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cipher", default=os.path.join(FILES, "ProductVersionPoM.xml"))
    ap.add_argument("--no-windows", action="store_true")
    a = ap.parse_args()
    ct = load_cipher(a.cipher)
    print(f"cipher file: {os.path.basename(a.cipher)} ({len(ct)} bytes)")
    blobs = []
    for name in DLLS:
        p = os.path.join(MANAGED, name)
        if os.path.exists(p): blobs.append((name, open(p, "rb").read()))
    for p in glob.glob(os.path.join(MANAGED, "*.dll")):
        if os.path.basename(p) not in DLLS and "KnowledgeAdventure" in os.path.basename(p):
            blobs.append((os.path.basename(p), open(p, "rb").read()))
    if not blobs: sys.exit(f"no DLLs found in {MANAGED}")

    t0 = time.time(); seen = set(); tested = 0
    for name, data in blobs:
        ss = strings_from(data) | (extra_strings() if name == blobs[0][0] else set())
        print(f"stage 1: {len(ss)} strings from {name}")
        for s in ss:
            for k in derive(s):
                if len(k) in (16, 24, 32) and k not in seen:
                    seen.add(k); tested += 1
                    r = try_key(k, ct)
                    if r is not None:
                        report(k, f"derived from string {s!r} in {name}", r)
                        return decrypt_all(k)
    print(f"stage 1 done: {tested} candidate keys, nothing matched ({time.time()-t0:.0f}s)")
    if a.no_windows: return
    for name, data in blobs:
        print(f"stage 2: raw byte windows of {name} ({len(data)} bytes)")
        for n in (16, 32, 24):
            for i in range(len(data) - n + 1):
                k = data[i:i + n]
                if k in seen or len(set(k)) < 6: continue
                r = try_key(k, ct)
                if r is not None:
                    report(k, f"raw bytes at offset 0x{i:x} in {name}", r)
                    return decrypt_all(k)
                if i % 500000 == 0 and i: print(f"  ...{name} size {n}: offset {i}")
    print("No key found in the DLL strings or raw bytes. The key may be derived at runtime"
          " (e.g. from the device or with a salt). Send me this output.")

if __name__ == "__main__":
    main()
