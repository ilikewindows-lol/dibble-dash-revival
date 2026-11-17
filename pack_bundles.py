"""Rebuild downloadable .unity3d bundles from the game's Unity cache.

The cache (files/UnityCache/Shared/<hash>/) holds each bundle already extracted:
a small __info file (list of the files inside) plus the files themselves, e.g.
    __info, CAB-PfUiStoresPoM
The game, however, asks the server for 'Data/PfUiStoresPoM.unity3d'. This script wraps
every cache folder back into a valid uncompressed Unity bundle ("UnityRaw", stream
version 3, Unity 4.5.4f1, same layout as the game's own DWAPoMRunnerRes.unity3d) and
writes it to www/bundles/<name>.unity3d, where dibble_server.py finds it by file name.

Naming: CAB-<Name> -> <Name>.unity3d ; BuildPlayer-<Name> -> <Name>.unity3d.
Folders whose inner file is a hash (CAB-06472fe3...) carry no name in their file name. For
those, the script looks inside the serialized asset for the name of the object it holds
(Unity stores names as a 4-byte length followed by the text) and matches it against the
asset names in AssetVersionsPoMRunner.xml. Anything still unmatched goes to
www/bundles_unnamed/ and is listed.

Usage:  python pack_bundles.py
"""
import glob, os, re, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "files", "UnityCache", "Shared")
OUT = os.path.join(HERE, "www", "bundles")
OUT_UNNAMED = os.path.join(HERE, "www", "bundles_unnamed")
ASSETS_XML = os.path.join(HERE, "www", "AssetVersionsPoMRunner.xml")
HEX32 = re.compile(r"^[0-9a-f]{32}$")

def build_raw_bundle(entries, engine=b"4.5.4f1", player=b"3.x.x"):
    """entries: list of (name, bytes). Returns the bytes of a UnityRaw (uncompressed) bundle."""
    table_len = 4 + sum(len(n.encode()) + 1 + 8 for n, _ in entries)
    data_header = (table_len + 3) & ~3                     # table padded to 4 bytes
    offs = []; pos = data_header
    for _, b in entries:
        offs.append(pos); pos = (pos + len(b) + 3) & ~3    # each file starts 4-aligned
    body = bytearray(struct.pack(">I", len(entries)))
    for (n, b), o in zip(entries, offs):
        body += n.encode() + b"\0" + struct.pack(">II", o, len(b))
    body += b"\0" * (data_header - len(body))
    for (n, b), o in zip(entries, offs):
        body += b"\0" * (o - len(body)) + b
    data = bytes(body)
    head = b"UnityRaw\0" + struct.pack(">i", 3) + player + b"\0" + engine + b"\0"
    fixed = 4 + 4 + 4 + 4 + 8 + 4 + 4                      # minStream,headerSize,levelsDl,levels,(c,u),complete,dataHdr
    header_size = (len(head) + fixed + 3) & ~3
    complete = header_size + len(data)
    hdr = head + struct.pack(">IiII", complete, header_size, 1, 1) + struct.pack(">II", len(data), len(data)) \
          + struct.pack(">II", complete, data_header)
    hdr += b"\0" * (header_size - len(hdr))
    return hdr + data

def read_info(path):
    lines = [l.strip() for l in open(path, "r", encoding="utf-8", errors="replace").read().splitlines()]
    try: stamp = int(lines[1])
    except Exception: stamp = 0
    names = [l for l in lines[3:] if l]
    return stamp, names

def asset_names():
    """file names (case preserved) of every asset the game knows, from the decrypted list"""
    out = {}
    if os.path.exists(ASSETS_XML):
        txt = open(ASSETS_XML, "r", encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"<AssetName>([^<]+)</AssetName>", txt):
            fn = m.group(1).rsplit("/", 1)[-1]
            out[fn.lower()] = fn
    return out

def base_name(names):
    for n in names:
        if n.startswith("CAB-") and not HEX32.match(n[4:]): return n[4:]
    for n in names:
        if n.startswith("BuildPlayer-") and not n.endswith((".sharedAssets", ".resS")): return n[len("BuildPlayer-"):]
    return None

