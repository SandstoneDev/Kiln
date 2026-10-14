#!/usr/bin/env python3
"""Builds psp/bin/data/spells.pak - the spell table and spell icons the PSP
client reads (the data upstream's SpellHandler takes from Spell.dbc and its
satellites at run time: loadSpellNameCache, getSpellData, getSpellIconPath) -
from the player's own extracted 3.3.5a client (never from this repo).

  1. Extract DBFilesClient\\ and Interface\\ with PNG sidecars (psp_mpq_extract
     or upstream asset_extract --emit-png).
  2. python tools/psp/build_spells_pak.py --extracted <extracted>
                                          [--out psp/bin/data/spells.pak] [--dump]

Layout (read by src/platform/psp/psp_spell_db.cpp, structs in
include/platform/psp/psp_spell_db.hpp), a WPAK with four raw entries:
  "shdr"  SpellPakHeader, bucket directory, icon id -> slot table
  "sdata" buckets of 256 spell ids: u32 count, SpellRec[count] (by id), strings
  "icons" fixed-stride WTEX blobs (32x32 T8), slot n at n * iconStride
  "sforms" 64 bytes: SpellShapeshiftForm.BonusActionBar by form id
          (+ the action bar: 64 x u16 SpellShapeshiftForm.Flags by form id)
  "cfail" SMSG_CAST_FAILED texts: 256 u32 offsets + UTF-8 strings - the
          client's own SPELL_FAILED_* lines from GlobalStrings.lua (ruRU),
          upstream's English (getSpellCastResultString) where none matches
  "skills" SkillLine.dbc: id, name, category, SpellIcon id - the
          spell book tabs and trainer tabs (upstream loadSkillLineDbc)
  "talents" TalentTab.dbc + Talent.dbc (upstream loadTalentDbc);
          layouts in include/platform/psp/psp_talent_format.hpp. Optional: a
          pak without them stays readable, the talent window asks for a rebake.
  "suse" (the action bar) what ActionButton_UpdateUsable needs beyond the
          record: ManaCostPercentage, Stances / StancesNot, the four aura
          states, the reagents, ATTR2_NOT_NEED_SHAPESHIFT - only the spells
          that have any (header, a bucket directory read on demand, records
          sorted by id + their reagents; layout: SpellUseRec in psp_spell_db.hpp).
          Optional: without it the PSP checks the flat cost only (upstream).
  tooltips (GameTooltip: spells and items as the ruRU client draws them), all in the "WBBT" bucketed
  blob table format below (read record by record on demand, nothing stays in RAM):
  "stip"   per spell: the description (ruRU), SpellDescriptionVariables text, the effect values of
           the $-tokens ($s $m $M $o $t $a $x $q $b $e $f), duration / proc / stack / range / level /
           rune / equipped item / totem fields (the presence-mask record of SpellTipRec below)
  "tform"  SpellShapeshiftForm id -> name (SPELL_REQUIRED_FORM)
  "ttotem" TotemCategory id -> name (SPELL_TOTEMS)
  "tsub"   ItemSubClass (class << 8 | subclass) -> u16 DisplayFlags, u16 0, name, verbose name
  "tsubm"  ItemSubClassMask (class << 24 | row) -> u32 mask, name (SPELL_EQUIPPED_ITEM)
  "tench"  SpellItemEnchantment id -> name (ITEM_SOCKET_BONUS, enchantments)
  "tgem"   GemProperties by its enchantment id -> u32 GemProperties id, u32 colour mask (a socketed gem
           is an enchantment id in the item; its colour decides ITEM_SOCKET_BONUS)
  "tset"   ItemSet id -> 17 items, 8 spells, 8 thresholds (u32), required skill, rank, name
  "tlimit" ItemLimitCategory id -> u32 count, u32 flags, name (ITEM_LIMIT_CATEGORY)
  WBBT: "WBBT" u32 version 1, bucketShift, bucketCount, recordCount, 0 (24 bytes); bucketCount x
  (u32 offset from the entry start, u32 n); a bucket = n x u32 ids (sorted), (n + 1) x u32 offsets
  (from the bucket start, the last = its size), then the records.
The PSP keeps only the header in memory and reads a bucket or an icon on
demand (one short-lived descriptor: the card's open-file limit).
Needs Pillow + numpy.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_texconv as tc  # noqa: E402
from build_ui_pak import write_pak  # noqa: E402

KIND_RAW = 3
BUCKET_SHIFT = 8
ICON_PX = 32
REC_FMT = "<IIIIIIiffIIIIIIIHBBH3BxHHH"
REC_SIZE = struct.calcsize(REC_FMT)
assert REC_SIZE == 80, REC_SIZE

# Spell.dbc 3.3.5a (234 fields) - upstream Data/expansions/wotlk/dbc_layouts.json
F_ID, F_DISPEL, F_ATTR, F_ATTR_EX, F_TARGETS = 0, 2, 4, 5, 16
F_CAST_TIME, F_RECOVERY, F_CAT_RECOVERY, F_DURATION = 28, 29, 30, 40
F_POWER, F_MANA, F_RANGE = 41, 42, 46
F_EFFECT, F_IMPLICIT_A, F_AURA = 71, 86, 95
F_VISUAL, F_ICON, F_NAME, F_RANK, F_SCHOOL = 131, 133, 136, 153, 225
# Action bar: "suse" (ActionButton_UpdateUsable). Spell.dbc 3.3.5a: AttributesEx2 6, Stances 12,
# StancesNot 14, CasterAuraState 20, TargetAuraState 21, CasterAuraStateNot 22, TargetAuraStateNot 23,
# Reagent[8] 52, ReagentCount[8] 60, ManaCostPercentage 204 (TrinityCore / AzerothCore SpellEntry).
F_ATTR_EX2, F_STANCES, F_STANCES_NOT, F_AURA_STATE, F_REAGENT, F_REAGENT_N, F_MANA_PCT = 6, 12, 14, 20, 52, 60, 204
USE_REC_FMT = "<IIIBBBBBBBBHH"  # id, stances, stancesNot, manaPct, auraState[4], flags, nReagents, pad, firstReagent, pad
USE_REC_SIZE = struct.calcsize(USE_REC_FMT)
assert USE_REC_SIZE == 24, USE_REC_SIZE
USE_NOT_NEED_SHAPESHIFT = 1  # flags bit 0: SPELL_ATTR2_NOT_NEED_SHAPESHIFT (AttributesEx2 0x80000)


def load_dbc(path: str):
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"WDBC":
        raise ValueError(f"{path}: not a WDBC")
    n, fields, rsize, ssize = struct.unpack_from("<4I", d, 4)
    recs = d[20:20 + n * rsize]
    strs = d[20 + n * rsize:20 + n * rsize + ssize]
    rows = [struct.unpack_from(f"<{fields}I", recs, i * rsize) for i in range(n)]
    return rows, strs


def dbc_str(strs: bytes, off: int) -> str:
    if off <= 0 or off >= len(strs):
        return ""
    end = strs.index(b"\0", off)
    return strs[off:end].decode("utf-8", "replace")


def as_float(u: int) -> float:
    return struct.unpack("<f", struct.pack("<I", u))[0]


def as_int(u: int) -> int:
    return struct.unpack("<i", struct.pack("<I", u))[0]


def dbc_path(root: str, name: str) -> str:
    for cand in (f"dbfilesclient/{name.lower()}", f"DBFilesClient/{name}"):
        p = os.path.join(root, cand)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"{name} not under {root}/dbfilesclient - extract DBFilesClient first")


def pick_locale(rows, strs) -> int:
    """The localized string column (enUS 0 .. ruRU 8): the one with names."""
    best, best_n = 0, -1
    for k in range(16):
        n = sum(1 for r in rows[:4000] if r[F_NAME + k] != 0)
        if n > best_n:
            best, best_n = k, n
    return best


# SpellCastResult codes whose English (upstream) does not spell the
# GlobalStrings key: the AzerothCore SharedDefines names.
CAST_FAIL_ALIASES = {
    2: ["SPELL_FAILED_ALREADY_AT_FULL_HEALTH"], 3: ["SPELL_FAILED_ALREADY_AT_FULL_MANA"],
    4: ["SPELL_FAILED_ALREADY_AT_FULL_POWER"], 10: ["SPELL_FAILED_AUTOTRACK_INTERRUPTED"],
    12: ["SPELL_FAILED_BAD_TARGETS"], 27: ["SPELL_FAILED_CANT_DO_THAT_RIGHT_NOW", "ERR_GENERIC_THROTTLE"],
    28: ["SPELL_FAILED_EQUIPPED_ITEM"], 35: ["SPELL_FAILED_FOOD_LOWLEVEL"], 36: ["SPELL_FAILED_HIGHLEVEL"],
    37: ["SPELL_FAILED_HUNGER_SATIATED"], 47: ["SPELL_FAILED_LINE_OF_SIGHT"], 48: ["SPELL_FAILED_LOWLEVEL"],
    49: ["SPELL_FAILED_LOW_CASTLEVEL"], 51: ["SPELL_FAILED_MOVING"], 56: ["SPELL_FAILED_NOPATH"],
    61: ["SPELL_FAILED_NOT_INFRONT"], 68: ["SPELL_FAILED_NOT_SHAPESHIFT"], 78: ["SPELL_FAILED_NO_COMBO_POINTS"],
    83: ["SPELL_FAILED_NO_MOUNTS_ALLOWED"], 85: ["SPELL_FAILED_NO_POWER", "ERR_OUT_OF_MANA"],
    88: ["SPELL_FAILED_ONLY_ABOVEWATER"], 94: ["SPELL_FAILED_ONLY_SHAPESHIFT"], 100: ["SPELL_FAILED_REAGENTS"],
    103: ["SPELL_FAILED_ROOTED"], 104: ["SPELL_FAILED_SILENCED"], 113: ["SPELL_FAILED_TARGET_ENEMY"],
    127: ["SPELL_FAILED_THIRST_SATIATED"], 134: ["SPELL_FAILED_UNIT_NOT_INFRONT"], 140: ["SPELL_FAILED_TARGET_FFA"],
    148: ["SPELL_FAILED_PLAY_TIME"], 149: ["SPELL_FAILED_REPUTATION"], 150: ["SPELL_FAILED_MIN_SKILL"],
    159: ["SPELL_FAILED_BM_OR_INVISGOD"], 160: ["SPELL_FAILED_EXPERT_RIDING_REQUIREMENT"],
    161: ["SPELL_FAILED_ARTISAN_RIDING_REQUIREMENT"], 170: ["SPELL_FAILED_ON_USE_ENCHANT"],
    172: ["SPELL_FAILED_CUSTOM_ERROR"], 174: ["SPELL_FAILED_TOO_MANY_SOCKETS"], 178: ["SPELL_FAILED_NO_VALID_TARGETS"],
    184: ["SPELL_FAILED_MAX_SOCKETS"], 187: ["SPELL_FAILED_UNKNOWN"],
}


def cast_fail_texts(repo: str, extracted: str) -> tuple[list[str], int]:
    """SpellCastResult -> text. Upstream's English table names the codes
    (AzerothCore SharedDefines order); the key SPELL_FAILED_<NAME> is derived
    from the English and looked up in the client's GlobalStrings.lua."""
    src = open(os.path.join(repo, "include", "game", "spell_defines.hpp"), encoding="utf-8").read()
    body = src[src.index("getSpellCastResultString"):]
    body = body[:body.index("        default:  return nullptr;")]
    # Top-level cases only (8 spaces): NO_POWER's nested per-power switch is skipped.
    english = {int(c): t for c, t in re.findall(r'^        case (\d+):\s+return "([^"]*)";', body, re.M)}
    english.setdefault(85, "Not enough mana")
    gs = {}
    gpath = os.path.join(extracted, "interface", "framexml", "globalstrings.lua")
    if os.path.exists(gpath):
        for k, v in re.findall(r'^((?:SPELL_FAILED|ERR)_[A-Z0-9_]+) = "(.*)";\s*$',
                               open(gpath, encoding="utf-8", errors="replace").read(), re.M):
            gs[k] = v.replace('\\"', '"')
    out, matched = [""] * 256, 0
    for code, text in english.items():
        keys = ["SPELL_FAILED_" + re.sub(r"[^A-Z0-9]+", "_", text.upper().replace("'", "")).strip("_")]
        keys += CAST_FAIL_ALIASES.get(code, [])
        hit = next((k for k in keys if k in gs), None)
        if hit:
            out[code] = gs[hit]
            matched += 1
        else:
            out[code] = text
    return out, matched


