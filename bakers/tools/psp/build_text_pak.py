#!/usr/bin/env python3
"""Builds psp/bin/data/text.pak - small ruRU text tables the PSP needs to show
the server's texts the way the Russian client does, from the player's own
extracted 3.3.5a client (never from this repo).

Quest and gossip texts of a ruRU realm carry the client's declension
markup: "|3-6($C)" = the class name in case 6. The client declines through
DeclinedWord.dbc / DeclinedWordCases.dbc (cases 1..5: genitive, dative,
accusative, instrumental, prepositional; 6: nominative lower case; 7/8:
plural). The PSP keeps only what $C / $R expand to: the class and race names
(ChrClasses / ChrRaces, ruRU), male and female, with their cases.

  "decl"  u32 count, then per word: the nominative, then cases 1..8, each
          a NUL-terminated UTF-8 string ("" = the case is missing)
  "names" u32 11 classes + u32 12 races: male name, female name (NUL-terminated),
          indexed by class / race id (empty for unused ids)
  "zones" (the quest log's headers - QuestLogFrame groups by the quest's
          ZoneOrSort): u32 count, then per record i32 key + NUL-terminated ruRU name;
          key > 0 = AreaTable id, key < 0 = -QuestSort id
  "faction_text" (character tabs: ReputationFrame - the faction names / descriptions and the parent
          chain that groups the list, as upstream's loadFactionNameCache reads Faction.dbc): u32 count,
          then per row (by id) u32 id, u32 ReputationListID (0xFFFFFFFF = none), u32 ParentFactionID,
          the ruRU name and description (NUL-terminated UTF-8)
  "skill_text" (character tabs: SkillFrame - upstream's loadSkillLineDbc): u32 category count, per
          SkillLineCategory row u32 id, u32 SortIndex, the ruRU name; u32 skill count, per SkillLine row
          u32 id, u32 CategoryID, the ruRU name and description (NUL-terminated UTF-8)
  "pet_text" (pets: the pet frame, the stable, the pet talents - include/platform/psp/psp_pet_text.hpp):
          "WPET", u32 version 1; u32 count + per CreatureFamily row u32 id, i32 PetTalentType, u32 PetFoodMask,
          u32 IconFile as its SpellIcon.dbc id, the ruRU name; u32 count + per ItemPetFood row u32 id, the ruRU
          name; PetPersonality row 1: u32 HappinessThreshold[3], f32 HappinessDamage[3]; u32 count + per
          StableSlotPrices row u32 id, u32 Cost
  "class_bars" (class bars: the shapeshift / totem / possess bars - include/platform/psp/psp_classbar_data.hpp):
          "WCBR", u32 version 1; u32 n + {u32 spell, i32 StanceBarOrder} of the shapeshift bar spells; u32 n +
          {u32 spell, u32 totem slot mask} of the totem spells; u32 n + {u32 spell, u32 flags} of the control
          spells; u32 n + {u32 form, u32 PresetSpellID[8]}; u32 n + {u32 id, u32 Spells[10]} of OverrideSpellData;
          u32 n + {u32 id, u32 type, u32 mask} of TotemCategory
  "class_vehicles" (class bars: the vehicles): "WCBV", u32 version 1; u32 n + Vehicle rows (64 B), u32 n +
          VehicleSeat rows (64 B), u32 n + VehicleUIIndSeat rows (16 B) - the fields psp_classbar_data.hpp names

  "glyph_data" (glyphs / guild bank: GlyphFrame - include/platform/psp/psp_glyph_bank_parse.hpp GlyphData):
          u32 count + per GlyphSlot row u32 id, u32 Type, u32 Tooltip; u32 count + per GlyphProperties row (by id)
          u32 id, u32 SpellID, u32 GlyphSlotFlags, u32 rune (N of UI-Glyph-Rune-N, 0 = UI-Glyph-Rune1 / another
          icon); u32 count + per Spell.dbc row with an APPLY_GLYPH (74) effect (by spell) u32 spell, u32 MiscValue
  "chat_chan" (glyphs / guild bank: the chat channels - ChatChannels.dbc): u32 count + per
          row u32 id, u32 Flags, u32 FactionGroup, the ruRU Name_lang and Shortcut_lang (NUL-terminated); the ruRU
          name of AreaTable 3459 (the word "City", NUL-terminated); u32 count + the AreaTable ids with flag 0x100 (capitals)
  "gb_icons" (glyphs / guild bank: the guild bank tab icons - GetMacroItemIconInfo's item icon names): u32 count
          + per icon (by name, lower case) u32 ItemDisplayInfo id (the first with it), u32 name offset; the names
          (NUL-terminated, as ItemDisplayInfo.InventoryIcon_1 spells them)

"pet_text", "class_bars", "class_vehicles", "glyph_data" and "chat_chan" are built by tools/psp/cbakers (one module
per entry, standard library only).

Usage: python build_text_pak.py --extracted <extracted>
Run with the normal Python, not the MSYS one.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_spells_pak import KIND_RAW, dbc_path, dbc_str, load_dbc  # noqa: E402
from build_ui_pak import write_pak  # noqa: E402
import cbakers  # noqa: E402

# ChrClasses.dbc 3.3.5a: 0 ID, 4 Name_lang[16] (+mask), 21 Name_female_lang[16]; ChrRaces.dbc: 14
# Name_lang[16], 31 Name_female_lang[16] - the ruRU column is chosen by pick_locale.
CLS_NAME, CLS_FEMALE = 4, 21
RACE_NAME, RACE_FEMALE = 14, 31
# AreaTable.dbc 3.3.5a: 0 ID, 11 AreaName_lang[16]; QuestSort.dbc: 0 ID, 1 SortName_lang[16].
AREA_NAME, SORT_NAME = 11, 1
# Character tabs. Faction.dbc 3.3.5a (57 fields): 0 ID, 1 ReputationListID, 18 ParentFactionID, 23 Name_lang[16]
# (+ mask), 40 Description_lang[16]. SkillLine.dbc (56): 0 ID, 1 CategoryID, 3 DisplayName_lang[16],
# 20 Description_lang[16]. SkillLineCategory.dbc (19): 0 ID, 1 Name_lang[16], 18 SortIndex (the last field).
FAC_REPLIST, FAC_PARENT, FAC_NAME, FAC_DESC = 1, 18, 23, 40
SKL_CAT, SKL_NAME, SKL_DESC = 1, 3, 20


def zones(extracted: str, loc: int) -> bytes:
    out = b""
    n = 0
    for file, col, sign in (("AreaTable.dbc", AREA_NAME, 1), ("QuestSort.dbc", SORT_NAME, -1)):
        rows, strs = load_dbc(dbc_path(extracted, file))
        for r in rows:
            name = dbc_str(strs, r[col + loc])
            if not name:
                continue
            out += struct.pack("<i", sign * int(r[0])) + name.encode() + b"\0"
            n += 1
    return struct.pack("<I", n) + out


def faction_text(extracted: str, loc: int) -> bytes:
    """Character tabs: "faction_text" - every Faction.dbc row (the headers of the reputation list have no
    standing of their own, and the PSP walks the parents like upstream's getReputationRows)."""
    rows, strs = load_dbc(dbc_path(extracted, "Faction.dbc"))
    out = b""
    for r in sorted(rows, key=lambda r: r[0]):
        out += struct.pack("<III", r[0], r[FAC_REPLIST], r[FAC_PARENT])
        out += dbc_str(strs, r[FAC_NAME + loc]).encode() + b"\0" + dbc_str(strs, r[FAC_DESC + loc]).encode() + b"\0"
    return struct.pack("<I", len(rows)) + out


def skill_text(extracted: str, loc: int) -> bytes:
    """Character tabs: "skill_text" - SkillLineCategory (the headings and their order) and SkillLine (names,
    categories, descriptions) as upstream's loadSkillLineDbc keeps them."""
    crow, cstr = load_dbc(dbc_path(extracted, "SkillLineCategory.dbc"))
    out = struct.pack("<I", len(crow))
    for r in sorted(crow, key=lambda r: r[0]):
        out += struct.pack("<II", r[0], r[-1]) + dbc_str(cstr, r[1 + loc]).encode() + b"\0"
    srow, sstr = load_dbc(dbc_path(extracted, "SkillLine.dbc"))
    out += struct.pack("<I", len(srow))
    for r in sorted(srow, key=lambda r: r[0]):
        out += struct.pack("<II", r[0], r[SKL_CAT])
        out += dbc_str(sstr, r[SKL_NAME + loc]).encode() + b"\0" + dbc_str(sstr, r[SKL_DESC + loc]).encode() + b"\0"
    return out


# ItemDisplayInfo.dbc 3.3.5a: 5 InventoryIcon_1.
IDI_ICON = 5


def _counts(blob: bytes, start: int, record_sizes: tuple[int, ...]) -> list[int]:
    """The record counts of consecutive "u32 n + n records" tables of a baked entry (for the summary lines)."""
    counts, off = [], start
    for size in record_sizes:
        n = struct.unpack_from("<I", blob, off)[0]
        counts.append(n)
        off += 4 + n * size
    return counts


def gb_icons(extracted: str) -> bytes:
    """Glyphs / guild bank: "gb_icons" - the item icon names (the guild bank tab icons) and a display id that shows
    each (itemicons.pak is by display id)."""
    rows, strs = load_dbc(dbc_path(extracted, "ItemDisplayInfo.dbc"))
    first = {}
    for r in sorted(rows):
        name = dbc_str(strs, r[IDI_ICON])
        if name and name.lower() not in first:
            first[name.lower()] = (r[0], name)
    keys = sorted(first)
    names = b""
    table = b""
    for k in keys:
        did, name = first[k]
        table += struct.pack("<II", did, len(names))
        names += name.encode() + b"\0"
    return struct.pack("<I", len(keys)) + table + names


def build(extracted: str, out: str) -> int:
    crow, cstr = load_dbc(dbc_path(extracted, "ChrClasses.dbc"))
    rrow, rstr = load_dbc(dbc_path(extracted, "ChrRaces.dbc"))
    # The localized column (enUS 0 .. ruRU 8): the one with names.
    loc = max(range(16), key=lambda k: sum(1 for r in crow if r[CLS_NAME + k] != 0))
    names = b""
    wanted: set[str] = set()
    for rows, strs, a, b, count in ((crow, cstr, CLS_NAME, CLS_FEMALE, 11), (rrow, rstr, RACE_NAME, RACE_FEMALE, 12)):
        by_id = {r[0]: r for r in rows}
        names += struct.pack("<I", count)
        for i in range(count):
            r = by_id.get(i)
            m = dbc_str(strs, r[a + loc]) if r else ""
            f = dbc_str(strs, r[b + loc]) if r else ""
            names += m.encode() + b"\0" + f.encode() + b"\0"
            for n in (m, f):
                if n:
                    wanted.add(n.lower())
    w, ws = load_dbc(dbc_path(extracted, "DeclinedWord.dbc"))
    c, cs = load_dbc(dbc_path(extracted, "DeclinedWordCases.dbc"))
    cases: dict[int, dict[int, str]] = {}
    for r in c:
        cases.setdefault(r[1], {})[r[2]] = dbc_str(cs, r[3])
    decl = b""
    n = 0
    seen: set[str] = set()
    for r in w:
        word = dbc_str(ws, r[1])
        if word.lower() not in wanted or word in seen:
            continue
        seen.add(word)
        cs_ = cases.get(r[0], {})
        decl += word.encode() + b"\0" + b"".join(cs_.get(k, "").encode() + b"\0" for k in range(1, 9))
        n += 1
    z = zones(extracted, loc)
    ft = faction_text(extracted, loc)  # character tabs
    st = skill_text(extracted, loc)    # character tabs
    pt = cbakers.build_pet_text(extracted, loc, warn=lambda m: print(f"  {m}"))  # pets
    cb = cbakers.build_class_bars(extracted)      # class bars
    stance, totem, possess, forms, overrides, cats = _counts(cb, 8, (8, 8, 8, 36, 44, 12))
    print(f"  class_bars: {stance} shapeshift, {totem} totem, {possess} possession spells, {forms} "
          f"preset forms, {overrides} override sets, {cats} totem categories, {len(cb)} B")
    cv = cbakers.build_class_vehicles(extracted)  # class bars
    vehicles, seats, ind = _counts(cv, 8, (64, 64, 16))
    print(f"  class_vehicles: {vehicles} vehicles, {seats} seats, {ind} indicator seats, {len(cv)} B")
    gd = cbakers.build_glyph_data(extracted)      # glyphs / guild bank
    cc = cbakers.build_chat_chan(extracted, loc)  # glyphs / guild bank
    gi = gb_icons(extracted)           # glyphs / guild bank
    write_pak(out, [("decl", KIND_RAW, struct.pack("<I", n) + decl), ("names", KIND_RAW, names),
                    ("zones", KIND_RAW, z), ("faction_text", KIND_RAW, ft), ("skill_text", KIND_RAW, st),
                    ("pet_text", KIND_RAW, pt),  # pets
                    ("class_bars", KIND_RAW, cb), ("class_vehicles", KIND_RAW, cv),  # class bars
                    ("glyph_data", KIND_RAW, gd), ("chat_chan", KIND_RAW, cc), ("gb_icons", KIND_RAW, gi)])
    print(f"  zones: {struct.unpack_from('<I', z)[0]} names, {len(z) // 1024 + 1} KB")
    print(f"  faction_text: {struct.unpack_from('<I', ft)[0]} factions, {len(ft)} B")
    print(f"  skill_text: {struct.unpack_from('<I', st)[0]} categories, {len(st)} B")
    print(f"  pet_text: {struct.unpack_from('<I', pt, 8)[0]} families, {len(pt)} B")  # pets
    print(f"  glyph_data: {len(gd)} B, chat_chan: {struct.unpack_from('<I', cc)[0]} channels {len(cc)} B, "
          f"gb_icons: {struct.unpack_from('<I', gi)[0]} icons {len(gi) // 1024 + 1} KB")  # glyphs / guild bank
    print(f"{out}: words={n} classes/races named, locale column={loc}, kb={os.path.getsize(out) // 1024 + 1}")
    return 0


def main() -> int:
    repo = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracted", required=True, help="extracted client root (dbfilesclient/)")
    ap.add_argument("--out", default=os.path.join(repo, "psp", "bin", "data", "text.pak"))
    a = ap.parse_args()
    return build(a.extracted, a.out)


if __name__ == "__main__":
    sys.exit(main())
