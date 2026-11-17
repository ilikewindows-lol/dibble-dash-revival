"""Find the game's secret and decrypt its cached config files (files/*.xml).

How the game does it (from Assembly-CSharp-firstpass.dll, UtUtilities.SaveTextToFile /
LoadTextFromFile + TripleDES.EncryptUnicode / DecryptUnicode):
    key        = MD5( UTF-16LE( secret ) )          -> 16 bytes (2-key Triple DES)
    cipher     = Triple DES, ECB mode, PKCS7 padding
    plaintext  = the XML text as UTF-16LE
    file       = base64( ciphertext ) + newline
The secret is ProductConfig.pSecret (a string loaded from the game's product settings), so
this script tries every string found in the game's DLLs and data files as the secret.

Needs:  pip install pycryptodome      (or: pip install cryptography)
Usage:  python decrypt_cache.py
On success it writes plain XML into www/ (the server prefers www/ over files/) and saves
the secret to secret.txt.
"""
import base64, glob, hashlib, os, re, sys

try:
    from Crypto.Cipher import DES3
    def tdes_ecb_decrypt(key16, data):
        return DES3.new(key16, DES3.MODE_ECB).decrypt(data)
except ImportError:
    try:
        try:
            from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
        except ImportError:
            from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        def tdes_ecb_decrypt(key16, data):
            d = Cipher(TripleDES(key16 + key16[:8]), modes.ECB()).decryptor()
            return d.update(data) + d.finalize()
    except ImportError:
        sys.exit("Install one of:  pip install pycryptodome   |   pip install cryptography")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "unpacked_dibble_dash_stuff")
FILES = os.path.join(HERE, "files")
WWW = os.path.join(HERE, "www")
APIKEY = b"1908BA66-49C5-4D43-BBCC-C798DD43A368"       # seen in the analytics requests

GUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
HEX32 = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])")

def load_cipher(path):
    d = base64.b64decode(open(path, "rb").read().strip())
    return d[:len(d) - len(d) % 8]

def unpad(b):
    n = b[-1] if b else 0
    return b[:-n] if 1 <= n <= 8 and b.endswith(bytes([n]) * n) else b

def as_xml_text(b):
    try:
        t = unpad(b).decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    if not t: return None
    ok = sum(1 for c in t if c in "\t\r\n" or 32 <= ord(c) < 127)
    return t if ok / len(t) > 0.97 and "<" in t else None

def try_secret(secret, ct):
    for enc in ("utf-16-le", "utf-8"):
        try:
            key = hashlib.md5(secret.encode(enc)).digest()
            t = as_xml_text(tdes_ecb_decrypt(key, ct))
        except Exception:
            continue
        if t: return enc, t
    return None

def runs(data):
    """printable ASCII runs (UTF-8 strings) and UTF-16LE runs"""
    for m in re.finditer(rb"[\x20-\x7e]{4,200}", data): yield m.group().decode()
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,200}", data): yield m.group().decode("utf-16le")

def expand(s):
    """a run plus the pieces a length-prefixed serialized string could be hiding in"""
    out = {s}
    out.update(GUID.findall(s)); out.update(HEX32.findall(s))
    for k in (1, 2, 3, 4):
        if len(s) > k + 3: out.add(s[k:])
    return out

def sources():
    paths = glob.glob(os.path.join(ROOT, "assets", "bin", "Managed", "*.dll")) + \
            glob.glob(os.path.join(ROOT, "assets", "bin", "Data", "Managed", "*.dll"))
    paths = [p for p in paths if os.path.basename(p).startswith(("Assembly-CSharp", "KnowledgeAdventure"))]
    data_dir = os.path.join(ROOT, "assets", "bin", "Data")
    if os.path.isdir(data_dir):
        for f in sorted(glob.glob(os.path.join(data_dir, "*"))):
            if os.path.isfile(f) and not f.endswith(".orig"): paths.append(f)
    paths.append(os.path.join(ROOT, "AndroidManifest.xml"))
    return paths

def main():
    cf = os.path.join(FILES, "ProductVersionPoM.xml")
    if not os.path.exists(cf): sys.exit(f"missing {cf}")
    ct = load_cipher(cf)
    print(f"cipher file: ProductVersionPoM.xml ({len(ct)} bytes)")
    tried = set(); n = 0; neighbours = []
    # the .splitN files are one asset cut in pieces: join them so strings are not cut in half
    split = sorted(glob.glob(os.path.join(ROOT, "assets", "bin", "Data", "*.split*")),
                   key=lambda p: int(re.search(r"split(\d+)$", p).group(1)))
    blobs = []
    for p in sources():
        if ".split" in os.path.basename(p): continue
        try: blobs.append((os.path.basename(p), open(p, "rb").read()))
        except OSError: pass
    if split: blobs.append(("sharedassets (joined splits)", b"".join(open(p, "rb").read() for p in split)))
    for name, data in blobs:
        if APIKEY in data:
            i = data.index(APIKEY)
            neighbours.append((name, [s for s in runs(data[max(0, i - 200):i + 300])]))
        for s in runs(data):
            for c in expand(s):
                if c in tried: continue
                tried.add(c); n += 1
                r = try_secret(c, ct)
                if r:
                    enc, text = r
                    print(f"\n*** SECRET FOUND in {name}: {c!r} (key = MD5 of the {enc} bytes) ***")
                    print("  plain text starts:", text[:120].replace("\n", " "))
                    return finish(c, enc)
    print(f"tried {n} candidate secrets from {len(blobs)} files: none decrypted the cache.")
    for name, ss in neighbours:
        print(f"\nThe analytics API key appears in {name}; strings around it (the secret is probably one of them):")
        for s in ss: print("   ", repr(s))
    if not neighbours:
        print("\nThe API key", APIKEY.decode(), "was not found in the data files either,")
        print("so the secret lives in an asset I have not seen. Tell me which files are in assets/bin/Data.")

def finish(secret, enc):
    os.makedirs(WWW, exist_ok=True)
    open(os.path.join(HERE, "secret.txt"), "w", encoding="utf-8").write(secret + "\n")
    k = 0
    for p in sorted(glob.glob(os.path.join(FILES, "*.xml"))):
        try:
            r = try_secret(secret, load_cipher(p))
        except Exception:
            r = None
        name = os.path.basename(p)
        if not r:
            print(f"  skipped (not encrypted with this secret): {name}"); continue
        text = r[1].rstrip("\r\n\x00") + "\r\n"
        open(os.path.join(WWW, name), "wb").write(text.encode("utf-8")); k += 1
        print(f"  decrypted {name} -> www/ ({len(text)} chars)")
    print(f"{k} files written to {WWW}; secret saved to secret.txt")

if __name__ == "__main__":
    main()
