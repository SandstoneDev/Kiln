#!/usr/bin/env python3
"""data/taxi.pak - the flight paths of the original client for the PSP's
TaxiFrame and taxi flights (src/platform/psp/psp_world_session_taxi.cpp, src/ui/psp/ui_world_taxi.cpp),
from the player's own extracted 3.3.5a client (DBFilesClient + Interface\\TaxiFrame) - nothing from a
server database (the target is any community 3.3.5a server: docs/COMMUNITY_SERVERS.md).

    python tools/psp/build_taxi_pak.py --extracted <extracted> \\
        [--maps 0,1,530,571] [--out psp/bin/data/taxi.pak] [--verify] [--preview <dir>] \\
        [--mount-displays psp/bin/creatures/taxi_mount_displays.tsv]
    python tools/psp/build_taxi_pak.py --dump [--out psp/bin/data/taxi.pak]

Needs (extracted, lower case): dbfilesclient/taxinodes.dbc, taxipath.dbc, taxipathnode.dbc,
worldmapcontinent.dbc; interface/taxiframe/taximap<map>.png (optional: without it the window draws the
nodes on the frame's dark background). Pillow + numpy (the texture), as build_ui_pak.py.

Records (one set per continent, read only for the map the player is on - the pak is opened, the entries
read and the pak closed again):
  "taxi_<map>"    raw (kind 3)  - header + nodes + edges + names, all little-endian:
      header  48 B: "WTXI", u32 version (1), u32 mapId, u32 nodeCount, u32 edgeCount, u32 waypointCount,
                    f32 taxiMinX, taxiMinY, taxiMaxX, taxiMaxY (WorldMapContinent TaxiMin / TaxiMax: the
                    square of the world the TaxiMap picture covers, server x = north, y = west),
                    u32 namesSize, u32 reserved
      node    28 B: u32 id, f32 x, y, z (TaxiNodes, server coordinates), u32 nameOffset (UTF-8, the ruRU
                    column - the one with the most names), u32 mountCreature[2] (TaxiNodes
                    MountCreatureID: [0] Horde, [1] Alliance - creature entries, kept for the log)
                    - ascending id (upstream's taxiNodeOrder: the flight map lists nodes by id)
      edge    20 B: u16 fromNode, u16 toNode, u32 pathId, u32 cost (copper), u32 wpFirst, u32 wpCount
                    (TaxiPath rows whose both ends are nodes of this map; their TaxiPathNode points are
                    "taxiwp_<map>"[wpFirst .. wpFirst + wpCount), ordered by NodeIndex)
      names   namesSize B
  "taxiwp_<map>"  raw (kind 3)  - waypointCount x f32[3] (TaxiPathNode Loc, server coordinates): read by
                    range (pakRead) for the hops of one flight only.
  "taximap_<map>" WTEX T8       - Interface\\TaxiFrame\\TaxiMap<map> at the PSP's TaxiFrame scale 0.6
                    (TaxiMap 316 x 352 units -> 190 x 211 px, 256 x 256 storage, 65 KB).

--mount-displays: the CreatureDisplayInfo rows (DBC only) whose model is one of the taxi mounts
(gryphon, wind rider, the bat taxi mount, hippogryph, the skeletal gryphon of a class start area) in the format of
tools/psp/mount_displays.py ("display<TAB>0<TAB>0<TAB>model"): the server puts one of them in our
UNIT_FIELD_MOUNTDISPLAYID for a flight (WorldSession::SendDoFlight -> Unit::Mount), and the PSP draws it
only if creatures.pak has it (psp_creature_conv --mounts). The upstream client resolves the same models by
path (EntitySpawner: Creature\\Gryphon\\Gryphon.m2 / Creature\\Wyvern\\Wyvern.m2).
Not needed for the bake any more - creatures.pak holds every
CreatureDisplayInfo row (the taxi beasts included) and psp_creature_conv ignores --mounts; the list stays
as a report.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KIND_TEXTURE = 1
KIND_RAW = 3
TAXI_S = 0.6                   # TaxiFrame units -> px (ui_world_taxi.cpp kS)
MAP_W, MAP_H = 316, 352        # TaxiMap / TaxiRouteMap (TaxiFrame.xml), TAXI_MAP_WIDTH / TAXI_MAP_HEIGHT
HEADER = struct.Struct("<4sIIIII4fII")
NODE = struct.Struct("<Ifff III")
EDGE = struct.Struct("<HHIIII")
assert HEADER.size == 48 and NODE.size == 28 and EDGE.size == 20

# The taxi mounts' models (CreatureModelData paths, lower case, '/').
TAXI_MOUNT_MODELS = (
    "creature/gryphon/gryphon.mdx",            # Alliance (TaxiNodes MountCreatureID 541 ...)
    "creature/wyvern/wyvern.mdx",              # Horde wind rider (2224 ...)
    "creature/felbat/battaxi.mdx",             # the bat taxi mount (3574)
    "creature/hippogryph/hippogryph.mdx",      # the hippogryph of map 1 (3837)
    "creature/gryphon/gryphon_skeletal.mdx",   # the skeletal gryphon (death knight start area)
)


def read_dbc(path: str):
    with open(path, "rb") as f:
        d = f.read()
    magic, n, fields, rec_size, str_size = struct.unpack_from("<4s4I", d)
    if magic != b"WDBC":
        raise ValueError(f"{path}: not a WDBC")
    recs = d[20:20 + n * rec_size]
    strings = d[20 + n * rec_size:20 + n * rec_size + str_size]

    def string(off: int) -> str:
        if off <= 0 or off >= len(strings):
            return ""
        return strings[off:strings.index(b"\0", off)].decode("utf-8", "replace")

    rows = [struct.unpack_from(f"<{fields}I", recs, i * rec_size) for i in range(n)]
    return rows, string


def f32(u: int) -> float:
    return struct.unpack("<f", struct.pack("<I", u))[0]


def dbc_path(root: str, name: str) -> str:
    for cand in (os.path.join(root, "dbfilesclient", name.lower()), os.path.join(root, "DBFilesClient", name)):
        if os.path.exists(cand):
            return cand
    sys.exit(f"{name} not found under {root}/dbfilesclient (extract DBFilesClient first)")


def load_tables(root: str):
    # TaxiNodes 3.3.5a: ID, ContinentID, X, Y, Z, Name_lang[16] + flags, MountCreatureID[2] (24 fields).
    nodes, nstr = read_dbc(dbc_path(root, "TaxiNodes.dbc"))
    # The localized name column with the most names (enUS 0 .. ruRU 8), as build_spells_pak.py.
    loc = max(range(16), key=lambda k: sum(1 for r in nodes if r[5 + k]))
    # TaxiPath: ID, FromTaxiNode, ToTaxiNode, Cost.
    paths, _ = read_dbc(dbc_path(root, "TaxiPath.dbc"))
    # TaxiPathNode: ID, PathID, NodeIndex, ContinentID, Loc[3], Flags, Delay, ArrivalEventID, DepartureEventID.
    pnodes, _ = read_dbc(dbc_path(root, "TaxiPathNode.dbc"))
    # WorldMapContinent: ID, MapID, Left, Right, Top, Bottom, ContinentOffset[2], Scale, TaxiMin[2], TaxiMax[2],
    # WorldMapID.
    wmc, _ = read_dbc(dbc_path(root, "WorldMapContinent.dbc"))
    bounds = {r[1]: (f32(r[9]), f32(r[10]), f32(r[11]), f32(r[12])) for r in wmc}
    return nodes, nstr, loc, paths, pnodes, bounds


def build_map(root: str, map_id: int, tables) -> tuple[list[tuple[str, int, bytes]], dict]:
    nodes, nstr, loc, paths, pnodes, bounds = tables
    if map_id not in bounds:
        print(f"  map {map_id}: no WorldMapContinent row (no taxi map), skipped")
        return [], {}
    mine = sorted((r for r in nodes if r[1] == map_id), key=lambda r: r[0])
    ids = {r[0] for r in mine}
    edges = sorted((r for r in paths if r[1] in ids and r[2] in ids), key=lambda r: (r[1], r[2], r[0]))
    by_path: dict[int, list] = {}
    for r in pnodes:
        by_path.setdefault(r[1], []).append(r)
    names = bytearray(b"\0")
    name_off = {}

    def add_name(s: str) -> int:
        if not s:
            return 0
        if s not in name_off:
            name_off[s] = len(names)
            names.extend(s.encode("utf-8") + b"\0")
        return name_off[s]

    node_blob = b""
    for r in mine:
        name = nstr(r[5 + loc]) or nstr(r[5])
        node_blob += NODE.pack(r[0], f32(r[2]), f32(r[3]), f32(r[4]), add_name(name), r[22], r[23])
    wp = []
    edge_blob = b""
    other_map = 0
    for r in edges:
        pts = sorted(by_path.get(r[0], []), key=lambda p: p[2])
        other_map += sum(1 for p in pts if p[3] != map_id)
        first = len(wp)
        wp.extend((f32(p[4]), f32(p[5]), f32(p[6])) for p in pts)
        edge_blob += EDGE.pack(r[1], r[2], r[0], r[3], first, len(pts))
    tmin_x, tmin_y, tmax_x, tmax_y = bounds[map_id]
    header = HEADER.pack(b"WTXI", 1, map_id, len(mine), len(edges), len(wp), tmin_x, tmin_y, tmax_x, tmax_y,
                         len(names), 0)
    out = [(f"taxi_{map_id}", KIND_RAW, header + node_blob + edge_blob + bytes(names)),
           (f"taxiwp_{map_id}", KIND_RAW, b"".join(struct.pack("<3f", *p) for p in wp))]
    img_rel = f"interface/taxiframe/taximap{map_id}.png"
    img_path = os.path.join(root, img_rel)
    if os.path.exists(img_path):
        from PIL import Image
        import psp_texconv as tc
        img = Image.open(img_path).convert("RGBA").resize((round(MAP_W * TAXI_S), round(MAP_H * TAXI_S)),
                                                           Image.LANCZOS)
        out.append((f"taximap_{map_id}", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    info = dict(nodes=len(mine), edges=len(edges), waypoints=len(wp), other_map=other_map,
                no_wp=sum(1 for r in edges if not by_path.get(r[0])), map_img=os.path.exists(img_path),
                names=len(name_off))
    return out, info


def taxi_uv(bounds, x: float, y: float) -> tuple[float, float]:
    """TaxiNodePosition: (u from the left, v from the bottom) of the TaxiMap - west (+y) on the left,
    north (+x) at the top (ui_world_taxi.cpp taxiNodeUv)."""
    min_x, min_y, max_x, max_y = bounds
    return (max_y - y) / (max_y - min_y), (x - min_x) / (max_x - min_x)


def write_pak(path: str, entries) -> None:
    import build_ui_pak  # noqa: E402 (same folder)
    build_ui_pak.write_pak(path, entries)


def read_pak(path: str) -> dict[str, tuple[int, bytes]]:
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"WPAK":
        sys.exit(f"{path}: not a WPAK")
    _, count, toc = struct.unpack_from("<III", data, 4)
    out = {}
    for i in range(count):
        name, off, size, kind, _ = struct.unpack_from("<48sIIII", data, toc + 64 * i)
        out[name.split(b"\0", 1)[0].decode()] = (kind, data[off:off + size])
    return out


def parse_taxi(blob: bytes):
    magic, ver, map_id, nn, ne, nw, a, b, c, d, nsize, _ = HEADER.unpack_from(blob, 0)
    if magic != b"WTXI" or ver != 1:
        raise ValueError("bad taxi header")
    o = HEADER.size
    nodes = [NODE.unpack_from(blob, o + i * NODE.size) for i in range(nn)]
    o += nn * NODE.size
    edges = [EDGE.unpack_from(blob, o + i * EDGE.size) for i in range(ne)]
    o += ne * EDGE.size
    names = blob[o:o + nsize]
    if len(names) != nsize or len(blob) != o + nsize:
        raise ValueError("bad taxi size")
    return map_id, nodes, edges, names, nw, (a, b, c, d)


def verify(path: str, maps: list[int]) -> int:
    pak = read_pak(path)
    fails = 0
    for m in maps:
        if f"taxi_{m}" not in pak:
            continue
        map_id, nodes, edges, names, nw, bounds = parse_taxi(pak[f"taxi_{m}"][1])
        wp = pak[f"taxiwp_{m}"][1]
        ids = [n[0] for n in nodes]
        if map_id != m or ids != sorted(ids) or len(wp) != nw * 12:
            print(f"verify taxi_{m}: FAIL header/order/waypoints")
            fails += 1
        for e in edges:
            if e[0] not in ids or e[1] not in ids or e[4] + e[5] > nw:
                print(f"verify taxi_{m}: FAIL edge {e}")
                fails += 1
                break
        for n in nodes:
            if n[4] and (n[4] >= len(names) or names[n[4] - 1] != 0):
                print(f"verify taxi_{m}: FAIL name offset of node {n[0]}")
                fails += 1
                break
        if f"taximap_{m}" in pak:
            t = pak[f"taximap_{m}"][1]
            if t[:4] != b"WTEX" or t[4] != 5:
                print(f"verify taximap_{m}: FAIL not a T8 WTEX")
                fails += 1
        print(f"verify taxi_{m}: nodes={len(nodes)} edges={len(edges)} waypoints={nw} ok")
    print("verify: " + ("OK" if not fails else f"{fails} failure(s)"))
    return 3 if fails else 0


def preview(root: str, out_dir: str, map_id: int, tables) -> None:
    """A PNG of the TaxiMap (at the frame's 316 x 352 units) with every node of the map at its
    TaxiNodePosition and the TaxiPath edges as lines - the projection check."""
    from PIL import Image, ImageDraw
    nodes, nstr, loc, paths, _, bounds = tables
    if map_id not in bounds:
        return
    p = os.path.join(root, f"interface/taxiframe/taximap{map_id}.png")
    img = (Image.open(p).convert("RGBA") if os.path.exists(p) else Image.new("RGBA", (512, 512), (40, 40, 40, 255)))
    img = img.resize((MAP_W * 2, MAP_H * 2))
    dr = ImageDraw.Draw(img)
    pos = {}
    for r in nodes:
        if r[1] != map_id or (r[2] == 0 and r[3] == 0):
            continue
        u, v = taxi_uv(bounds[map_id], f32(r[2]), f32(r[3]))
        pos[r[0]] = (u * MAP_W * 2, (1.0 - v) * MAP_H * 2)
    for r in paths:
        if r[1] in pos and r[2] in pos:
            dr.line([pos[r[1]], pos[r[2]]], fill=(20, 20, 20, 255), width=2)
    for nid, (x, y) in pos.items():
        dr.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
        dr.text((x + 6, y - 6), str(nid), fill=(255, 255, 0, 255))
    os.makedirs(out_dir, exist_ok=True)
    img.save(os.path.join(out_dir, f"taximap_{map_id}_nodes.png"))


def mount_displays(root: str, out: str) -> None:
    cmd, cs = read_dbc(dbc_path(root, "CreatureModelData.dbc"))
    cdi, ds = read_dbc(dbc_path(root, "CreatureDisplayInfo.dbc"))
    model = {r[0]: cs(r[2]).lower().replace("\\", "/") for r in cmd}
    rows = [(r[0], model.get(r[1], "")) for r in cdi if model.get(r[1], "") in TAXI_MOUNT_MODELS]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# display\tentry\tspell\tname (taxi mounts from CreatureDisplayInfo by model)\n")
        for d, m in sorted(rows):
            f.write(f"{d}\t0\t0\t{m}\n")
    print(f"taxi mount displays: {len(rows)} ({len({m for _, m in rows})} models) -> {out}")


def main() -> int:
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracted", help="extracted client root (dbfilesclient/, interface/taxiframe/)")
    ap.add_argument("--out", default=os.path.join(repo, "psp", "bin", "data", "taxi.pak"))
    ap.add_argument("--maps", default="0,1,530,571", help="continents (map ids), comma separated")
    ap.add_argument("--verify", action="store_true", help="read the written pak back and check it (exit 3 on error)")
    ap.add_argument("--preview", help="directory for taximap_<map>_nodes.png (node projection check)")
    ap.add_argument("--mount-displays", help="also write the taxi mounts' display list (TSV) here")
    ap.add_argument("--dump", action="store_true", help="list an existing --out pak and exit")
    a = ap.parse_args()
    if a.dump:
        pak = read_pak(a.out)
        for name, (kind, blob) in pak.items():
            extra = ""
            if name.startswith("taxi_"):
                m, nodes, edges, names, nw, b = parse_taxi(blob)
                extra = f"  nodes={len(nodes)} edges={len(edges)} waypoints={nw} names={len(names)} B"
            print(f"  {name:16s} kind={kind} {len(blob):7d} B{extra}")
        return 0
    if not a.extracted:
        ap.error("--extracted is required")
    maps = [int(m) for m in a.maps.split(",") if m.strip()]
    tables = load_tables(a.extracted)
    entries = []
    for m in maps:
        out, info = build_map(a.extracted, m, tables)
        if not out:
            continue
        entries += out
        kb = sum(len(b) for _, _, b in out) / 1024.0
        print(f"  map {m}: nodes={info['nodes']} edges={info['edges']} waypoints={info['waypoints']} "
              f"(other map {info['other_map']}, edges without points {info['no_wp']}) names={info['names']} "
              f"map_img={'yes' if info['map_img'] else 'no'}  {kb:.1f} KB")
        for name, _, blob in out:
            print(f"    {name:16s} {len(blob):7d} B")
        if a.preview:
            preview(a.extracted, a.preview, m, tables)
    write_pak(a.out, entries)
    print(f"wrote {a.out}: {len(entries)} entries, {os.path.getsize(a.out)} B")
    if a.mount_displays:
        mount_displays(a.extracted, a.mount_displays)
    return verify(a.out, maps) if a.verify else 0


if __name__ == "__main__":
    sys.exit(main())
