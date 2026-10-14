"""Which maps to bake, read from the user's own extracted client tables (nothing about the maps is
stored in Kiln itself).

* Map table (the client's DBFilesClient map table, WDBC): column 0 id, 1 directory, 2 instance type
  (0 open world, 1 party instance, 2 raid, 3 battleground, 4 arena), 57 loading-screen id.
* A map's WDT (world/maps/<dir>/<dir>.wdt): MPHD flag 0x1 = the map is one global WMO (no terrain
  tiles); MAIN = 64 x 64 entries, flag 0x1 = the tile has terrain; MWMO = the global WMO's path.
"""
from __future__ import annotations

import os
import struct
import subprocess
from dataclasses import dataclass, field

# The four open-world maps the port streams (map ids, the same numbers the port's taxi and light
# bakers default to).
CONTINENT_IDS = (0, 1, 530, 571)


@dataclass
class MapInfo:
    id: int
    dir: str
    itype: int
    loading: int
    wdt: bool = False
    global_wmo: bool = False
    tiles: list = field(default_factory=list)   # [(x, y)] in the client's x-major order

    @property
    def lower(self) -> str:
        return self.dir.lower()


def read_dbc(path):
    with open(path, "rb") as f:
        d = f.read()
    magic, n, fields, size, _ssize = struct.unpack_from("<4sIIII", d, 0)
    if magic != b"WDBC":
        raise ValueError("%s: not a WDBC table" % path)
    recs = [d[20 + i * size:20 + (i + 1) * size] for i in range(n)]
    strs = d[20 + n * size:]

    def string(off):
        end = strs.find(b"\0", off)
        return strs[off:end].decode("utf-8", "replace")

    return recs, fields, string


def _u32(r, i):
    return struct.unpack_from("<I", r, i * 4)[0]


def read_wdt(path):
    """-> (exists, global_wmo, tiles)"""
    if not os.path.exists(path):
        return False, False, []
    with open(path, "rb") as f:
        d = f.read()
    mphd, tiles, wmo = 0, [], ""
    i = 0
    while i + 8 <= len(d):
        tag = d[i:i + 4][::-1]
        size = struct.unpack_from("<I", d, i + 4)[0]
        if tag == b"MPHD" and i + 12 <= len(d):
            mphd = struct.unpack_from("<I", d, i + 8)[0]
        elif tag == b"MAIN" and i + 8 + 4096 * 8 <= len(d):
            flags = struct.unpack_from("<8192I", d, i + 8)[0::2]
            tiles = [(x, y) for x in range(64) for y in range(64) if flags[y * 64 + x] & 1]
        elif tag == b"MWMO":
            wmo = d[i + 8:i + 8 + size].split(b"\0")[0].decode("latin-1")
        i += 8 + size
    return True, bool(mphd & 1) and bool(wmo), tiles


def load_maps(extracted):
    path = os.path.join(extracted, "dbfilesclient", "map.dbc")
    recs, _fields, string = read_dbc(path)
    out = {}
    for r in recs:
        m = MapInfo(id=_u32(r, 0), dir=string(_u32(r, 1)), itype=_u32(r, 2), loading=_u32(r, 57))
        m.wdt, m.global_wmo, m.tiles = read_wdt(os.path.join(extracted, "world", "maps", m.lower, m.lower + ".wdt"))
        out[m.id] = m
    return out


def _bakeable(m):
    return m.wdt and (m.global_wmo or m.tiles)


def select_maps(extracted, groups, python=None, dungeon_lister=None, log=print):
    """groups: subset of {'continents', 'dungeons', 'battlegrounds', 'arenas'} -> [(group, MapInfo)].

    Dungeons and raids come from the port's own list script when it is available (it leaves out the
    rows the client itself marks as unused); otherwise every party/raid map with terrain or a global
    WMO and a loading screen."""
    maps = load_maps(extracted)
    out = []
    if "continents" in groups:
        for mid in CONTINENT_IDS:
            m = maps.get(mid)
            if m and _bakeable(m):
                out.append(("continents", m))
    if "dungeons" in groups:
        dirs = None
        if dungeon_lister and python and os.path.exists(dungeon_lister):
            r = subprocess.run([python, dungeon_lister, "--extracted", extracted, "--dirs"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0:
                dirs = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
            else:
                log("  dungeon list script failed (rc %d), using Kiln's own rule" % r.returncode)
        by_dir = {m.lower: m for m in maps.values()}
        if dirs is not None:
            chosen = [by_dir[d.lower()] for d in dirs if d.lower() in by_dir]
        else:
            chosen = [m for m in maps.values() if m.itype in (1, 2) and m.loading]
        out += [("dungeons", m) for m in chosen if _bakeable(m)]
    if "battlegrounds" in groups:
        out += [("battlegrounds", m) for m in maps.values() if m.itype == 3 and _bakeable(m)]
    if "arenas" in groups:
        out += [("arenas", m) for m in maps.values() if m.itype == 4 and _bakeable(m)]
    seen, uniq = set(), []
    for g, m in out:
        if m.id not in seen:
            seen.add(m.id)
            uniq.append((g, m))
    return uniq, maps