def match_by_content(blob, wanted):
    """names from `wanted` (lower->Proper) that appear in blob as a length-prefixed string.
    Returns [(offset, ProperName)] sorted by offset."""
    found = []
    for low, proper in wanted.items():
        base = proper[:-len(".unity3d")] if proper.lower().endswith(".unity3d") else proper
        raw = base.encode()
        key = struct.pack("<i", len(raw)) + raw
        i = blob.find(key)
        if i >= 0: found.append((i, proper))
    return sorted(found)

def main():
    if not os.path.isdir(CACHE): sys.exit(f"no cache folder at {CACHE}")
    known = asset_names()
    print(f"{len(known)} asset names known from the version list" if known else
          "no AssetVersionsPoMRunner.xml in www/ (run decrypt_cache.py first); names taken from the cache only")
    os.makedirs(OUT, exist_ok=True)
    best = {}; unnamed = []; skipped = 0
    for folder in sorted(glob.glob(os.path.join(CACHE, "*"))):
        info = os.path.join(folder, "__info")
        if not os.path.isfile(info): skipped += 1; continue
        stamp, names = read_info(info)
        present = [f for f in os.listdir(folder) if f != "__info"]
        order = [n for n in names if n in present] + [f for f in present if f not in names]
        if not order: skipped += 1; continue
        base = base_name(order)
        if base is None: unnamed.append((folder, order, stamp)); continue
        fname = known.get(base.lower() + ".unity3d", base + ".unity3d")
        if fname not in best or stamp > best[fname][0]:
            best[fname] = (stamp, folder, order)
    # hash-named folders: find the asset name inside the file
    if unnamed and known:
        wanted = {k: v for k, v in known.items() if k not in {f.lower() for f in best}}
        cand = []
        for folder, order, stamp in unnamed:
            blob = b"".join(open(os.path.join(folder, n), "rb").read() for n in order)
            cand.append((folder, order, stamp, match_by_content(blob, wanted)))
        taken = {}
        for pass_no in (1, 2):
            for folder, order, stamp, hits in cand:
                if folder in taken.values(): continue
                free = [(o, n) for o, n in hits if n not in taken]
                if (pass_no == 1 and len(free) == 1) or (pass_no == 2 and free):
                    if pass_no == 2: print(f"   (several names inside {order[0]}: {[n for _, n in hits]}; using {free[0][1]})")
                    taken[free[0][1]] = folder
        resolved = {folder: n for n, folder in taken.items()}
        still = []
        for folder, order, stamp, hits in cand:
            if folder in resolved:
                best[resolved[folder]] = (stamp, folder, order)
            else: still.append((folder, order, stamp))
        print(f"matched {len(resolved)} hash-named folders to asset names by their contents")
        unnamed = still
    total = 0
    for fname, (stamp, folder, order) in sorted(best.items()):
        entries = [(n, open(os.path.join(folder, n), "rb").read()) for n in order]
        blob = build_raw_bundle(entries)
        open(os.path.join(OUT, fname), "wb").write(blob); total += len(blob)
    print(f"packed {len(best)} named bundles into {OUT} ({total/1048576:.0f} MB)")
    if unnamed:
        os.makedirs(OUT_UNNAMED, exist_ok=True)
        for folder, order, stamp in unnamed:
            entries = [(n, open(os.path.join(folder, n), "rb").read()) for n in order]
            open(os.path.join(OUT_UNNAMED, order[0] + ".unity3d"), "wb").write(build_raw_bundle(entries))
        print(f"{len(unnamed)} folders have hash-only names (no asset name); written to {OUT_UNNAMED}:")
        for folder, order, stamp in unnamed[:60]: print("   ", order[0])
    if known:
        have = {f.lower() for f in best}
        missing = sorted(v for k, v in known.items() if k not in have and not v.startswith("Dlg"))
        print(f"\nassets in the version list with no cached bundle (excluding voice lines): {len(missing)}")
        for m in missing[:80]: print("   ", m)
    if skipped: print(f"({skipped} cache folders skipped: no __info or no files)")

if __name__ == "__main__":
    main()
