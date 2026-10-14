#!/usr/bin/env python3
"""Every dungeon and raid of the client (Map.dbc InstanceType 1 = party, 2 = raid) and what kind of map
it is - for tools/psp/bake_dungeons.sh (the bake loop) and tools/psp/dungeon_check.py (the PC check).

    python tools/psp/dungeon_list.py [--extracted ../extracted] [--out list.tsv] [--all] [--dirs]

Columns (TSV, a header line): id, dir (Map.dbc Directory = the WDT folder and the paks' name, lower case on the card),
type (party / raid), max players, expansion, kind (wmo = a global WMO, MPHD 0x1: no ADT; tiles = ADT tiles), the WDT's
tile count, the global WMO's path, LoadingScreenID and its file (LoadingScreens.dbc), the ghost's entrance (Map.dbc
corpse map, x, y), unused (1: the client's own "<UNUSED>" / ruRU "(not used)" rows and the test map without a
loading screen - left out of the bake unless --all), the ruRU name.
--dirs: only the directories, one a line (the bake loop's input).
Data: the client's DBC and WDT files only (docs/COMMUNITY_SERVERS.md: nothing from a server's database).
"""
import argparse
import os
import struct
import sys


def read_dbc(path):
    d = open(path, "rb").read()
    magic, n, fields, size, ssize = struct.unpack_from("<4sIIII", d, 0)
    if magic != b"WDBC":
        raise ValueError(path + ": not a DBC")
    recs = d[20:20 + n * size]
    strs = d[20 + n * size:]

    def string(off):
        end = strs.find(b"\0", off)
        return strs[off:end].decode("utf-8", "replace")

    return [recs[i * size:(i + 1) * size] for i in range(n)], fields, string


def u32(r, i):
    return struct.unpack_from("<I", r, i * 4)[0]


def i32(r, i):
    return struct.unpack_from("<i", r, i * 4)[0]


def f32(r, i):
    return struct.unpack_from("<f", r, i * 4)[0]


def localized(r, base, string):
    for k in range(16):
        off = u32(r, base + k)
        if off:
            return string(off)
    return ""


def wdt_info(root, d):
    path = os.path.join(root, "world", "maps", d.lower(), d.lower() + ".wdt")
    info = {"exists": os.path.exists(path), "mphd": 0, "tiles": 0, "wmo": ""}
    if not info["exists"]:
        return info
    data = open(path, "rb").read()
    i = 0
    while i + 8 <= len(data):
        tag = data[i:i + 4][::-1]
        size = struct.unpack_from("<I", data, i + 4)[0]
        if tag == b"MPHD":
            info["mphd"] = struct.unpack_from("<I", data, i + 8)[0]
        elif tag == b"MAIN":
            info["tiles"] = sum(1 for t in range(4096) if struct.unpack_from("<I", data, i + 8 + t * 8)[0] & 1)
        elif tag == b"MWMO":
            info["wmo"] = data[i + 8:i + 8 + size].split(b"\0")[0].decode("latin-1")
        i += 8 + size
    return info


def dungeons(root, include_unused=False):
    dbc = os.path.join(root, "dbfilesclient")
    rows, fields, string = read_dbc(os.path.join(dbc, "map.dbc"))
    screens = {}
    if os.path.exists(os.path.join(dbc, "loadingscreens.dbc")):
        lrows, _, lstr = read_dbc(os.path.join(dbc, "loadingscreens.dbc"))
        for r in lrows:  # ID, Name, FileName, HasWideScreen
            screens[u32(r, 0)] = lstr(u32(r, 2))
    out = []
    for r in rows:
        # Map.dbc 3.3.5a: 0 ID, 1 Directory, 2 InstanceType, 3 Flags, 4 PVP, 5..21 MapName_lang, 22 AreaTableID,
        # 23..56 the descriptions, 57 LoadingScreenID, 58 MinimapIconScale, 59 CorpseMapID, 60 / 61 corpse X / Y,
        # 62 TimeOfDayOverride, 63 ExpansionID, 64 RaidOffset, 65 MaxPlayers.
        itype = u32(r, 2)
        if itype not in (1, 2):
            continue
        m = {
            "id": u32(r, 0), "dir": string(u32(r, 1)), "type": "party" if itype == 1 else "raid",
            "name": localized(r, 5, string), "loading": u32(r, 57), "corpse_map": i32(r, 59),
            "corpse_x": f32(r, 60), "corpse_y": f32(r, 61), "expansion": u32(r, 63), "max_players": u32(r, 65),
        }
        w = wdt_info(root, m["dir"])
        m["wdt"] = w["exists"]
        m["kind"] = "wmo" if (w["mphd"] & 1) and w["wmo"] else "tiles"
        m["tiles"] = w["tiles"]
        m["wmo"] = w["wmo"] if m["kind"] == "wmo" else ""
        m["loading_file"] = screens.get(m["loading"], "")
        low = m["name"].lower()
        m["unused"] = int("<unused>" in low or "не используется" in low or (m["loading"] == 0 and m["corpse_map"] < 0))
        if m["unused"] and not include_unused:
            continue
        out.append(m)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--extracted", default=os.path.join(os.path.dirname(__file__), "..", "..", "..", "extracted"))
    ap.add_argument("--out", default="")
    ap.add_argument("--all", action="store_true", help="also the unused / test maps")
    ap.add_argument("--dirs", action="store_true", help="only the directories, one a line")
    a = ap.parse_args()
    maps = dungeons(a.extracted, a.all)
    lines = []
    if a.dirs:
        lines = [m["dir"] for m in maps]
    else:
        lines.append("\t".join(["id", "dir", "type", "max_players", "expansion", "kind", "tiles", "wmo", "loading",
                                "loading_file", "corpse_map", "corpse_x", "corpse_y", "unused", "name"]))
        for m in maps:
            lines.append("\t".join(str(v) for v in [
                m["id"], m["dir"], m["type"], m["max_players"], m["expansion"], m["kind"], m["tiles"], m["wmo"],
                m["loading"], m["loading_file"], m["corpse_map"], "%.1f" % m["corpse_x"], "%.1f" % m["corpse_y"],
                m["unused"], m["name"]]))
    text = "\n".join(lines) + "\n"
    if a.out:
        open(a.out, "w", encoding="utf-8", newline="\n").write(text)
        print("%d maps -> %s" % (len(maps), a.out), file=sys.stderr)
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
