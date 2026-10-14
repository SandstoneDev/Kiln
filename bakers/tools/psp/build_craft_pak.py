#!/usr/bin/env python3
"""Builds psp/bin/data/craft.pak - what the trade skill window (TradeSkillFrame, the client's TradeSkillUI addon)
reads about the recipes of a profession, from the player's own extracted 3.3.5a client (never from
this repo, never from a server database).

Upstream WoWee takes the recipe data from its DBC caches at run time
(SpellHandler::loadSpellNameCache: Reagent0..7 / ReagentCount0..7, the CREATE_ITEM effect's
EffectItemType; loadSkillLineAbilityDbc: SkillLine, TrivialSkillLineRankHigh / Low) and the crafted item's
subclass from the item query. The PSP cannot keep Spell.dbc / SkillLineAbility.dbc / Item.dbc in memory,
so the profession part of them is baked here, one entry per skill line, read when that profession's
window opens and freed when it closes. Names stay where they are: the recipe names in spells.pak
(SpellDb), the item names come from the server (CMSG_ITEM_QUERY_SINGLE, as the client).

  "chdr"   magic "WCRF", u32 version 1, u32 lineCount, u32 0; per line u32 SkillLine id, u32 recipes
  "cl_<SkillLine id>"  one profession (SkillLine category 9 secondary / 11 profession, as upstream's
           tradeskillOpenerSkillLine), layouts = include/platform/psp/psp_craft_db.hpp:
     CraftLineHeader (40 B): "WCRL", u16 version 1, u16 skillLine, u16 recipes, reagents, items, groups,
           slots, u16 0, u32 string offset / size (from the entry start), u32 AlternateVerb (string
           offset, 0 = none), u32 0, u32 0
     CraftRecipeRec (28 B) x recipes, by spell id: the recipe spells of the line that create an item or
           take reagents (upstream getCraftingRecipes' rule) - u32 spell, u32 created item (0 = none),
           u32 description (string offset, $-tokens resolved here), u16 TrivialSkillLineRankHigh / Low,
           u16 first reagent, u16 made min / max (the CREATE_ITEM effect: BasePoints + 1 .. + DieSides),
           u8 reagent count, u8 group (0xFF = no created item), u8 the item's InventoryType, u8 0, u16 0
     CraftReagentRec (8 B): u32 item, u32 count
     CraftItemRec (8 B) x items, by id: u32 item, u32 ItemDisplayInfo id (Item.dbc) - the created items
           and the reagents (their icons: itemicons.pak)
     CraftGroupRec (8 B) x groups: u8 ItemClass, u8 ItemSubClass, u16 0, u32 name - the crafted item's
           subclass (ItemSubClass.dbc VerboseName, else DisplayName, ruRU): the list's headings and the
           TradeSkillSubClassDropDown
     CraftSlotRec (8 B) x slots: u8 InventoryType, 3 x u8 0, u32 name - TradeSkillInvSlotDropDown's
           labels: upstream's kAuctionSlots (include/game/auction_filters.hpp) token per InventoryType,
           the token's GlobalStrings.lua text (ruRU)
     strings: UTF-8, NUL-terminated, offset 0 = ""

Usage: python build_craft_pak.py --extracted <extracted> [--out ...] [--dump LINE]
Needs DBFilesClient (Spell, SkillLine, SkillLineAbility, Item, ItemSubClass, SpellDuration) and
Interface/FrameXML/GlobalStrings.lua. Run with the normal Python, not the MSYS one.
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_spells_pak import KIND_RAW, as_int, dbc_path, dbc_str, load_dbc, pick_locale  # noqa: E402
from build_ui_pak import CHARSET, write_pak  # noqa: E402

# Spell.dbc 3.3.5a (234 fields; AzerothCore SpellEntry / upstream dbc_layouts.json).
F_ID, F_DURATION, F_REAGENT, F_REAGENT_N = 0, 40, 52, 60
F_EFFECT, F_DIESIDES, F_BASEPOINTS, F_AMPLITUDE, F_ITEMTYPE = 71, 74, 80, 98, 107
F_DESC = 170  # Description_lang[16] (+ the locale column)
EFFECT_CREATE_ITEM, EFFECT_CREATE_ITEM_2 = 24, 114  # upstream: "CREATE_ITEM, CREATE_ITEM_2"
# SkillLineAbility.dbc: 1 SkillLine, 2 Spell, 10 TrivialSkillLineRankHigh, 11 TrivialSkillLineRankLow.
SLA_LINE, SLA_SPELL, SLA_TRIV_HIGH, SLA_TRIV_LOW = 1, 2, 10, 11
# SkillLine.dbc (56): 0 ID, 1 CategoryID, 38 AlternateVerb_lang[16].
SL_CAT, SL_VERB = 1, 38
CRAFT_CATEGORIES = (9, 11)  # upstream tradeskillOpenerSkillLine: CAT_SECONDARY, CAT_PROFESSION
# Item.dbc (8): 0 ID, 1 ClassID, 2 SubclassID, 5 DisplayInfoID, 6 InventoryType.
IT_CLASS, IT_SUB, IT_DISPLAY, IT_INVTYPE = 1, 2, 5, 6
# ItemSubClass.dbc (44): 0 ClassID, 1 SubClassID, 10 DisplayName_lang[16], 27 VerboseName_lang[16].
ISC_DISPLAY, ISC_VERBOSE = 10, 27
# upstream kAuctionSlots (include/game/auction_filters.hpp): InventoryType -> GlobalStrings token.
AUCTION_SLOTS = [(1, "INVTYPE_HEAD"), (2, "INVTYPE_NECK"), (3, "INVTYPE_SHOULDER"), (5, "INVTYPE_CHEST"),
                 (6, "INVTYPE_WAIST"), (7, "INVTYPE_LEGS"), (8, "INVTYPE_FEET"), (9, "INVTYPE_WRIST"),
                 (10, "INVTYPE_HAND"), (11, "INVTYPE_FINGER"), (12, "INVTYPE_TRINKET"), (16, "INVTYPE_CLOAK"),
                 (13, "INVTYPE_WEAPON"), (17, "INVTYPE_2HWEAPON"), (21, "INVTYPE_WEAPONMAINHAND"),
                 (22, "INVTYPE_WEAPONOFFHAND"), (26, "INVTYPE_RANGED"), (14, "INVTYPE_SHIELD"),
                 (23, "INVTYPE_HOLDABLE"), (28, "INVTYPE_RELIC")]

HDR_FMT = "<4sHHHHHHHHIIIII"
REC_FMT = "<IIIHHHHHBBBBH"
assert struct.calcsize(HDR_FMT) == 40 and struct.calcsize(REC_FMT) == 28


def global_strings(extracted: str) -> dict[str, str]:
    path = os.path.join(extracted, "interface", "framexml", "globalstrings.lua")
    if not os.path.exists(path):
        raise FileNotFoundError("interface/framexml/globalstrings.lua missing - extract Interface/FrameXML/")
    gs = {}
    for k, v in re.findall(r'^([A-Z0-9_]+) = "(.*)";\s*$', open(path, encoding="utf-8", errors="replace").read(),
                           re.M):
        gs[k] = v.replace('\\"', '"')
    return gs


def build(extracted: str, out: str) -> int:
    sp_rows, sp_strs = load_dbc(dbc_path(extracted, "Spell.dbc"))
    loc = pick_locale(sp_rows, sp_strs)
    spell = {r[F_ID]: r for r in sp_rows}
    sla, _ = load_dbc(dbc_path(extracted, "SkillLineAbility.dbc"))
    sl_rows, sl_strs = load_dbc(dbc_path(extracted, "SkillLine.dbc"))
    item_rows, _ = load_dbc(dbc_path(extracted, "Item.dbc"))
    isc_rows, isc_strs = load_dbc(dbc_path(extracted, "ItemSubClass.dbc"))
    dur_rows, _ = load_dbc(dbc_path(extracted, "SpellDuration.dbc"))
    gs = global_strings(extracted)
    item = {r[0]: r for r in item_rows}
    dur_ms = {r[0]: as_int(r[1]) for r in dur_rows}
    line_cat = {r[0]: r[SL_CAT] for r in sl_rows}
    line_verb = {r[0]: dbc_str(sl_strs, r[SL_VERB + loc]) for r in sl_rows}
    sub_name = {}
    for r in isc_rows:
        verbose, display = dbc_str(isc_strs, r[ISC_VERBOSE + loc]), dbc_str(isc_strs, r[ISC_DISPLAY + loc])
        sub_name[(r[0], r[1])] = verbose or display
    slot_label = {inv: gs.get(tok, "") for inv, tok in AUCTION_SLOTS}

    def duration_text(sid: int) -> str:
        """$d: the spell's SpellDuration base, as the client's INT_SPELL_DURATION_* / SPELL_DURATION_*."""
        row = spell.get(sid)
        ms = dur_ms.get(row[F_DURATION], 0) if row else 0
        if ms <= 0:
            return ""
        sec = ms / 1000.0
        for unit, key in ((86400, "DAYS"), (3600, "HOURS"), (60, "MIN"), (1, "SEC")):
            if sec >= unit or unit == 1:
                v = sec / unit
                if abs(v - round(v)) < 1e-6:
                    return gs.get(f"INT_SPELL_DURATION_{key}", "%d").replace("%d", str(int(round(v))))
                return gs.get(f"SPELL_DURATION_{key}", "%.2f").replace("%.2f", f"{v:.2f}")
        return ""

    unresolved: dict[str, int] = {}

    def resolve(text: str, self_id: int) -> str:
        """The description's $-tokens with this client's numbers (upstream formatSpellDescription's subset the
        profession descriptions use: $s / $m base points, $d duration, $o periodic total, $<id> another spell)."""
        def sub(m: re.Match) -> str:
            sid = int(m.group(1)) if m.group(1) else self_id
            kind, idx = m.group(2), int(m.group(3)) if m.group(3) else 1
            row = spell.get(sid)
            if row is None or not 1 <= idx <= 3:
                unresolved[m.group(0)] = unresolved.get(m.group(0), 0) + 1
                return ""
            bp, ds = as_int(row[F_BASEPOINTS + idx - 1]), as_int(row[F_DIESIDES + idx - 1])
            if kind in "sSmM":
                lo, hi = abs(bp + 1), abs(bp + ds)
                return str(lo) if ds <= 1 or hi == lo else f"{lo} - {hi}"
            if kind in "dD":
                return duration_text(sid)
            if kind in "oO":
                amp = row[F_AMPLITUDE + idx - 1]
                ms = dur_ms.get(row[F_DURATION], 0)
                return str(abs(bp + 1) * (ms // amp)) if amp > 0 and ms > 0 else str(abs(bp + 1))
            unresolved[m.group(0)] = unresolved.get(m.group(0), 0) + 1
            return ""
        return re.sub(r"\$(\d*)([a-zA-Z])(\d?)", sub, text)

    # The recipes of each profession line: SkillLineAbility rows of a category 9 / 11 line whose spell
    # creates an item or takes reagents (upstream: "The window-opener spell and passive skill ranks are
    # not recipes"). One record per spell (the Death Knight's duplicate First Aid rows share the ranks).
    lines: dict[int, dict[int, tuple]] = {}
    for r in sla:
        line, sid = r[SLA_LINE], r[SLA_SPELL]
        if line_cat.get(line) not in CRAFT_CATEGORIES or sid not in spell:
            continue
        s = spell[sid]
        reag = [(s[F_REAGENT + i], as_int(s[F_REAGENT_N + i])) for i in range(8)]
        reag = [(i, n) for i, n in reag if i != 0 and n > 0]
        made, made_min, made_max = 0, 1, 1
        for e in range(3):
            if s[F_EFFECT + e] in (EFFECT_CREATE_ITEM, EFFECT_CREATE_ITEM_2):
                made = s[F_ITEMTYPE + e]
                bp, ds = as_int(s[F_BASEPOINTS + e]), as_int(s[F_DIESIDES + e])
                made_min, made_max = max(1, bp + 1), max(1, bp + 1, bp + ds)
                break
        if not reag and made == 0:
            continue
        lines.setdefault(line, {}).setdefault(sid, (r, s, reag, made, made_min, made_max))

    entries = []
    chdr = b""
    total = {"recipes": 0, "reagents": 0, "items": 0, "desc": 0, "bytes": 0}
    missing_chars: dict[str, int] = {}
    charset = set(CHARSET) | {"\n"}
    for line in sorted(lines):
        recs = lines[line]
        strings = bytearray(b"\0")
        str_at: dict[str, int] = {"": 0}

        def s_off(t: str) -> int:
            if t not in str_at:
                str_at[t] = len(strings)
                strings.extend(t.encode("utf-8") + b"\0")
                for ch in t:
                    if ch not in charset:
                        missing_chars[ch] = missing_chars.get(ch, 0) + 1
            return str_at[t]

        groups: list[tuple[int, int]] = []
        slots: list[int] = []
        items: dict[int, int] = {}
        reagents = []
        rec_bytes = b""
        for sid in sorted(recs):
            r, s, reag, made, made_min, made_max = recs[sid]
            group, inv = 0xFF, 0
            if made:
                it = item.get(made)
                if it:
                    items[made] = it[IT_DISPLAY]
                    inv = it[IT_INVTYPE] & 0xFF
                    key = (it[IT_CLASS], it[IT_SUB])
                    if sub_name.get(key):
                        if key not in groups:
                            groups.append(key)
                        group = groups.index(key)
                    if inv in slot_label and slot_label[inv] and inv not in slots:
                        slots.append(inv)
                else:
                    items[made] = 0
            for i, _n in reag:
                items[i] = item[i][IT_DISPLAY] if i in item else 0
            desc = dbc_str(sp_strs, s[F_DESC + loc]).replace("\r\n", "\n")
            desc = resolve(desc, sid).strip() if desc else ""
            if desc:
                total["desc"] += 1
            rec_bytes += struct.pack(REC_FMT, sid, made, s_off(desc), min(0xFFFF, r[SLA_TRIV_HIGH]),
                                     min(0xFFFF, r[SLA_TRIV_LOW]), len(reagents), min(0xFFFF, made_min),
                                     min(0xFFFF, made_max), len(reag), group, inv, 0, 0)
            reagents += reag
        group_bytes = b"".join(struct.pack("<BBHI", c, sc, 0, s_off(sub_name[(c, sc)])) for c, sc in groups)
        slot_bytes = b"".join(struct.pack("<BBBBI", inv, 0, 0, 0, s_off(slot_label[inv])) for inv in slots)
        verb = s_off(line_verb.get(line, ""))
        reag_bytes = b"".join(struct.pack("<II", i, n) for i, n in reagents)
        item_bytes = b"".join(struct.pack("<II", i, items[i]) for i in sorted(items))
        body = rec_bytes + reag_bytes + item_bytes + group_bytes + slot_bytes
        str_off = 40 + len(body)
        hdr = struct.pack(HDR_FMT, b"WCRL", 1, line, len(recs), len(reagents), len(items), len(groups), len(slots),
                          0, str_off, len(strings), verb, 0, 0)
        blob = hdr + body + bytes(strings)
        entries.append((f"cl_{line}", KIND_RAW, blob))
        chdr += struct.pack("<II", line, len(recs))
        total["recipes"] += len(recs)
        total["reagents"] += len(reagents)
        total["items"] += len(items)
        total["bytes"] += len(blob)
        print(f"  cl_{line:<4d} recipes={len(recs):4d} reagents={len(reagents):5d} items={len(items):4d} "
              f"groups={len(groups):2d} slots={len(slots):2d} {len(blob):6d} B  verb={line_verb.get(line, '')!r}")
    entries.insert(0, ("chdr", KIND_RAW, b"WCRF" + struct.pack("<3I", 1, len(lines), 0) + chdr))
    write_pak(out, entries)
    print(f"wrote {out}: {len(lines)} lines, {total['recipes']} recipes, {total['reagents']} reagents, "
          f"{total['items']} item rows, {total['desc']} descriptions, {total['bytes']} B of line data, "
          f"{os.path.getsize(out)} B file (locale column {loc})")
    if unresolved:
        print(f"  unresolved description tokens (dropped): {unresolved}")
    if missing_chars:
        print(f"  characters not in build_ui_pak.CHARSET: {missing_chars}")
    return 0


def dump(path: str, line: int) -> int:
    """Lists one line of a baked craft.pak (self-check)."""
    with open(path, "rb") as f:
        data = f.read()
    count, toc = struct.unpack_from("<II", data, 8)
    for i in range(count):
        name, off, size, _kind, _ = struct.unpack_from("<48sIIII", data, toc + 64 * i)
        if name.split(b"\0", 1)[0].decode() != f"cl_{line}":
            continue
        blob = data[off:off + size]
        h = struct.unpack_from(HDR_FMT, blob, 0)
        _, _ver, sk, nrec, nreag, nitem, ngrp, nslot, _, soff, ssize, verb, _, _ = h
        strs = blob[soff:soff + ssize]

        def st(o: int) -> str:
            return strs[o:strs.index(b"\0", o)].decode()
        p = 40
        recs = [struct.unpack_from(REC_FMT, blob, p + 28 * k) for k in range(nrec)]
        p += 28 * nrec
        reag = [struct.unpack_from("<II", blob, p + 8 * k) for k in range(nreag)]
        p += 8 * nreag
        p += 8 * nitem
        grp = [struct.unpack_from("<BBHI", blob, p + 8 * k) for k in range(ngrp)]
        p += 8 * ngrp
        slots = [struct.unpack_from("<BBBBI", blob, p + 8 * k) for k in range(nslot)]
        print(f"cl_{sk}: {nrec} recipes, verb {st(verb)!r}, groups {[st(g[3]) for g in grp]}, "
              f"slots {[st(s[4]) for s in slots]}")
        for r in recs[:40]:
            sid, made, desc, hi, lo, first, mn, mx, nr, g, inv, _, _ = r
            print(f"  {sid:6d} made={made:6d} x{mn}-{mx} grey>={hi} green>={(hi + lo) // 2} yellow>={lo} "
                  f"group={st(grp[g][3]) if g != 0xFF else '-'} inv={inv} reagents={reag[first:first + nr]} "
                  f"{('desc: ' + st(desc)[:60]) if desc else ''}")
        return 0
    print(f"no cl_{line} in {path}")
    return 1


def main() -> int:
    repo = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracted", help="extracted client root (dbfilesclient/, interface/framexml/)")
    ap.add_argument("--out", default=os.path.join(repo, "psp", "bin", "data", "craft.pak"))
    ap.add_argument("--dump", type=int, metavar="SKILLLINE", help="list one line of an existing --out pak and exit")
    a = ap.parse_args()
    if a.dump is not None:
        return dump(a.out, a.dump)
    if not a.extracted:
        ap.error("--extracted is required")
    return build(a.extracted, a.out)


if __name__ == "__main__":
    sys.exit(main())