def build(extracted: str, repo: str, prereq_cols: tuple = (13, 16)):
    spell_rows, spell_strs = load_dbc(dbc_path(extracted, "Spell.dbc"))
    loc = pick_locale(spell_rows, spell_strs)
    cast_rows, _ = load_dbc(dbc_path(extracted, "SpellCastTimes.dbc"))
    range_rows, _ = load_dbc(dbc_path(extracted, "SpellRange.dbc"))
    dur_rows, _ = load_dbc(dbc_path(extracted, "SpellDuration.dbc"))
    icon_rows, icon_strs = load_dbc(dbc_path(extracted, "SpellIcon.dbc"))
    sla_rows, _ = load_dbc(dbc_path(extracted, "SkillLineAbility.dbc"))
    form_rows, _ = load_dbc(dbc_path(extracted, "SpellShapeshiftForm.dbc"))
    # SpellShapeshiftForm.BonusActionBar (col 1) by form id: GetBonusBarOffset.
    forms = bytearray(64)
    form_flags = [0] * 64  # action bar: SpellShapeshiftForm.Flags (col 19) - SpellInfo::CheckShapeshift
    for r in form_rows:
        if r[0] < 64:
            forms[r[0]] = min(255, r[1])
            form_flags[r[0]] = r[19] & 0xFFFF if len(r) > 19 else 0
    forms += struct.pack("<64H", *form_flags)
    cast_ms = {r[0]: as_int(r[1]) for r in cast_rows}
    ranges = {r[0]: (as_float(r[2]), as_float(r[4])) for r in range_rows}  # upstream: MinRange=2, MaxRange=4
    dur_ms = {r[0]: as_int(r[1]) for r in dur_rows}
    skill_of = {}
    for r in sla_rows:  # SkillLineAbility: 1 = SkillLine, 2 = Spell
        # the last row wins, as upstream loadSkillLineAbilityDbc
        # (spellToSkillLine_[spell] = line) - the spell book / trainer tabs.
        if r[2] > 0 and r[1] > 0:
            skill_of[r[2]] = r[1]

    # Icons: SpellIcon id -> slot, T8 32x32, fixed stride.
    icon_blobs, icon_slot = [], {}
    missing_icons = 0
    for r in icon_rows:
        rel = dbc_str(icon_strs, r[1]).replace("\\", "/").lower()
        png = os.path.join(extracted, rel + ".png") if rel else ""
        if not rel or not os.path.exists(png):
            missing_icons += 1
            continue
        img = Image.open(png).convert("RGBA").resize((ICON_PX, ICON_PX), Image.LANCZOS)
        icon_slot[r[0]] = len(icon_blobs)
        icon_blobs.append(tc.wtex_blob(tc.encode(img, "t8", linear=True)))
    stride = (max(len(b) for b in icon_blobs) + 63) // 64 * 64
    icons = b"".join(b + b"\0" * (stride - len(b)) for b in icon_blobs)
    icon_max = max(icon_slot) if icon_slot else 0
    slot_table = [0xFFFF] * (icon_max + 1)
    for iid, s in icon_slot.items():
        slot_table[iid] = s

    # Spell buckets.
    buckets: dict[int, list] = {}
    for r in spell_rows:
        buckets.setdefault(r[F_ID] >> BUCKET_SHIFT, []).append(r)
    bucket_count = (max(buckets) + 1) if buckets else 0
    sdata = bytearray()
    directory = []
    no_icon = 0
    for b in range(bucket_count):
        rows = sorted(buckets.get(b, []), key=lambda r: r[F_ID])
        if not rows:
            directory.append((0, 0, 0))
            continue
        strings = bytearray(b"\0")
        interned = {"": 0}

        def intern(s: str) -> int:
            if s not in interned:
                interned[s] = len(strings)
                strings.extend(s.encode("utf-8") + b"\0")
            return interned[s]

        recs = bytearray()
        for r in rows:
            mn, mx = ranges.get(r[F_RANGE], (0.0, 0.0))
            slot = icon_slot.get(r[F_ICON], 0xFFFF)
            if slot == 0xFFFF:
                no_icon += 1
            recs += struct.pack(
                REC_FMT, r[F_ID], intern(dbc_str(spell_strs, r[F_NAME + loc])),
                intern(dbc_str(spell_strs, r[F_RANK + loc])), max(0, cast_ms.get(r[F_CAST_TIME], 0)),
                r[F_RECOVERY], r[F_CAT_RECOVERY], dur_ms.get(r[F_DURATION], 0) if r[F_DURATION] else 0,
                mn, mx, r[F_ATTR], r[F_ATTR_EX], r[F_TARGETS], r[F_IMPLICIT_A], r[F_SCHOOL], r[F_MANA],
                r[F_VISUAL], slot, min(255, r[F_POWER]) if r[F_POWER] < 0x80000000 else 255,
                min(255, r[F_DISPEL]), min(0xFFFF, skill_of.get(r[F_ID], 0)),
                min(255, r[F_EFFECT]), min(255, r[F_EFFECT + 1]), min(255, r[F_EFFECT + 2]),
                min(0xFFFF, r[F_AURA]), min(0xFFFF, r[F_AURA + 1]), min(0xFFFF, r[F_AURA + 2]))
        blob = struct.pack("<I", len(rows)) + recs + strings
        blob += b"\0" * ((-len(blob)) % 4)
        directory.append((len(sdata), len(blob), len(rows)))
        sdata += blob

    header = b"WSPL" + struct.pack("<7I", 1, BUCKET_SHIFT, bucket_count, len(icon_blobs), stride, icon_max, REC_SIZE)
    shdr = header + b"".join(struct.pack("<3I", *d) for d in directory)
    shdr += struct.pack(f"<{len(slot_table)}H", *slot_table)

    texts, matched = cast_fail_texts(repo, extracted)
    cfail_strings = bytearray(b"\0")
    offs = []
    for t in texts:
        if not t:
            offs.append(0)
            continue
        offs.append(1024 + len(cfail_strings))
        cfail_strings.extend(t.encode("utf-8") + b"\0")
    cfail = struct.pack("<256I", *offs) + cfail_strings

    stats = dict(spells=len(spell_rows), locale=loc, buckets=bucket_count, icons=len(icon_blobs), stride=stride,
                 missing_icons=missing_icons, spells_without_icon=no_icon, cast_fail_ru=matched,
                 sdata_kb=len(sdata) // 1024, icons_kb=len(icons) // 1024)
    entries = [("shdr", KIND_RAW, bytes(shdr)), ("sdata", KIND_RAW, bytes(sdata)),
               ("icons", KIND_RAW, icons), ("cfail", KIND_RAW, cfail), ("sforms", KIND_RAW, bytes(forms))]
    # skill lines + talents
    general_icon = 0
    for r in icon_rows:
        if dbc_str(icon_strs, r[1]).replace("/", "\\").lower().endswith("\\inv_misc_book_09"):
            general_icon = r[0]
            break
    skills, n_skills = build_skills(extracted, loc, general_icon)
    talents, n_tabs, n_talents = build_talents(extracted, loc, prereq_cols)
    entries += [("skills", KIND_RAW, skills), ("talents", KIND_RAW, talents)]
    stats.update(skill_lines=n_skills, talent_tabs=n_tabs, talents=n_talents, general_icon=general_icon)
    use, n_use, n_reag = build_use(spell_rows, bucket_count)  # action bar usability
    entries += [("suse", KIND_RAW, use)]
    stats.update(use_spells=n_use, use_reagents=n_reag, use_kb=len(use) // 1024)
    tips, tip_stats = build_tooltips(extracted, spell_rows, spell_strs, loc)  # tooltips
    entries += tips
    stats.update(tip_stats)
    return entries, stats


def build_use(spell_rows, bucket_count: int) -> tuple[bytes, int, int]:
    """Action bar: "suse" - header "WSUS" u32 version 1, bucketShift, bucketCount, recSize, 0;
    bucketCount x (u32 offset from the entry start, u16 records, u16 reagents); per bucket the records
    (USE_REC_FMT, by id) then its reagents (u32 item | count << 24). Only spells with something to check."""
    buckets: dict[int, list] = {}
    n_use = n_reag = 0
    for r in spell_rows:
        reag = [(r[F_REAGENT + i], as_int(r[F_REAGENT_N + i])) for i in range(8)]
        reag = [(item, min(255, n)) for item, n in reag if 0 < item < (1 << 24) and n > 0]
        flags = USE_NOT_NEED_SHAPESHIFT if r[F_ATTR_EX2] & 0x80000 else 0
        states = [min(255, r[F_AURA_STATE + k]) for k in range(4)]
        pct = min(255, r[F_MANA_PCT])
        if not (pct or r[F_STANCES] or r[F_STANCES_NOT] or any(states) or reag or flags):
            continue
        buckets.setdefault(r[F_ID] >> BUCKET_SHIFT, []).append((r, pct, states, flags, reag))
    head = b"WSUS" + struct.pack("<5I", 1, BUCKET_SHIFT, bucket_count, USE_REC_SIZE, 0)
    body = bytearray()
    directory = []
    base = len(head) + 8 * bucket_count
    for b in range(bucket_count):
        rows = sorted(buckets.get(b, []), key=lambda x: x[0][F_ID])
        if not rows:
            directory.append((0, 0, 0))
            continue
        recs, reagents = bytearray(), []
        for r, pct, states, flags, reag in rows:
            recs += struct.pack(USE_REC_FMT, r[F_ID], r[F_STANCES], r[F_STANCES_NOT], pct, *states, flags,
                                len(reag), 0, len(reagents), 0)
            reagents += [item | (n << 24) for item, n in reag]
        directory.append((base + len(body), len(rows), len(reagents)))
        body += recs + struct.pack(f"<{len(reagents)}I", *reagents)
        n_use += len(rows)
        n_reag += len(reagents)
    out = head + b"".join(struct.pack("<IHH", *d) for d in directory) + bytes(body)
    return out, n_use, n_reag


# --- tooltips: GameTooltip data ------------------------------------------------------------------------
# Spell.dbc 3.3.5a columns (TrinityCore / AzerothCore SpellEntry) the tooltip reads beyond the record.
F_ATTR_EX3, F_ATTR_EX6, F_SPELL_FOCUS = 7, 10, 18
F_PROC_CHANCE, F_PROC_CHARGES, F_MAX_LEVEL, F_BASE_LEVEL, F_SPELL_LEVEL = 35, 36, 37, 38, 39
F_MANA_PER_LEVEL, F_MANA_PER_SECOND, F_STACK, F_TOTEM = 43, 44, 49, 50
F_EQ_CLASS, F_EQ_SUBMASK, F_EQ_INVMASK = 68, 69, 70
F_DIE, F_RPPL, F_BASE, F_RADIUS, F_AMPL, F_MULT, F_CHAIN = 74, 77, 80, 92, 98, 101, 104
F_MISC, F_MISC_B, F_TRIGGER, F_PPCP = 110, 113, 116, 119
F_DESC, F_MAX_TARGET_LEVEL, F_MAX_TARGETS, F_DMG_MULT = 170, 207, 212, 216
F_TOTEM_CAT, F_RUNE_COST, F_BONUS_MULT, F_DESC_VAR = 222, 226, 229, 232
# SpellTipRec scalar fields (u32 each, present when bit i of the mask is set; psp_spell_db.hpp SpellTip).
TIP_FIELDS = ["dur", "durPerLevel", "durMax", "procChance", "procCharges", "maxTargets", "stack",
              "maxTargetLevel", "maxLevel", "baseLevel", "spellLevel", "attrEx2", "attrEx3", "attrEx6",
              "manaPerSecond", "manaPerLevel", "runeCost", "eqClass", "eqSubMask", "eqInvMask", "totem0",
              "totem1", "totemCat0", "totemCat1", "spellFocus", "minRangeHostile", "maxRangeHostile",
              "rangeFlags"]
assert len(TIP_FIELDS) <= 29
TIP_EFF_BITS = 29  # bits 29..31: effect k present
# Effect fields (u32 each, present when bit j of the effect's u16 mask is set).
TIP_EFF_FIELDS = ["effectAura", "basePoints", "dieSides", "realPointsPerLevel", "pointsPerCombo", "amplitude",
                  "radius", "radiusMax", "chainTarget", "multipleValue", "miscValue", "triggerSpell",
                  "damageMultiplier", "bonusMultiplier", "miscValueB"]
BBT_SHIFT = 8


def bbt(records: dict, shift: int = BBT_SHIFT) -> bytes:
    """Tooltips: the WBBT bucketed blob table (id -> bytes), read record by record on the PSP."""
    buckets: dict[int, list] = {}
    for rid in sorted(records):
        buckets.setdefault(rid >> shift, []).append(rid)
    nb = (max(buckets) + 1) if buckets else 0
    head = b"WBBT" + struct.pack("<5I", 1, shift, nb, len(records), 0)
    base = len(head) + 8 * nb
    body = bytearray()
    directory = []
    for b in range(nb):
        ids = buckets.get(b, [])
        if not ids:
            directory.append((0, 0))
            continue
        offs, data = [], bytearray()
        for rid in ids:
            offs.append(len(data))
            data += records[rid]
        offs.append(len(data))
        blob = struct.pack(f"<{len(ids)}I", *ids) + struct.pack(f"<{len(offs)}I", *offs) + bytes(data)
        blob += b"\0" * ((-len(blob)) % 4)
        directory.append((base + len(body), len(ids)))
        body += blob
    return head + b"".join(struct.pack("<II", *d) for d in directory) + bytes(body)


def bbt_records(data: bytes, off: int, size: int):
    """Every (id, bytes) of a WBBT entry (--verify / --dump)."""
    magic, ver, shift, nb, count, _ = struct.unpack_from("<4s5I", data, off)
    if magic != b"WBBT" or ver != 1:
        raise ValueError(f"WBBT header bad: {magic} v{ver}")
    out = []
    for b in range(nb):
        boff, n = struct.unpack_from("<II", data, off + 24 + 8 * b)
        if n == 0:
            continue
        p = off + boff
        ids = struct.unpack_from(f"<{n}I", data, p)
        offs = struct.unpack_from(f"<{n + 1}I", data, p + 4 * n)
        dbase = p + 4 * n + 4 * (n + 1)
        if dbase + offs[-1] > off + size:
            raise ValueError(f"WBBT bucket {b} past the entry")
        for k in range(n):
            if ids[k] >> shift != b:
                raise ValueError(f"WBBT id {ids[k]} in bucket {b}")
            out.append((ids[k], data[dbase + offs[k]:dbase + offs[k + 1]]))
    if len(out) != count:
        raise ValueError(f"WBBT count {len(out)} != {count}")
    return shift, out


def spell_tip_record(r, strs, loc, durations, radii, ranges, runes, var_text) -> bytes:
    """One "stip" record: id, mask, desc / var lengths, the present scalars, the present effects, texts."""
    dur = durations.get(r[F_DURATION], (0, 0, 0)) if r[F_DURATION] else (0, 0, 0)
    rng = ranges.get(r[F_RANGE], (0, 0, 0))
    rune = runes.get(r[F_RUNE_COST], (0, 0, 0))
    vals = {
        "dur": dur[0], "durPerLevel": dur[1], "durMax": dur[2],
        "procChance": r[F_PROC_CHANCE], "procCharges": r[F_PROC_CHARGES], "maxTargets": r[F_MAX_TARGETS],
        "stack": r[F_STACK], "maxTargetLevel": r[F_MAX_TARGET_LEVEL], "maxLevel": r[F_MAX_LEVEL],
        "baseLevel": r[F_BASE_LEVEL], "spellLevel": r[F_SPELL_LEVEL], "attrEx2": r[F_ATTR_EX2],
        "attrEx3": r[F_ATTR_EX3], "attrEx6": r[F_ATTR_EX6], "manaPerSecond": r[F_MANA_PER_SECOND],
        "manaPerLevel": r[F_MANA_PER_LEVEL],
        "runeCost": min(255, rune[0]) | min(255, rune[1]) << 8 | min(255, rune[2]) << 16,
        # PSP: EquippedItemClass stored + 1 (0 = no requirement; -1 in the DBC, class 0 = consumable).
        "eqClass": (r[F_EQ_CLASS] + 1) & 0xFFFFFFFF,
        "eqSubMask": r[F_EQ_SUBMASK] if r[F_EQ_CLASS] != 0xFFFFFFFF else 0,
        "eqInvMask": r[F_EQ_INVMASK] if r[F_EQ_CLASS] != 0xFFFFFFFF else 0,
        "totem0": r[F_TOTEM], "totem1": r[F_TOTEM + 1], "totemCat0": r[F_TOTEM_CAT], "totemCat1": r[F_TOTEM_CAT + 1],
        "spellFocus": r[F_SPELL_FOCUS], "minRangeHostile": rng[0], "maxRangeHostile": rng[1], "rangeFlags": rng[2],
    }
    mask, fields = 0, []
    for i, name in enumerate(TIP_FIELDS):
        v = vals[name] & 0xFFFFFFFF
        if v:
            mask |= 1 << i
            fields.append(v)
    effs = b""
    for k in range(3):
        eff = r[F_EFFECT + k]
        if eff == 0:
            continue
        rad = radii.get(r[F_RADIUS + k], (0, 0))
        ev = [eff | (min(0xFFFF, r[F_AURA + k]) << 16), r[F_BASE + k], r[F_DIE + k], r[F_RPPL + k], r[F_PPCP + k],
              r[F_AMPL + k], rad[0], rad[1], r[F_CHAIN + k], r[F_MULT + k], r[F_MISC + k], r[F_TRIGGER + k],
              r[F_DMG_MULT + k] if r[F_DMG_MULT + k] != 0x3F800000 else 0,  # 1.0 = absent
              r[F_BONUS_MULT + k], r[F_MISC_B + k]]
        emask, efields = 0, []
        for j, v in enumerate(ev):
            if v & 0xFFFFFFFF:
                emask |= 1 << j
                efields.append(v & 0xFFFFFFFF)
        mask |= 1 << (TIP_EFF_BITS + k)
        effs += struct.pack("<HH", emask, 0) + struct.pack(f"<{len(efields)}I", *efields)
    desc = dbc_str(strs, r[F_DESC + loc]).encode("utf-8")
    var = var_text.get(r[F_DESC_VAR], "").encode("utf-8") if r[F_DESC_VAR] else b""
    rec = struct.pack("<IIHH", r[F_ID], mask, min(0xFFFF, len(desc)), min(0xFFFF, len(var)))
    rec += struct.pack(f"<{len(fields)}I", *fields) + effs + desc[:0xFFFF] + var[:0xFFFF]
    return rec


def build_tooltips(extracted: str, spell_rows, spell_strs, loc: int) -> tuple[list, dict]:
    """Tooltips: the GameTooltip records (module docstring)."""
    dur_rows, _ = load_dbc(dbc_path(extracted, "SpellDuration.dbc"))
    durations = {r[0]: (r[1], r[2], r[3]) for r in dur_rows}  # Duration, DurationPerLevel, MaxDuration
    rad_rows, _ = load_dbc(dbc_path(extracted, "SpellRadius.dbc"))
    radii = {r[0]: (r[1], r[3]) for r in rad_rows}  # Radius, RadiusMax (float bits)
    range_rows, _ = load_dbc(dbc_path(extracted, "SpellRange.dbc"))
    ranges = {r[0]: (r[1], r[3], r[5]) for r in range_rows}  # MinRangeHostile, MaxRangeHostile, Flags
    rune_rows, _ = load_dbc(dbc_path(extracted, "SpellRuneCost.dbc"))
    runes = {r[0]: (r[1], r[2], r[3]) for r in rune_rows}  # Blood, Unholy, Frost
    var_rows, var_strs = load_dbc(dbc_path(extracted, "SpellDescriptionVariables.dbc"))
    var_text = {r[0]: dbc_str(var_strs, r[1]) for r in var_rows}
    stip = {r[F_ID]: spell_tip_record(r, spell_strs, loc, durations, radii, ranges, runes, var_text)
            for r in spell_rows}
    n_desc = sum(1 for r in spell_rows if r[F_DESC + loc])

    def names(dbc: str, name_col: int, extra=None, key=lambda r: r[0]) -> dict:
        rows, strs = load_dbc(dbc_path(extracted, dbc))
        out = {}
        for r in rows:
            n = dbc_str(strs, r[name_col + loc]) or dbc_str(strs, r[name_col])
            head = extra(r, strs) if extra else b""
            if n or head:
                out[key(r)] = head + n.encode("utf-8")
        return out

    tform = names("SpellShapeshiftForm.dbc", 2)
    ttotem = names("TotemCategory.dbc", 1)
    sub_rows, sub_strs = load_dbc(dbc_path(extracted, "ItemSubClass.dbc"))
    tsub = {}
    for r in sub_rows:  # ClassID 0, SubClassID 1, DisplayFlags 5, DisplayName 10, VerboseName 27
        disp = dbc_str(sub_strs, r[10 + loc]) or dbc_str(sub_strs, r[10])
        verbose = dbc_str(sub_strs, r[27 + loc]) or dbc_str(sub_strs, r[27])
        tsub[(r[0] & 0xFF) << 8 | (r[1] & 0xFF)] = (struct.pack("<HH", r[5] & 0xFFFF, 0) + disp.encode("utf-8") +
                                                    b"\0" + verbose.encode("utf-8"))
    mask_rows, mask_strs = load_dbc(dbc_path(extracted, "ItemSubClassMask.dbc"))
    tsubm = {}
    for i, r in enumerate(mask_rows):  # ClassID 0, Mask 1, Name 2
        n = dbc_str(mask_strs, r[2 + loc]) or dbc_str(mask_strs, r[2])
        tsubm[(r[0] & 0xFF) << 24 | i] = struct.pack("<I", r[1]) + n.encode("utf-8")
    tench = names("SpellItemEnchantment.dbc", 14)
    gem_rows, _ = load_dbc(dbc_path(extracted, "GemProperties.dbc"))
    tgem = {}
    for r in gem_rows:  # ID 0, Enchant_Id 1, Type 4: keyed by the enchantment (the first gem of it wins)
        if r[0] and r[1]:
            tgem.setdefault(r[1], struct.pack("<II", r[0], r[4]))
    set_rows, set_strs = load_dbc(dbc_path(extracted, "ItemSet.dbc"))
    tset = {}
    for r in set_rows:  # ID 0, Name 1, ItemID[17] 18, SetSpellID[8] 35, SetThreshold[8] 43, skill 51, rank 52
        n = dbc_str(set_strs, r[1 + loc]) or dbc_str(set_strs, r[1])
        tset[r[0]] = struct.pack("<35I", *r[18:51], r[51], r[52]) + n.encode("utf-8")
    tlimit = names("ItemLimitCategory.dbc", 1, extra=lambda r, s: struct.pack("<II", r[18], r[19]))
    entries = [("stip", KIND_RAW, bbt(stip)), ("tform", KIND_RAW, bbt(tform)), ("ttotem", KIND_RAW, bbt(ttotem)),
               ("tsub", KIND_RAW, bbt(tsub)), ("tsubm", KIND_RAW, bbt(tsubm, shift=24)),
               ("tench", KIND_RAW, bbt(tench)), ("tgem", KIND_RAW, bbt(tgem)), ("tset", KIND_RAW, bbt(tset)),
               ("tlimit", KIND_RAW, bbt(tlimit))]
    stats = dict(tip_spells=len(stip), tip_desc=n_desc, tip_kb=len(entries[0][2]) // 1024, tip_forms=len(tform),
                 tip_totems=len(ttotem), tip_subclasses=len(tsub), tip_enchants=len(tench), tip_gems=len(tgem),
                 tip_sets=len(tset), tip_limits=len(tlimit),
                 tip_other_kb=sum(len(e[2]) for e in entries[1:]) // 1024)
    return entries, stats


def parse_spell_tip(rec: bytes) -> dict:
    """A "stip" record back to fields (--verify / --dump; the PSP reader is SpellDb::tip)."""
    sid, mask, dlen, vlen = struct.unpack_from("<IIHH", rec, 0)
    p, out = 12, {"id": sid}
    for i, name in enumerate(TIP_FIELDS):
        if mask & (1 << i):
            out[name] = struct.unpack_from("<I", rec, p)[0]
            p += 4
    out["effects"] = []
    for k in range(3):
        if not mask & (1 << (TIP_EFF_BITS + k)):
            continue
        emask = struct.unpack_from("<H", rec, p)[0]
        p += 4
        e = {"slot": k}
        for j, name in enumerate(TIP_EFF_FIELDS):
            if emask & (1 << j):
                e[name] = struct.unpack_from("<I", rec, p)[0]
                p += 4
        out["effects"].append(e)
    out["desc"] = rec[p:p + dlen].decode("utf-8")
    out["var"] = rec[p + dlen:p + dlen + vlen].decode("utf-8")
    if p + dlen + vlen != len(rec):
        raise ValueError(f"stip {sid}: {len(rec) - p - dlen - vlen} stray bytes")
    return out


def verify_tooltips(data: bytes, ent: dict, ids: set) -> int:
    """Tooltips: every table parses, every "stip" record decodes and belongs to a spell record."""
    errors = 0
    for name in ("stip", "tform", "ttotem", "tsub", "tsubm", "tench", "tgem", "tset", "tlimit"):
        if name not in ent:
            print(f"verify: no '{name}' entry - an older spells.pak (tooltips without descriptions / names)")
            return 1
        try:
            _, recs = bbt_records(data, *ent[name])
        except ValueError as e:
            print(f"verify: {name}: {e}")
            errors += 1
            continue
        bad = 0
        if name == "stip":
            for rid, rec in recs:
                try:
                    t = parse_spell_tip(rec)
                    bad += t["id"] != rid or rid not in ids
                except (ValueError, UnicodeDecodeError, struct.error):
                    bad += 1
            with_desc = sum(1 for _, rec in recs if struct.unpack_from("<H", rec, 8)[0])
            print(f"verify: stip records={len(recs)} with_desc={with_desc} bad={bad}")
        else:
            for _, rec in recs:
                try:
                    rec.decode("utf-8", "strict") if name in ("tform", "ttotem", "tench") else None
                except UnicodeDecodeError:
                    bad += 1
            print(f"verify: {name} records={len(recs)} bad={bad}")
        errors += bad
    return errors


# --- skill lines + talents --------------------------------------------------------
# Layouts: include/platform/psp/psp_talent_format.hpp.
SKILL_HDR_FMT = "<4s5I"
SKILL_REC_FMT = "<IIIHH"
TAL_HDR_FMT = "<4s5I"
TAB_REC_FMT = "<6IB3x"
TALENT_REC_FMT = "<IIBBBx5I3I3Bx"
assert struct.calcsize(SKILL_HDR_FMT) == 24 and struct.calcsize(SKILL_REC_FMT) == 16
assert struct.calcsize(TAL_HDR_FMT) == 24 and struct.calcsize(TAB_REC_FMT) == 28
assert struct.calcsize(TALENT_REC_FMT) == 48

# SkillLine.dbc 3.3.5a (56 fields; upstream dbc_layouts.json): ID 0, Category 1,
# Name 3 (+ locale), SpellIcon 37.
SL_ID, SL_CATEGORY, SL_NAME, SL_ICON = 0, 1, 3, 37
# TalentTab.dbc 3.3.5a (24 fields; upstream layout + SpellIconID 18, PetTalentMask 21).
TT_ID, TT_NAME, TT_ICON, TT_CLASS, TT_PET, TT_ORDER, TT_BG = 0, 1, 18, 20, 21, 22, 23
# Talent.dbc 3.3.5a (23 fields): ID 0, TabID 1, Row 2, Column 3, RankSpell 4..8
# (9..12 unused ranks), PrereqTalent 13..15, PrereqRank 16..18. PSP: upstream's
# layout says PrereqTalent0 = 9 / PrereqRank0 = 12 - the unused rank columns,
# always 0, so upstream never sees a prerequisite; --talent-prereq-cols 9,12
# reproduces it.
TA_ID, TA_TAB, TA_ROW, TA_COL, TA_RANK0 = 0, 1, 2, 3, 4


class Strings:
    def __init__(self):
        self.data = bytearray(b"\0")
        self.index = {"": 0}

    def add(self, s: str) -> int:
        if s not in self.index:
            self.index[s] = len(self.data)
            self.data.extend(s.encode("utf-8") + b"\0")
        return self.index[s]


def build_skills(extracted: str, loc: int, general_icon: int) -> tuple[bytes, int]:
    rows, strs = load_dbc(dbc_path(extracted, "SkillLine.dbc"))
    st = Strings()
    recs = []
    for r in sorted(rows, key=lambda r: r[SL_ID]):
        name = dbc_str(strs, r[SL_NAME + loc]) or dbc_str(strs, r[SL_NAME])
        if r[SL_ID] == 0 or not name:  # upstream: id > 0 and a name
            continue
        icon = r[SL_ICON] if len(r) > SL_ICON else 0
        recs.append(struct.pack(SKILL_REC_FMT, r[SL_ID], st.add(name), icon, min(0xFFFF, r[SL_CATEGORY]), 0))
    blob = struct.pack(SKILL_HDR_FMT, b"WSKL", 1, len(recs), struct.calcsize(SKILL_REC_FMT), general_icon, 0)
    blob += b"".join(recs) + bytes(st.data)
    blob += b"\0" * ((-len(blob)) % 4)
    return blob, len(recs)


def build_talents(extracted: str, loc: int, prereq_cols: tuple) -> tuple[bytes, int, int]:
    tab_rows, tab_strs = load_dbc(dbc_path(extracted, "TalentTab.dbc"))
    tal_rows, _ = load_dbc(dbc_path(extracted, "Talent.dbc"))
    pt, pr = prereq_cols
    st = Strings()
    tabs = []
    for r in sorted(tab_rows, key=lambda r: r[TT_ID]):
        if r[TT_ID] == 0:
            continue
        name = dbc_str(tab_strs, r[TT_NAME + loc]) or dbc_str(tab_strs, r[TT_NAME])
        tabs.append(struct.pack(TAB_REC_FMT, r[TT_ID], st.add(name), r[TT_ICON], r[TT_CLASS], r[TT_PET],
                                st.add(dbc_str(tab_strs, r[TT_BG])), min(255, r[TT_ORDER])))
    talents = []
    for r in sorted(tal_rows, key=lambda r: r[TA_ID]):
        if r[TA_ID] == 0:
            continue
        ranks = [r[TA_RANK0 + k] for k in range(5)]
        max_rank = max((k + 1 for k in range(5) if ranks[k] != 0), default=0)  # upstream: last non-zero rank
        talents.append(struct.pack(TALENT_REC_FMT, r[TA_ID], r[TA_TAB], min(255, r[TA_ROW]), min(255, r[TA_COL]),
                                   max_rank, *ranks, *(r[pt + k] for k in range(3)),
                                   *(min(255, r[pr + k]) for k in range(3))))
    blob = struct.pack(TAL_HDR_FMT, b"WTAL", 1, len(tabs), len(talents), struct.calcsize(TAB_REC_FMT),
                       struct.calcsize(TALENT_REC_FMT))
    blob += b"".join(tabs) + b"".join(talents) + bytes(st.data)
    blob += b"\0" * ((-len(blob)) % 4)
    return blob, len(tabs), len(talents)


def read_pak(path: str) -> tuple[bytes, dict]:
    with open(path, "rb") as f:
        data = f.read()
    count, toc = struct.unpack_from("<II", data, 8)
    ent = {}
    for i in range(count):
        name, off, size, kind, _ = struct.unpack_from("<48sIIII", data, toc + 64 * i)
        ent[name.split(b"\0", 1)[0].decode()] = (off, size)
    return data, ent


def _string_reader(data: bytes, base: int):
    def s(o: int) -> str:
        e = data.index(b"\0", base + o)
        return data[base + o:e].decode("utf-8")
    return s


def parse_talents(data: bytes, off: int, size: int):
    magic, ver, ntab, ntal, tabsz, talsz = struct.unpack_from(TAL_HDR_FMT, data, off)
    if magic != b"WTAL" or ver != 1 or tabsz != 28 or talsz != 48:
        raise ValueError(f"talents header bad: {magic} v{ver} {tabsz}/{talsz}")
    p = off + 24
    tabs = [struct.unpack_from(TAB_REC_FMT, data, p + 28 * i) for i in range(ntab)]
    p += 28 * ntab
    tals = [struct.unpack_from(TALENT_REC_FMT, data, p + 48 * i) for i in range(ntal)]
    sbase = p + 48 * ntal
    if sbase > off + size:
        raise ValueError("talents entry truncated")
    return tabs, tals, _string_reader(data, sbase)


def parse_skills(data: bytes, off: int, size: int):
    magic, ver, n, recsz, general_icon, _ = struct.unpack_from(SKILL_HDR_FMT, data, off)
    if magic != b"WSKL" or ver != 1 or recsz != 16:
        raise ValueError(f"skills header bad: {magic} v{ver} rec={recsz}")
    recs = [struct.unpack_from(SKILL_REC_FMT, data, off + 24 + 16 * i) for i in range(n)]
    sbase = off + 24 + 16 * n
    if sbase > off + size:
        raise ValueError("skills entry truncated")
    return recs, general_icon, _string_reader(data, sbase)


def spell_records(data: bytes, ent: dict):
    so, _ = ent["shdr"]
    _, shift, nb, _, _, _, rec = struct.unpack_from("<7I", data, so + 4)
    do, _ = ent["sdata"]
    for b in range(nb):
        boff, _, bn = struct.unpack_from("<3I", data, so + 32 + 12 * b)
        for i in range(bn):
            yield struct.unpack_from(REC_FMT, data, do + boff + 4 + i * rec)


def dump_talents(path: str, class_id: int) -> int:
    data, ent = read_pak(path)
    if "skills" in ent:
        recs, gi, s = parse_skills(data, *ent["skills"])
        cats = {}
        for r in recs:
            cats[r[3]] = cats.get(r[3], 0) + 1
        print(f"  skills: {len(recs)} lines, general_icon={gi}, by category {dict(sorted(cats.items()))}")
    else:
        print("  skills: missing (old spells.pak)")
    if "talents" not in ent:
        print("  talents: missing (old spells.pak)")
        return 0
    tabs, tals, s = parse_talents(data, *ent["talents"])
    print(f"  talents: {len(tabs)} tabs, {len(tals)} talents")
    if class_id <= 0:
        return 0
    mask = 1 << (class_id - 1)
    for t in sorted((t for t in tabs if t[3] & mask), key=lambda t: t[6]):
        mine = sorted((x for x in tals if x[1] == t[0]), key=lambda x: (x[2], x[3]))
        print(f"  tab {t[0]} '{s(t[1])}' order={t[6]} icon={t[2]} bg='{s(t[5])}' talents={len(mine)}")
        for x in mine:
            pre = [(x[10 + k], x[13 + k] + 1) for k in range(3) if x[10 + k]]
            print(f"    {x[0]:5d} r{x[2]} c{x[3]} max={x[4]} ranks={[v for v in x[5:10] if v]} prereq={pre}")
    return 0


def verify(path: str) -> int:
    """Self-check of a baked spells.pak's talents / skills (run it after
    the bake): headers, every talent's tab, rank spells and prereqs
    present, tiers / columns in range, skill lines the spells refer to."""
    data, ent = read_pak(path)
    errors = warnings = 0
    for need in ("shdr", "sdata"):
        if need not in ent:
            print(f"verify: no '{need}' entry")
            return 1
    if "talents" not in ent or "skills" not in ent:
        print("verify: no talents/skills entries - an old spells.pak (the talent window asks for a rebake)")
        return 1
    spells = list(spell_records(data, ent))
    ids = {r[0] for r in spells}
    if "suse" in ent:  # action bar: every usability record belongs to a spell record
        uo, _ = ent["suse"]
        magic, ver, shift, nb, rsz, _ = struct.unpack_from("<4s5I", data, uo)
        n_use = bad = 0
        for b in range(nb if magic == b"WSUS" and ver == 1 and rsz == USE_REC_SIZE else 0):
            off, cnt, _nr = struct.unpack_from("<IHH", data, uo + 24 + 8 * b)
            for k in range(cnt):
                uid = struct.unpack_from("<I", data, uo + off + k * rsz)[0]
                n_use += 1
                if uid not in ids or uid >> shift != b:
                    bad += 1
        print(f"verify: suse records={n_use} bad={bad}" + ("" if magic == b"WSUS" else " (bad header)"))
        errors += bad + (0 if magic == b"WSUS" and ver == 1 and rsz == USE_REC_SIZE else 1)
    else:
        print("verify: no 'suse' entry - an older spells.pak (the action bar checks the flat cost only)")
    errors += verify_tooltips(data, ent, ids)  # tooltips
    tabs, tals, s = parse_talents(data, *ent["talents"])
    tab_ids = {t[0] for t in tabs}
    tal_ids = {x[0] for x in tals}
    classes = set()
    for t in tabs:
        if not s(t[1]):
            print(f"  tab {t[0]}: no name")
            errors += 1
        classes.update(c for c in range(1, 12) if t[3] & (1 << (c - 1)))
    for x in tals:
        if x[1] not in tab_ids:
            print(f"  talent {x[0]}: tab {x[1]} missing")
            errors += 1
        if x[2] > 15 or x[3] > 3:
            print(f"  talent {x[0]}: row {x[2]} col {x[3]} out of the 4-column grid")
            errors += 1
        for k in range(x[4]):
            if x[5 + k] and x[5 + k] not in ids:
                print(f"  talent {x[0]}: rank {k + 1} spell {x[5 + k]} not in sdata")
                errors += 1
        for k in range(3):
            if x[10 + k] and x[10 + k] not in tal_ids:
                # The client's own Talent.dbc names two absent talents (1756 -> 1409, 1993 -> 1994, every locale);
                # the talent frames skip a prerequisite they cannot find, so this is a warning, not a failed bake.
                print(f"  talent {x[0]}: prereq {x[10 + k]} missing (skipped by the talent frames)")
                warnings += 1
    with_prereq = sum(1 for x in tals if x[10])
    recs, gi, _ = parse_skills(data, *ent["skills"])
    skill_ids = {r[0] for r in recs}
    unknown_lines = {r[19] for r in spells if r[19] and r[19] not in skill_ids}
    if unknown_lines:
        print(f"  {len(unknown_lines)} skill lines used by spells but not in 'skills' "
              f"(e.g. {sorted(unknown_lines)[:5]})")
        warnings += 1
    if with_prereq == 0 and tals:
        print("  note: no talent has a prerequisite - baked with the upstream columns (--talent-prereq-cols 9,12)?")
        warnings += 1
    print(f"talents_verify tabs={len(tabs)} talents={len(tals)} with_prereq={with_prereq} classes={len(classes)} "
          f"skills={len(recs)} general_icon={gi} errors={errors} warnings={warnings}")
    return 1 if errors else 0


def dump(path: str, ids: list[int]) -> int:
    with open(path, "rb") as f:
        data = f.read()
    count, toc = struct.unpack_from("<II", data, 8)
    ent = {}
    for i in range(count):
        name, off, size, kind, _ = struct.unpack_from("<48sIIII", data, toc + 64 * i)
        ent[name.split(b"\0", 1)[0].decode()] = (off, size)
    so, _ = ent["shdr"]
    magic = data[so:so + 4]
    ver, shift, nb, nicons, stride, icon_max, rec = struct.unpack_from("<7I", data, so + 4)
    print(f"{path}: {magic.decode()} v{ver} buckets={nb} icons={nicons} stride={stride} rec={rec}")
    do, _ = ent["sdata"]
    for sid in ids:
        b = sid >> shift
        if b >= nb:
            print(f"  {sid}: no bucket")
            continue
        boff, bsize, bn = struct.unpack_from("<3I", data, so + 32 + 12 * b)
        base = do + boff
        strings = base + 4 + bn * rec
        for i in range(bn):
            r = struct.unpack_from(REC_FMT, data, base + 4 + i * rec)
            if r[0] != sid:
                continue

            def s(o):
                e = data.index(b"\0", strings + o)
                return data[strings + o:e].decode("utf-8")
            print(f"  {sid}: '{s(r[1])}' '{s(r[2])}' cast={r[3]} rec={r[4]}/{r[5]} dur={r[6]} "
                  f"range={r[7]:.0f}-{r[8]:.0f} attr=0x{r[9]:X} implA={r[12]} school={r[13]} icon={r[16]} "
                  f"skill={r[19]} eff={r[20:23]} aura={r[23:26]}")
            break
        else:
            print(f"  {sid}: not found")
    if "stip" in ent:  # tooltips: the tooltip records of the same ids
        _, recs = bbt_records(data, *ent["stip"])
        tips = dict(recs)
        for sid in ids:
            if sid in tips:
                t = parse_spell_tip(tips[sid])
                print(f"  tip {sid}: " + " ".join(f"{k}={v}" for k, v in t.items() if k not in ("desc", "var", "id")))
                print(f"       desc: {t['desc'][:160]!r}" + (f" var: {t['var'][:80]!r}" if t["var"] else ""))
    return 0


def main() -> int:
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracted", help="extracted client root (dbfilesclient/, interface/ with PNGs)")
    ap.add_argument("--out", default=os.path.join(repo, "psp", "bin", "data", "spells.pak"))
    ap.add_argument("--dump", action="store_true", help="print a few records of an existing --out and exit")
    # talents / skill lines
    ap.add_argument("--verify", action="store_true",
                    help="check an existing --out (talents/skills against the spell records) and exit")
    ap.add_argument("--talent-class", type=int, default=0,
                    help="--dump: also list the talent tree of this class id (1 warrior .. 11 druid)")
    ap.add_argument("--talent-prereq-cols", default="13,16",
                    help="Talent.dbc columns of PrereqTalent0,PrereqRank0 (13,16 = the 23-field 3.3.5a file; "
                         "9,12 = upstream dbc_layouts.json)")
    ap.add_argument("--ids", default="133,6603,8690,2050,585,1459,172,774", help="spell ids for --dump")
    args = ap.parse_args()
    if args.dump:
        rc = dump(args.out, [int(x) for x in args.ids.split(",") if x])
        return rc or dump_talents(args.out, args.talent_class)  # talents
    if args.verify:
        return verify(args.out)  # talents / skill lines
    if not args.extracted:
        ap.error("--extracted is required")
    prereq_cols = tuple(int(x) for x in args.talent_prereq_cols.split(","))
    entries, stats = build(args.extracted, repo, prereq_cols)
    write_pak(args.out, entries)
    print("spells_pak " + " ".join(f"{k}={v}" for k, v in stats.items()))
    print(f"wrote {args.out}: {os.path.getsize(args.out)} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
