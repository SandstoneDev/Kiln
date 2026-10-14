#!/usr/bin/env python3
"""Builds psp/bin/data/itemicons.pak - the item icons of the client for the
PSP (loot window first, then bags, the character sheet, vendors, rewards),
from the player's own extracted 3.3.5a client (never from this repo).

Item buttons in the original (ItemButtonTemplate, LootFrame,
ContainerFrame...) show Interface\\Icons\\<ItemDisplayInfo.InventoryIcon>; the
PSP had none and drew a question mark. The icons are baked like the spell
icons of spells.pak (32x32 T8, fixed stride) and read on demand:

  "ihdr"  magic "WIIC", version 1, iconCount, iconStride, maxDisplayId, fixedSlots
  "dmap"  u16 per ItemDisplayInfo id 0..maxDisplayId: icon slot, 0xFFFF = none
          (read per id from the card, not kept - 135 KB)
  "icons" iconCount fixed-stride WTEX blobs (32x32 T8)

Fixed slots (the client's C side picks them, not a DBC):
  0 INV_Misc_QuestionMark (unknown item), 1 INV_Misc_Coin_01 (GetCoinIcon: gold),
  2 INV_Misc_Coin_03 (silver), 3 INV_Misc_Coin_05 (copper).

Usage: python build_itemicons_pak.py --extracted <extracted>
       python build_itemicons_pak.py --verify
Run with the normal Python (Pillow), not the MSYS one.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_texconv as tc  # noqa: E402
from build_spells_pak import KIND_RAW, ICON_PX, dbc_path, dbc_str, load_dbc, read_pak  # noqa: E402
from build_ui_pak import write_pak  # noqa: E402

F_ICON = 5  # ItemDisplayInfo.dbc 3.3.5a: 0 ID, 1-2 model, 3-4 model texture, 5-6 InventoryIcon
FIXED = ["inv_misc_questionmark", "inv_misc_coin_01", "inv_misc_coin_03", "inv_misc_coin_05"]


def build(extracted: str, out: str) -> int:
    rows, strs = load_dbc(dbc_path(extracted, "ItemDisplayInfo.dbc"))
    icon_dir = os.path.join(extracted, "interface", "icons")
    slot_of: dict[str, int] = {}
    blobs: list[bytes] = []

    def add(name: str) -> int:
        if name in slot_of:
            return slot_of[name]
        png = os.path.join(icon_dir, name + ".png")
        if not os.path.exists(png):
            return 0xFFFF
        img = Image.open(png).convert("RGBA").resize((ICON_PX, ICON_PX), Image.LANCZOS)
        slot_of[name] = len(blobs)
        blobs.append(tc.wtex_blob(tc.encode(img, "t8", linear=True)))
        return slot_of[name]

    for name in FIXED:
        if add(name) == 0xFFFF:
            print(f"error: fixed icon {name} missing under {icon_dir}", file=sys.stderr)
            return 1
    max_id = max(r[0] for r in rows)
    dmap = [0xFFFF] * (max_id + 1)
    missing = 0
    for r in rows:
        name = dbc_str(strs, r[F_ICON]).replace("\\", "/").split("/")[-1].lower()
        if not name:
            continue
        s = add(name)
        if s == 0xFFFF:
            missing += 1
            s = 0  # the client shows the question mark for a missing icon too
        dmap[r[0]] = s
    stride = (max(len(b) for b in blobs) + 63) // 64 * 64
    icons = b"".join(b + b"\0" * (stride - len(b)) for b in blobs)
    hdr = b"WIIC" + struct.pack("<5I", 1, len(blobs), stride, max_id, len(FIXED))
    write_pak(out, [("ihdr", KIND_RAW, hdr), ("dmap", KIND_RAW, struct.pack(f"<{len(dmap)}H", *dmap)),
                    ("icons", KIND_RAW, icons)])
    mapped = sum(1 for v in dmap if v != 0xFFFF)
    print(f"{out}: icons={len(blobs)} stride={stride} displays={mapped} max_display={max_id} "
          f"missing_icon_files={missing} kb={os.path.getsize(out) // 1024}")
    return 0


def verify(path: str) -> int:
    data, ent = read_pak(path)
    ho, hs = ent["ihdr"][:2]
    magic = data[ho:ho + 4]
    ver, count, stride, max_id, fixed = struct.unpack_from("<5I", data, ho + 4)
    do, ds = ent["dmap"][:2]
    io, isz = ent["icons"][:2]
    ok = magic == b"WIIC" and ver == 1 and ds == (max_id + 1) * 2 and isz == count * stride and fixed == 4
    slots = struct.unpack_from(f"<{max_id + 1}H", data, do)
    bad = sum(1 for s in slots if s != 0xFFFF and s >= count)
    print(f"{path}: {magic.decode()} v{ver} icons={count} stride={stride} max_display={max_id} "
          f"mapped={sum(1 for s in slots if s != 0xFFFF)} bad_slots={bad} -> {'OK' if ok and not bad else 'BAD'}")
    return 0 if ok and not bad else 1


def main() -> int:
    repo = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracted", help="extracted client root (dbfilesclient/, interface/icons/ with PNGs)")
    ap.add_argument("--out", default=os.path.join(repo, "psp", "bin", "data", "itemicons.pak"))
    ap.add_argument("--verify", action="store_true", help="check an existing --out and exit")
    a = ap.parse_args()
    if a.verify:
        return verify(a.out)
    if not a.extracted:
        ap.error("--extracted is required")
    return build(a.extracted, a.out)


if __name__ == "__main__":
    sys.exit(main())
