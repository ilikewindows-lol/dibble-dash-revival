"""Remove the <html> ... </html> wrapper that got added to the captured config files.

The game decrypts these files itself (they are base64 ciphertext), so any extra
characters around the data break them. Originals of every changed file are saved
in files_backup/ (not in files/, so the server never serves them).

Usage:  python fix_xml_wrappers.py
"""
import glob, os, re, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, "files")
BACKUP = os.path.join(HERE, "files_backup")
B64 = re.compile(rb"^[A-Za-z0-9+/=\s]+$")

fixed = clean = odd = 0
for p in sorted(glob.glob(os.path.join(FILES, "*.xml"))):
    raw = open(p, "rb").read()
    s = raw.strip()
    name = os.path.basename(p)
    if not (s.startswith(b"<html>") or s.endswith(b"</html>")):
        print(f"  ok       {name}")
        clean += 1
        continue
    body = s
    if body.startswith(b"<html>"):
        body = body[len(b"<html>"):]
    if body.endswith(b"</html>"):
        body = body[:-len(b"</html>")]
    body = body.strip()
    if not B64.match(body):
        print(f"  SKIPPED  {name}  (not pure base64 after unwrapping - check it by hand)")
        odd += 1
        continue
    os.makedirs(BACKUP, exist_ok=True)
    shutil.copyfile(p, os.path.join(BACKUP, name))
    with open(p, "wb") as f:
        f.write(body)
    print(f"  FIXED    {name}  ({len(raw)} -> {len(body)} bytes)")
    fixed += 1
print(f"\n{fixed} fixed, {clean} already clean, {odd} skipped")
