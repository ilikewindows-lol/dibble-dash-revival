"""Scan the unpacked Dibble Dash APK for server URLs/hostnames.

Run on your PC:  python find_server_urls.py
Prints every URL-like string (UTF-8 and UTF-16LE, since .NET stores
string literals as UTF-16) with its file, offset and character length,
so a replacement server name can be matched to the exact length.
"""
import os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unpacked_dibble_dash_stuff")
TARGETS = [
    os.path.join(ROOT, "assets", "bin", "Data", "Managed"),
    os.path.join(ROOT, "assets", "bin", "Data"),
    os.path.join(ROOT, "smali"),
    os.path.join(ROOT, "res", "values"),
    os.path.join(ROOT, "AndroidManifest.xml"),
]

U8 = re.compile(rb"(?:https?://|ftp://)?[A-Za-z0-9][A-Za-z0-9.\-]{2,}\.(?:com|net|org|io|us|co|info)(?::\d+)?(?:/[\x21-\x7e]{0,120})?")
U16 = re.compile(rb"(?:(?:[A-Za-z0-9./:_\-?=&%]\x00){6,})")
KEYWORDS = (b"http", b".com", b".net", b"cdn", b"server", b"amazonaws", b"smartfox")

def files(path):
    if os.path.isfile(path):
        yield path
    elif os.path.isdir(path):
        for d, _, fs in os.walk(path):
            for f in fs:
                yield os.path.join(d, f)

seen = set()
for t in TARGETS:
    for fp in files(t):
        if fp in seen or os.path.getsize(fp) > 64 * 1024 * 1024:
            continue
        seen.add(fp)
        try:
            data = open(fp, "rb").read()
        except OSError:
            continue
        for m in U8.finditer(data):
            s = m.group().decode("ascii", "ignore")
            if any(k in m.group().lower() for k in (b"http", b".com", b".net")):
                print(f"[utf8 ] {len(s):4d} chars  {os.path.relpath(fp, ROOT)} @0x{m.start():x}  {s}")
        for m in U16.finditer(data):
            raw = m.group()
            s = raw.decode("utf-16le", "ignore")
            if any(k in s.lower().encode() for k in KEYWORDS):
                print(f"[utf16] {len(s):4d} chars  {os.path.relpath(fp, ROOT)} @0x{m.start():x}  {s}")
