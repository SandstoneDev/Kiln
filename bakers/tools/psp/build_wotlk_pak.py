#!/usr/bin/env python3
"""Builds psp/bin/data/wotlk.pak - the data and the art of the 3.3.5a windows:
the Dungeon Finder (LFDFrame.xml / LFGFrame.xml, MiniMapLFGFrame, DungeonCompletionAlertFrame), the achievements
(the client's AchievementUI addon, AchievementAlertFrame) and the calendar (its Calendar addon) of the original ruRU 3.3.5a
client, from the player's own extracted client (never from this repo, never from a server database).

The PSP reads this pak only on demand: a window opens it, reads its entries and closes it (no descriptor is kept -
docs/PSP_PORT_ARCHITECTURE.md); the data is freed with the window. Layouts of the raw entries =
include/platform/psp/psp_wotlk_db.hpp (keep the two in step).

Raw entries (little-endian, strings UTF-8 NUL-terminated, string offset 0 = ""):
  "lfg"      LFGDungeons.dbc + LFGDungeonGroup.dbc (ruRU): the Dungeon Finder's catalogue
             LfgHeader 32 B: "WLFG", u16 version 1, u16 dungeons, u16 groups, u16 pad, u32 dungeonOff, u32 groupOff,
                 u32 strOff, u32 strSize, u32 coinIcon (SpellIcon id of INV_Misc_Coin_02)
             LfgDungeonRec 32 B x dungeons, by id: u32 id, name, texture, description; u16 mapId, orderIndex;
                 u8 minLevel, maxLevel, recLevel, minRecLevel, maxRecLevel, typeId; s8 faction; u8 expansion,
                 groupId, difficulty, flags, encounters (DungeonEncounter.dbc rows of the map / difficulty)
             LfgGroupRec 12 B x groups, by order: u32 id, name; u16 order; u8 typeId, pad
  "ach"      Achievement.dbc + Achievement_Category.dbc: the index
             AchHeader 32 B: "WACH", u16 version 1, u16 categories, u16 achievements, u16 pad, u32 catOff, u32 achOff,
                 u32 strOff, u32 strSize, u32 pad
             AchCatRec 12 B x categories, in the client's order (Achievement_Category.ui_order, then id; the
                 statistic tree after the achievement tree): u16 id; s16 parent (-1 top); u16 order; u8 flags
                 (1 = statistic); u8 pad; u32 name
             AchIdxRec 16 B x achievements, by id: u16 id, category, prev (Supercedes), next (the achievement that
                 supercedes it), icon (SpellIcon id), flags, order (ui_order); u8 points; s8 faction (-1, 0 Horde,
                 1 Alliance)
  "ac_<id>"  one category: AchCatHeader 16 B: "WACC", u16 version 1, u16 achievements, u16 criteria, u16 pad,
                 u32 strOff; AchRec 20 B x achievements in the client's list order (ui_order, then id): u32 id,
                 name, description, reward; u16 firstCriteria, criteriaCount; AchCritRec 24 B (Achievement_Criteria
                 by its show order): u32 id, description, type, asset, quantity; u16 flags, order
  "hol"      Holidays.dbc + HolidayNames.dbc + HolidayDescriptions.dbc: "WHOL", u16 version 1, u16 count, u32 strOff;
                 HolRec 16 B x count by id: u32 id, name, description, texture (Holidays.dbc TextureFilename)
  "maps"     Map.dbc names of the instances (raid lockouts): "WMAP", u16 version 1, u16 count, u32 strOff;
                 MapRec 8 B x count by id: u16 id, u16 instance type (1 party, 2 raid), u32 name

Textures (WTEX, tools/psp/psp_texconv.py) - names and pixel sizes are the draw code's (src/ui/psp/ui_world_lfd.cpp,
ui_world_achievements.cpp, ui_world_calendar.cpp); see the functions below.

Usage: python build_wotlk_pak.py --extracted <extracted> [--out ...] [--dump] [--preview DIR]
Needs DBFilesClient (LFGDungeons, LFGDungeonGroup, Achievement, Achievement_Category, Achievement_Criteria,
SpellIcon, Holidays, HolidayNames, HolidayDescriptions, Map) and Interface\\ PNGs (LFGFrame, AchievementFrame,
Calendar, Buttons, ...). Run with the normal Python (Pillow + numpy), not the MSYS one.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_texconv as tc  # noqa: E402
from build_spells_pak import as_int, dbc_path, dbc_str, load_dbc  # noqa: E402
from build_ui_pak import CHARSET, write_pak  # noqa: E402

KIND_TEXTURE, KIND_RAW = 1, 3
LOC = 8  # the ruRU column of the 16 localized strings (enUS 0 .. ruRU 8); checked by pick() below


class Strings:
    """A string pool: offset 0 is "", each string once."""

    def __init__(self) -> None:
        self.data = bytearray(b"\0")
        self.at: dict[str, int] = {"": 0}
        self.missing: dict[str, int] = {}

    def add(self, s: str) -> int:
        s = s or ""
        if s not in self.at:
            self.at[s] = len(self.data)
            self.data.extend(s.encode("utf-8") + b"\0")
            for ch in s:
                if ch not in CHARSET_SET and ch not in "\n\r\t":
                    self.missing[ch] = self.missing.get(ch, 0) + 1
        return self.at[s]


CHARSET_SET = set(CHARSET)


def pick(rows, strs, first: int) -> int:
    """The localized column that carries the strings (ruRU client: 8)."""
    best, best_n = LOC, -1
    for k in range(16):
        n = sum(1 for r in rows if len(r) > first + k and r[first + k] != 0)
        if n > best_n:
            best, best_n = k, n
    return best


# --------------------------------------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------------------------------------

# LFGDungeons.dbc 3.3.5a (49 fields; upstream GameHandler::loadLfgDungeonDbc): 0 ID, 1..16 Name_lang, 18 MinLevel,
# 19 MaxLevel, 20 Target_level, 21 Target_level_min, 22 Target_level_max, 23 MapID, 24 Difficulty, 25 Flags,
# 26 TypeID, 27 Faction, 28 TextureFilename, 29 ExpansionLevel, 30 Order_index, 31 Group_ID, 32..47 Description_lang.
LD_NAME, LD_MIN, LD_MAX, LD_REC, LD_RMIN, LD_RMAX, LD_MAP, LD_DIFF, LD_FLAGS, LD_TYPE, LD_FACTION = (1, 18, 19, 20, 21,
                                                                                                   22, 23, 24, 25, 26,
                                                                                                   27)
LD_TEXTURE, LD_EXP, LD_ORDER, LD_GROUP, LD_DESC = 28, 29, 30, 31, 32
# LFGDungeonGroup.dbc (21): 0 ID, 1..16 Name_lang, 18 Order_index, 19 Parent_group_id, 20 Typeid.
LG_NAME, LG_ORDER, LG_TYPE = 1, 18, 20


def build_lfg(ext: str) -> tuple[bytes, dict]:
    rows, strs = load_dbc(dbc_path(ext, "LFGDungeons.dbc"))
    loc = pick(rows, strs, LD_NAME)
    grows, gstrs = load_dbc(dbc_path(ext, "LFGDungeonGroup.dbc"))
    gloc = pick(grows, gstrs, LG_NAME)
    # DungeonEncounter.dbc (23): 0 ID, 1 MapID, 2 Difficulty, 3 OrderIndex, 4 Bit, 5..20 Name_lang, 21 Flags,
    # 22 SpellIconID - the encounters of a map / difficulty (GetLFGProposal's totalEncounters, BOSSES_KILLED).
    enc: dict[tuple[int, int], int] = {}
    if os.path.exists(dbc_path(ext, "DungeonEncounter.dbc")):
        erows, _ = load_dbc(dbc_path(ext, "DungeonEncounter.dbc"))
        for r in erows:
            enc[(r[1], r[2])] = enc.get((r[1], r[2]), 0) + 1
    st = Strings()
    recs = []
    for r in sorted(rows, key=lambda r: r[0]):
        name = dbc_str(strs, r[LD_NAME + loc])
        if r[0] == 0 or not name:
            continue
        recs.append(struct.pack("<IIIIHHBBBBBBbBBBBB", r[0], st.add(name), st.add(dbc_str(strs, r[LD_TEXTURE])),
                                st.add(dbc_str(strs, r[LD_DESC + loc]).replace("\r\n", "\n")), r[LD_MAP] & 0xFFFF,
                                min(0xFFFF, r[LD_ORDER]), min(255, r[LD_MIN]), min(255, r[LD_MAX]),
                                min(255, r[LD_REC]), min(255, r[LD_RMIN]), min(255, r[LD_RMAX]), r[LD_TYPE] & 0xFF,
                                max(-128, min(127, as_int(r[LD_FACTION]))), r[LD_EXP] & 0xFF, r[LD_GROUP] & 0xFF,
                                r[LD_DIFF] & 0xFF, r[LD_FLAGS] & 0xFF,
                                min(255, enc.get((r[LD_MAP], r[LD_DIFF]), 0))))
    groups = []
    for r in sorted(grows, key=lambda r: (r[LG_ORDER], r[0])):
        groups.append(struct.pack("<IIHBB", r[0], st.add(dbc_str(gstrs, r[LG_NAME + gloc])), min(0xFFFF, r[LG_ORDER]),
                                  r[LG_TYPE] & 0xFF, 0))
    # The misc reward's coin (LFDDungeonReadyDialogReward_SetMisc: Interface\Icons\inv_misc_coin_02) - its SpellIcon
    # id, so the PSP draws it from spells.pak's icons.
    irows, istrs = load_dbc(dbc_path(ext, "SpellIcon.dbc"))
    coin = 0
    for r in irows:
        if dbc_str(istrs, r[1]).replace("/", "\\").lower().endswith("\\inv_misc_coin_02"):
            coin = r[0]
            break
    body = b"".join(recs) + b"".join(groups)
    dungeon_off = 32
    group_off = dungeon_off + 32 * len(recs)
    str_off = group_off + 12 * len(groups)
    hdr = b"WLFG" + struct.pack("<HHHHIIIII", 1, len(recs), len(groups), 0, dungeon_off, group_off, str_off,
                                len(st.data), coin)
    assert len(hdr) == 32 and all(len(x) == 32 for x in recs) and all(len(x) == 12 for x in groups)
    return hdr + body + bytes(st.data), dict(dungeons=len(recs), groups=len(groups), coin=coin, locale=loc,
                                              missing=st.missing)


# Achievement.dbc 3.3.5a (62 fields; upstream loadAchievementNameCache / ensureAchievementCategoriesLoaded): 0 ID,
# 1 Faction, 2 Instance_Id, 3 Supercedes, 4..19 Title_lang, 21..36 Description_lang, 38 Category, 39 Points,
# 40 Ui_order, 41 Flags, 42 IconID, 43..58 Reward_lang, 60 Minimum_criteria, 61 Shares_criteria.
A_FACTION, A_SUPER, A_TITLE, A_DESC, A_CAT, A_POINTS, A_ORDER, A_FLAGS, A_ICON, A_REWARD = 1, 3, 4, 21, 38, 39, 40, 41, \
    42, 43
# Achievement_Category.dbc (20): 0 ID, 1 Parent (-1), 2..17 Name_lang, 19 Ui_order.
AC_PARENT, AC_NAME, AC_ORDER = 1, 2, 19
# Achievement_Criteria.dbc (31; upstream ensureAchievementCriteriaLoaded): 0 ID, 1 Achievement_Id, 2 Type,
# 3 Asset_Id, 4 Quantity, 9..24 Description_lang, 26 Flags, 29 Timer limit, 30 Ui_order.
CR_ACH, CR_TYPE, CR_ASSET, CR_QTY, CR_DESC, CR_FLAGS, CR_ORDER = 1, 2, 3, 4, 9, 26, 30
ACH_FLAG_COUNTER = 0x1  # FrameXML ACHIEVEMENT_FLAGS_STATISTIC


def build_achievements(ext: str) -> tuple[list[tuple[str, int, bytes]], dict]:
    arows, astrs = load_dbc(dbc_path(ext, "Achievement.dbc"))
    aloc = pick(arows, astrs, A_TITLE)
    crows, cstrs = load_dbc(dbc_path(ext, "Achievement_Category.dbc"))
    cloc = pick(crows, cstrs, AC_NAME)
    krows, kstrs = load_dbc(dbc_path(ext, "Achievement_Criteria.dbc"))
    kloc = pick(krows, kstrs, CR_DESC)
    ach = {r[0]: r for r in arows if r[0] and dbc_str(astrs, r[A_TITLE + aloc])}
    cats = {r[0]: r for r in crows}
    # Statistic categories: every category holding a statistic (flag 0x1) and every one above it (upstream).
    stat = set()
    for r in ach.values():
        if r[A_FLAGS] & ACH_FLAG_COUNTER:
            c = r[A_CAT]
            while c in cats and c not in stat:
                stat.add(c)
                p = as_int(cats[c][AC_PARENT])
                c = p if p >= 0 else -1
    next_of = {}
    for r in ach.values():
        if r[A_SUPER]:
            next_of[r[A_SUPER]] = r[0]
    entries = []
    st = Strings()
    order = sorted(cats.values(), key=lambda r: (1 if r[0] in stat else 0, r[AC_ORDER], r[0]))
    cat_recs = b"".join(struct.pack("<HhHBBI", r[0], max(-1, as_int(r[AC_PARENT])), min(0xFFFF, r[AC_ORDER]),
                                    1 if r[0] in stat else 0, 0, st.add(dbc_str(cstrs, r[AC_NAME + cloc])))
                        for r in order)
    idx_recs = b"".join(struct.pack("<HHHHHHHBb", r[0], r[A_CAT], r[A_SUPER] if r[A_SUPER] in ach else 0,
                                    next_of.get(r[0], 0), min(0xFFFF, r[A_ICON]), r[A_FLAGS] & 0xFFFF,
                                    min(0xFFFF, r[A_ORDER]), min(255, r[A_POINTS]),
                                    max(-1, min(1, as_int(r[A_FACTION]))))
                        for r in sorted(ach.values(), key=lambda r: r[0]))
    cat_off = 32
    ach_off = cat_off + 12 * len(order)
    str_off = ach_off + 16 * len(ach)
    hdr = b"WACH" + struct.pack("<HHHHIIIII", 1, len(order), len(ach), 0, cat_off, ach_off, str_off,
                                len(st.data), 0)
    assert len(hdr) == 32
    entries.append(("ach", KIND_RAW, hdr + cat_recs + idx_recs + bytes(st.data)))
    # Criteria by achievement, in their show order.
    crit_of: dict[int, list] = {}
    for r in krows:
        if r[CR_ACH] in ach:
            crit_of.setdefault(r[CR_ACH], []).append(r)
    for lst in crit_of.values():
        lst.sort(key=lambda r: (r[CR_ORDER], r[0]))
    sizes = {}
    missing: dict[str, int] = dict(st.missing)
    n_crit = 0
    for cid in sorted(cats):
        rows = sorted((r for r in ach.values() if r[A_CAT] == cid), key=lambda r: (r[A_ORDER], r[0]))
        if not rows:
            continue
        cs = Strings()
        recs, crits = [], []
        for r in rows:
            first = len(crits)
            for k in crit_of.get(r[0], []):
                crits.append(struct.pack("<IIIIIHH", k[0], cs.add(dbc_str(kstrs, k[CR_DESC + kloc])), k[CR_TYPE],
                                         k[CR_ASSET], k[CR_QTY], k[CR_FLAGS] & 0xFFFF, min(0xFFFF, k[CR_ORDER])))
            recs.append(struct.pack("<IIIIHH", r[0], cs.add(dbc_str(astrs, r[A_TITLE + aloc])),
                                    cs.add(dbc_str(astrs, r[A_DESC + aloc]).replace("\r\n", "\n")),
                                    cs.add(dbc_str(astrs, r[A_REWARD + aloc])), first, len(crits) - first))
        n_crit += len(crits)
        str_off = 16 + 20 * len(recs) + 24 * len(crits)
        blob = b"WACC" + struct.pack("<HHHHI", 1, len(recs), len(crits), 0, str_off) + b"".join(recs) + \
            b"".join(crits) + bytes(cs.data)
        entries.append((f"ac_{cid}", KIND_RAW, blob))
        sizes[cid] = len(blob)
        for ch, n in cs.missing.items():
            missing[ch] = missing.get(ch, 0) + n
    return entries, dict(categories=len(order), statistic_categories=len(stat), achievements=len(ach),
                         criteria=n_crit, biggest=max(sizes.values()), total=sum(sizes.values()), locale=aloc,
                         index_kb=len(entries[0][2]) // 1024, missing=missing)


# Holidays.dbc (55): 0 ID, 1..10 Duration, 11..36 Date, 37 Region, 38 Looping, 39..48 CalendarFlags,
# 49 HolidayNameID, 50 HolidayDescriptionID, 51 TextureFilename, 52 Priority, 53 CalendarFilterType, 54 Flags.
H_NAME, H_DESC, H_TEXTURE = 49, 50, 51


def build_holidays(ext: str) -> tuple[bytes, dict]:
    rows, strs = load_dbc(dbc_path(ext, "Holidays.dbc"))
    nrows, nstrs = load_dbc(dbc_path(ext, "HolidayNames.dbc"))
    drows, dstrs = load_dbc(dbc_path(ext, "HolidayDescriptions.dbc"))
    nloc, dloc = pick(nrows, nstrs, 1), pick(drows, dstrs, 1)
    names = {r[0]: dbc_str(nstrs, r[1 + nloc]) for r in nrows}
    descs = {r[0]: dbc_str(dstrs, r[1 + dloc]) for r in drows}
    st = Strings()
    recs = b"".join(struct.pack("<IIII", r[0], st.add(names.get(r[H_NAME], "")),
                                st.add(descs.get(r[H_DESC], "").replace("\r\n", "\n")),
                                st.add(dbc_str(strs, r[H_TEXTURE])))
                    for r in sorted(rows, key=lambda r: r[0]))
    str_off = 12 + len(recs)
    return b"WHOL" + struct.pack("<HHI", 1, len(rows), str_off) + recs + bytes(st.data), dict(holidays=len(rows),
                                                                                             missing=st.missing)


# Map.dbc (66): 0 ID, 1 Directory, 2 InstanceType (1 party, 2 raid, 3 pvp, 4 arena), 5..20 MapName_lang.
M_TYPE, M_NAME = 2, 5


def build_maps(ext: str) -> tuple[bytes, dict]:
    rows, strs = load_dbc(dbc_path(ext, "Map.dbc"))
    loc = pick(rows, strs, M_NAME)
    st = Strings()
    keep = [r for r in sorted(rows, key=lambda r: r[0]) if r[M_TYPE] in (1, 2)]
    recs = b"".join(struct.pack("<HHI", r[0] & 0xFFFF, r[M_TYPE] & 0xFFFF, st.add(dbc_str(strs, r[M_NAME + loc])))
                    for r in keep)
    return b"WMAP" + struct.pack("<HHI", 1, len(keep), 12 + len(recs)) + recs + bytes(st.data), dict(maps=len(keep))


# --------------------------------------------------------------------------------------------------------------------
# Art
# --------------------------------------------------------------------------------------------------------------------

def png(root: str, rel: str) -> Image.Image | None:
    p = os.path.join(root, "interface", rel.lower().replace("\\", "/") + ".png")
    if not os.path.exists(p):
        return None
    return Image.open(p).convert("RGBA")


def need(root: str, rel: str) -> Image.Image:
    im = png(root, rel)
    if im is None:
        raise FileNotFoundError(f"interface/{rel}.png missing under {root} - extract Interface\\ with PNG sidecars")
    return im


def tcrop(im: Image.Image, l: float, r: float, t: float, b: float) -> Image.Image:
    """SetTexCoord(l, r, t, b) of a texture: the sub-image (mirrored when l > r or t > b)."""
    w, h = im.size
    x0, x1 = sorted((l * w, r * w))
    y0, y1 = sorted((t * h, b * h))
    out = im.crop((round(x0), round(y0), max(round(x0) + 1, round(x1)), max(round(y0) + 1, round(y1))))
    if l > r:
        out = out.transpose(Image.FLIP_LEFT_RIGHT)
    if t > b:
        out = out.transpose(Image.FLIP_TOP_BOTTOM)
    return out


def sized(im: Image.Image, w: float, h: float) -> Image.Image:
    return im.resize((max(1, round(w)), max(1, round(h))), Image.LANCZOS)


def t8(img: Image.Image) -> bytes:
    return tc.wtex_blob(tc.encode(img, "t8", linear=True))


def fit256(img: Image.Image) -> Image.Image:
    """PSP: a frame a few pixels over 256 high (258 at 0.41 / 0.455) squeezed to 256 - the texture is then 512 x 256,
    not 512 x 512 (half the memory while the window is open); the window draws it into its rect, 2 px taller."""
    if 256 < img.height <= 264:
        return img.resize((img.width, 256), Image.LANCZOS)
    return img


def c4444(img: Image.Image) -> bytes:
    return tc.wtex_blob(tc.encode(img, "4444", linear=True))


class Canvas:
    """A frame composed in FrameXML units (1 unit = 1 canvas pixel), cut and scaled at the end."""

    def __init__(self, w: int, h: int, x0: int = 0, y0: int = 0) -> None:
        self.im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        self.x0, self.y0 = x0, y0  # the canvas pixel of frame unit (0, 0)

    def put(self, im: Image.Image, x: float, y: float, w: float | None = None, h: float | None = None) -> None:
        if w is not None or h is not None:
            im = sized(im, w if w is not None else im.width, h if h is not None else im.height)
        self.im.alpha_composite(im, (round(self.x0 + x), round(self.y0 + y)))

    def fill(self, x: float, y: float, w: float, h: float, rgba: tuple) -> None:
        layer = Image.new("RGBA", self.im.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rectangle((round(self.x0 + x), round(self.y0 + y), round(self.x0 + x + w) - 1,
                                         round(self.y0 + y + h) - 1), fill=rgba)
        self.im.alpha_composite(layer)

    def scaled(self, sx: float, sy: float) -> Image.Image:
        return sized(self.im, self.im.width * sx, self.im.height * sy)


# --- Dungeon Finder ----------------------------------------------------------------------------------------------
# LFDParentFrame (355 x 440, UI-LFG-FRAME at TOPLEFT): baked at the trainer's 0.6 x 0.55 (ui_world_lfd.cpp kSX/kSY).
LFD_SX, LFD_SY, LFD_W, LFD_H = 0.6, 0.55, 356, 440
# The random panel's UIPanelScrollFrameTemplate (298 x 253 at BOTTOMRIGHT -34,30 = x 23..321, y 157..410): its
# ScrollBackgroundTopLeft (31 x 256 at TOPLEFT.TOPRIGHT -2,5) and ...BottomRight (31 x 106 at BOTTOMLEFT.BOTTOMRIGHT
# -2,-4) of UI-Character-ScrollBar.
LFD_RANDOM_SCROLL = (23, 157, 298, 253)
# PSP: the specific list has 10 rows of 24 units from 165 (the original 15 of 16; the PSP font is 10 px) - the
# FauxScrollFrame from ListButton1's TOPLEFT (0,7) to the last row's BOTTOMRIGHT (1,-7).
LFD_LIST_X, LFD_LIST_Y, LFD_LIST_W, LFD_ROW_U, LFD_ROWS = 25, 165, 295, 24, 10
LFD_KINDS = (("random", "ui-lfg-background-questpaper"), ("heroic", "ui-lfg-background-heroic"),
             ("specific", "ui-lfg-background-dungeonwall"), ("hol_halloween", "ui-lfg-holiday-background-halloween"),
             ("hol_love", "ui-lfg-holiday-background-love"), ("hol_brew", "ui-lfg-holiday-background-brew"),
             ("hol_summer", "ui-lfg-holiday-background-summer"))


def scrollbar_bg(root: str, h: int) -> Image.Image:
    """UIPanelScrollFrameTemplate's background: UI-Character-ScrollBar top (31 x 256, tc 0..0.484) over the bottom
    piece (31 x 106, tc 0.516..1 x 0..0.414) - h units tall, 31 wide."""
    sb = need(root, "paperdollinfoframe/ui-character-scrollbar")
    out = Image.new("RGBA", (31, h), (0, 0, 0, 0))
    out.alpha_composite(sized(tcrop(sb, 0, 0.484375, 0, 1.0), 31, 256), (0, 0))
    out.alpha_composite(sized(tcrop(sb, 0.515625, 1.0, 0, 0.4140625), 31, 106), (0, max(0, h - 106)))
    return out


def lfd_art(root: str) -> list[tuple[str, int, bytes]]:
    lay = png(root, "lfgframe/ui-lfg-frame")
    if lay is None:
        return []
    out = []
    portrait = png(root, "lfgframe/ui-lfg-portrait")
    lf = png(root, "glues/charactercreate/charactercreate-labelframe")
    for kind, bg_rel in LFD_KINDS:
        bg = png(root, "lfgframe/" + bg_rel)
        if bg is None:
            continue
        c = Canvas(LFD_W, LFD_H)
        c.put(lay, 0, 0)  # LFDQueueFrameLayout (BACKGROUND)
        if portrait is not None:  # LFDParentFramePortraitIcon (BACKGROUND of the portrait: 64 at 9+3, -5)
            c.put(portrait, 12, 5, 64, 64)
        # LFDQueueFrameBackground (BORDER): 512 x 256 at LEFT 21,-64 (the frame's middle 220 + 64 - 128 = 156).
        c.put(bg, 21, 156, 512, 256)
        if lf is not None:  # LFDQueueFrameTypeDropDown at TOPLEFT 131,-125: Left / Middle (SetWidth 180) / Right
            c.put(tcrop(lf, 0, 0.1953125, 0, 1), 131, 108, 25, 64)
            c.put(tcrop(lf, 0.1953125, 0.8046875, 0, 1), 156, 108, 180, 64)
            c.put(tcrop(lf, 0.8046875, 1, 0, 1), 336, 108, 25, 64)
        if not kind.startswith("specific"):
            x, y, w, h = LFD_RANDOM_SCROLL
            sb = scrollbar_bg(root, h + 9)  # TOPLEFT +5 above .. BOTTOMRIGHT -4 below
            c.put(sb, x + w - 2, y - 5)
        img = c.im.crop((0, 0, LFD_W, LFD_H))
        out.append((f"lfd_{kind}", KIND_TEXTURE, t8(sized(img, LFD_W * LFD_SX, LFD_H * LFD_SY))))
    # The specific list's FauxScrollFrame bar background (shown when the list scrolls).
    h = LFD_ROW_U * LFD_ROWS + 14
    out.append(("lfd_sbar", KIND_TEXTURE, t8(sized(scrollbar_bg(root, h + 7), 31 * LFD_SX, (h + 7) * LFD_SY))))
    return out


def small(root: str, rel: str, w: int, h: int, crop=None, fmt="4444") -> bytes | None:
    im = png(root, rel)
    if im is None:
        return None
    if crop:
        im = tcrop(im, *crop)
    im = sized(im, w, h)
    return c4444(im) if fmt == "4444" else t8(im)


def circle(im: Image.Image) -> Image.Image:
    """SetPortraitToTexture: the texture in a circle."""
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).ellipse((0, 0, im.width - 1, im.height - 1), fill=255)
    a = Image.fromarray(np.minimum(np.asarray(im.getchannel("A")), np.asarray(m)).astype(np.uint8), "L")
    im = im.copy()
    im.putalpha(a)
    return im


def lfg_small_art(root: str) -> list[tuple[str, int, bytes]]:
    out = []

    def add(name, blob):
        if blob:
            out.append((name, KIND_TEXTURE, blob))

    roles = png(root, "lfgframe/ui-lfg-icon-roles")
    if roles is not None:
        add("lfg_roles_s", t8(sized(roles, 128, 128)))   # cells of 33.5 px: the frame's 29, the search status' 33
        add("lfg_roles_l", t8(roles))                     # 256: the popups' 54 / 56 px
    add("lfg_rolebg", small(root, "lfgframe/ui-lfg-icons-rolebackgrounds", 128, 64, fmt="t8"))
    add("lfg_proles", small(root, "lfgframe/ui-lfg-icon-portraitroles", 32, 32))
    eye = png(root, "lfgframe/lfg-eye")
    if eye is not None:
        add("lfg_eye32", t8(sized(eye, 256, 128)))       # LFDParentFramePortrait (38 x 35 px)
        add("lfg_eye16", t8(sized(eye, 128, 64)))        # MiniMapLFGFrameIcon (16 px)
    # MiniMapLFGFrameBorder (MiniMap-TrackingBorder 52 at TOPLEFT 1,-1 of the 33-unit button): its ring, as the
    # minimap atlas' kSrcTrkBorder (the minimap scale 76 / 140), and the button's highlight.
    ring = png(root, "minimap/minimap-trackingborder")
    if ring is not None:
        add("mm_lfg_ring", c4444(sized(ring.crop((0, 0, 40, 40)), 18, 18)))
    add("mm_lfg_hl", small(root, "minimap/ui-minimap-zoombutton-highlight", 18, 18))
    for name, rel in (("lfg_cb_up", "buttons/ui-checkbox-up"), ("lfg_cb_dn", "buttons/ui-checkbox-down"),
                      ("lfg_cb_hl", "buttons/ui-checkbox-highlight"), ("lfg_cb_check", "buttons/ui-checkbox-check"),
                      ("lfg_cb_check_dis", "buttons/ui-checkbox-check-disabled")):
        add(name, small(root, rel, 16, 16))
    add("lfg_cb_multi", small(root, "buttons/ui-multicheck-up", 16, 16))
    add("lfg_cb_multi_dis", small(root, "buttons/ui-multicheck-disabled", 16, 16))
    add("lfg_lock", small(root, "lfgframe/ui-lfg-icon-lock", 16, 16, crop=(0, 0.71875, 0, 0.875)))
    add("lfg_heroic", small(root, "lfgframe/ui-lfg-icon-heroic", 16, 20, crop=(0, 0.5, 0, 0.625)))
    add("lfg_plus", small(root, "buttons/ui-plusbutton-up", 16, 16))
    add("lfg_minus", small(root, "buttons/ui-minusbutton-up", 16, 16))
    add("lfg_plushl", small(root, "buttons/ui-plusbutton-hilight", 16, 16))
    add("lfg_dd_btn", small(root, "chatframe/ui-chaticon-scrolldown-up", 16, 16))
    add("lfg_dd_btn_dn", small(root, "chatframe/ui-chaticon-scrolldown-down", 16, 16))
    add("lfg_dd_hl", small(root, "buttons/ui-common-mousehilight", 16, 16))
    add("lfg_namefr", small(root, "questframe/ui-questitemnameframe", 77, 35, fmt="t8"))  # LargeItemButtonTemplate
    # LFDDungeonReadyPopup (0.8 = the StaticPopups' kDialogScale): the filigree (292 x 54, tc 0.027..0.598 x
    # 0.578..1), the bottom art (287 x 72, tc 0..0.5605 x 0..0.5625), the separator (UI-LFG-SEPARATOR 170 x 40,
    # tc 0..0.664 x 0..0.3125), the reward ring (40, tc 0..0.675), the misc reward (inv_misc_coin_02 as a portrait),
    # UI-Panel-HideButton (32), the ready check icons (30 / 55-unit icons).
    fil = png(root, "lfgframe/ui-lfg-filigree")
    if fil is not None:
        add("lfr_fili", t8(sized(tcrop(fil, 0.02734, 0.59765, 0.578125, 1.0), 292 * 0.8, 54 * 0.8)))
        add("lfr_bottom", t8(sized(tcrop(fil, 0.0, 0.5605, 0.0, 0.5625), 287 * 0.8, 72 * 0.8)))
    add("lfr_sep", small(root, "lfgframe/ui-lfg-separator", 136, 32, crop=(0, 0.6640625, 0, 0.3125), fmt="t8"))
    add("lfr_ring", small(root, "lfgframe/ui-lfg-icon-rewardring", 32, 32, crop=(0, 0.675, 0, 0.675)))
    coin = png(root, "icons/inv_misc_coin_02")
    if coin is not None:
        add("lfr_coin", c4444(circle(sized(coin, 32, 32))))
    add("lfr_hide", small(root, "buttons/ui-panel-hidebutton-up", 32, 32))
    add("lfr_hide_dn", small(root, "buttons/ui-panel-hidebutton-down", 32, 32))
    add("lfr_hide_hl", small(root, "buttons/ui-panel-minimizebutton-highlight", 32, 32))
    for name, rel in (("lfr_wait", "raidframe/readycheck-waiting"), ("lfr_ready", "raidframe/readycheck-ready"),
                      ("lfr_notready", "raidframe/readycheck-notready")):
        add(name, small(root, rel, 32, 32))
    # DungeonCompletionAlertFrame (336 x 80, 0.6): the four UI-LFG-DUNGEONTOAST pieces (BORDER, over the dungeon
    # texture at BOTTOMLEFT 13,13).
    toast = png(root, "lfgframe/ui-lfg-dungeontoast")
    if toast is not None:
        c = Canvas(336, 80)
        c.put(tcrop(toast, 0, 0.546875, 0.28515, 0.5664), 0, 8, 70, 72)
        c.put(tcrop(toast, 0, 0.9921875, 0, 0.28125), 69, 8, 127, 72)
        c.put(tcrop(toast, 0, 0.9921875, 0.58203, 0.86328), 336 - 14 - 127, 8, 127, 72)
        c.put(tcrop(toast, 0.5546875, 0.671875, 0.28515, 0.5664), 336 - 15, 8, 15, 72)
        add("lfg_toast", t8(c.scaled(0.6, 0.6)))
        glow = png(root, "achievementframe/ui-achievement-alert-glow")
        if glow is not None:
            add("alert_glow", t8(sized(tcrop(glow, 0, 0.78125, 0, 0.66796875), 256, 128)))
            add("alert_shine", small(root, "achievementframe/ui-achievement-alert-glow", 40, 44,
                                     crop=(0.78125, 0.912109375, 0, 0.28125)))
    return out


def lfg_texture_art(root: str, ext: str) -> list[tuple[str, int, bytes]]:
    """The per-dungeon pictures LFGDungeons.dbc names (TextureFilename): the ready dialog's background
    (UI-LFG-BACKGROUND-<name>, 294 x 148 units at 0.8; the random one cut at 118 / 128 too) and the dungeon icon
    (LFGIcon-<name>: the completion toast's 45 units at 0.6, the calendar's event icons)."""
    rows, strs = load_dbc(dbc_path(ext, "LFGDungeons.dbc"))
    names = sorted({dbc_str(strs, r[LD_TEXTURE]).lower() for r in rows if dbc_str(strs, r[LD_TEXTURE])})
    out = []
    for n in names + ["randomdungeon"]:
        bg = png(root, f"lfgframe/ui-lfg-background-{n}")
        if bg is not None:
            out.append((f"lfgbg_{n}", KIND_TEXTURE, t8(sized(bg, 294 * 0.8, 148 * 0.8))))
    for n in names + ["dungeon", "raid"]:
        ic = png(root, f"lfgframe/lfgicon-{n}")
        if ic is not None:
            out.append((f"lfgic_{n}", KIND_TEXTURE, c4444(sized(ic, 32, 32))))
    return out


# --- Achievements ---------------------------------------------------------------------------------------------------
# AchievementFrame (768 x 500 + the header 67 above) at 0.6 x 0.455 (ui_world_achievements.cpp kSX / kSY).
ACH_SX, ACH_SY = 0.6, 0.455


def _edge_nine(strip: Image.Image, edge: int) -> Image.Image:
    """A backdrop edge file (8 cells L, R, T, B, TL, TR, BL, BR; T / B stored rotated) as a 3 x 3 picture."""
    e = strip.height
    cells = [strip.crop((i * e, 0, (i + 1) * e, e)) for i in range(8)]
    left, right, top, bottom, tl, tr, bl, br = cells
    top = top.transpose(Image.ROTATE_270)
    bottom = bottom.transpose(Image.ROTATE_270)
    nine = Image.new("RGBA", (3 * e, 3 * e), (0, 0, 0, 0))
    for im, x, y in ((tl, 0, 0), (top, 1, 0), (tr, 2, 0), (left, 0, 1), (right, 2, 1), (bl, 0, 2), (bottom, 1, 2),
                     (br, 2, 2)):
        nine.paste(im, (x * e, y * e))
    return sized(nine, 3 * edge, 3 * edge)


def _backdrop_edge(c: "Canvas", strip: Image.Image, x: int, y: int, w: int, h: int, edge: int) -> None:
    nine = _edge_nine(strip, edge)
    e = edge

    def cell(i, j):
        return nine.crop((i * e, j * e, (i + 1) * e, (j + 1) * e))

    c.put(cell(1, 0), x + e, y, w - 2 * e, e)
    c.put(cell(1, 2), x + e, y + h - e, w - 2 * e, e)
    c.put(cell(0, 1), x, y + e, e, h - 2 * e)
    c.put(cell(2, 1), x + w - e, y + e, e, h - 2 * e)
    c.put(cell(0, 0), x, y)
    c.put(cell(2, 0), x + w - e, y)
    c.put(cell(0, 2), x, y + h - e)
    c.put(cell(2, 2), x + w - e, y + h - e)


def _flip(im: Image.Image, h: bool = False, v: bool = False) -> Image.Image:
    if h:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if v:
        im = im.transpose(Image.FLIP_TOP_BOTTOM)
    return im


def ach_art(root: str) -> list[tuple[str, int, bytes]]:
    out = []

    def a(rel):
        return png(root, "achievementframe/" + rel)

    header = a("ui-achievement-header")
    if header is None:
        return out

    def add(name, blob):
        if blob:
            out.append((name, KIND_TEXTURE, blob))

    def S(w, h):
        return max(1, round(w * ACH_SX)), max(1, round(h * ACH_SY))

    # The frame composites (the achievements tab / the statistics tab): canvas units = frame units, y from -67.
    bg = png(root, "tutorialframe/tutorialframebackground")
    for kind, panel_bg, mark in (("a", "ui-achievement-achievementbackground", "ui-achievement-achievementwatermark"),
                                 ("s", "ui-achievement-statsbackground", "ui-achievement-statwatermark")):
        c = Canvas(768, 567, 0, 67)
        if bg is not None:  # AchievementFrameBackground (TutorialFrameBackground, TOPLEFT 16,-16 .. -16,16), tiled
            t = sized(bg, 256, 256)
            for yy in range(16, 484, 256):
                for xx in range(16, 752, 256):
                    c.put(t.crop((0, 0, min(256, 752 - xx), min(256, 484 - yy))), xx, yy)
        par = a("ui-achievement-parchment")
        if par is not None:  # AchievementFrameCategoriesBG (195 wide, 25,-23 .. 25,23)
            c.put(par, 25, 23, 195, 454)
        pb = a(panel_bg)
        if pb is not None:  # the panel's background (TOPLEFT 3,-3 .. -3,3 of 218..722 x 19..480)
            c.put(pb, 221, 22, 498, 455)
        wm = a(mark)
        if wm is not None:  # AchievementFrameWaterMark (145 of 256 at the categories' BOTTOMLEFT)
            c.put(tcrop(wm, 0, 145 / 256, 0, 1), 25, 477 - 256, 145, 256)
        ml, mt, mj = a("ui-achievement-metalborder-left"), a("ui-achievement-metalborder-top"), \
            a("ui-achievement-metalborder-joint")
        if ml is not None:
            c.put(tcrop(ml, 0, 1, 0, 0.87), 14, 32, 16, 436)
            c.put(tcrop(ml, 1, 0, 0.87, 0), 739, 32, 16, 436)
        if mt is not None:
            c.put(tcrop(mt, 0, 0.87, 1.0, 0), 28, 471, 712, 16)
            c.put(tcrop(mt, 0.87, 0, 0, 1), 28, 12, 712, 16)
        wood = a("ui-achievement-woodborder")
        if wood is not None:  # the Backdrop (edgeSize 64)
            _backdrop_edge(c, wood, 0, 0, 768, 500, 64)
        if mj is not None:
            c.put(_flip(mj, True, True), 9, 7, 32, 32)
            c.put(_flip(mj, False, True), 768 - 8 - 32, 7, 32, 32)
            c.put(_flip(mj, True, False), 9, 500 - 8 - 32, 32, 32)
            c.put(mj, 768 - 8 - 32, 500 - 8 - 32, 32, 32)
        wc = a("ui-achievement-woodborder-corner")
        if wc is not None:
            c.put(wc, 4, 2, 64, 64)
            c.put(_flip(wc, True), 768 - 4 - 64, 2, 64, 64)
            c.put(_flip(wc, False, True), 4, 500 - 3 - 64, 64, 64)
            c.put(_flip(wc, True, True), 768 - 4 - 64, 500 - 3 - 64, 64, 64)
        # AchievementFrameHeader: Left 512 x 106 at (26, -67), Right 215 x 100 at (538, -55), the point border.
        c.put(tcrop(header, 0, 1, 0, 0.4140625), 26, -67, 512, 106)
        c.put(tcrop(header, 0, 0.419921875, 0.4140625, 0.8046875), 538, -55, 215, 100)
        c.put(tcrop(header, 0.419921875, 0.6796875, 0.4140625, 0.56640625), 342.5, -16, 133, 39)
        if kind == "a":
            ddl = a("ui-achievement-rightddlinset")
            if ddl is not None:  # AchievementFrameHeaderRightDDLInset (TOPRIGHT -76,-56 of the header)
                c.put(ddl, 548, -11, 128, 32)
        add(f"ach_frame_{kind}", t8(fit256(c.scaled(ACH_SX, ACH_SY))))
    cb = a("ui-achievement-category-background")
    if cb is not None:
        add("ach_cat_bg", c4444(sized(tcrop(cb, 0, 0.6640625, 0, 1), *S(170, 32))))
    ch = a("ui-achievement-category-highlight")
    if ch is not None:
        add("ach_cat_hl", c4444(sized(tcrop(ch, 0, 0.6640625, 0, 1), *S(170, 32))))
    for name, rel in (("ach_parch", "ui-achievement-parchment-horizontal"),
                      ("ach_parch_d", "ui-achievement-parchment-horizontal-desaturated")):
        im = a(rel)
        if im is not None:
            add(name, t8(sized(im, *S(490, 256))))
    tt = a("ui-achievement-title")
    if tt is not None:
        add("ach_title_s", t8(sized(tcrop(tt, 0, 0.9765625, 0, 0.3125), *S(490, 24))))
        add("ach_title_d", t8(sized(tcrop(tt, 0, 0.9765625, 0.34375, 0.65625), *S(490, 24))))
    add("ach_iconframe", small(root, "achievementframe/ui-achievement-iconframe", 40, 40, crop=(0, 0.5625, 0, 0.5625)))
    add("ach_shields", small(root, "achievementframe/ui-achievement-shields", 64, 32))
    add("ach_shields_np", small(root, "achievementframe/ui-achievement-shields-nopoints", 64, 32))
    add("ach_plusminus", small(root, "achievementframe/ui-achievement-plusminus", 32, 32))
    add("ach_check", small(root, "achievementframe/ui-achievement-criteria-check", 16, 12, crop=(0, 0.65625, 0, 1)))
    add("ach_pbar_border", small(root, "achievementframe/ui-achievement-progressbar-border", 112, 12,
                                 crop=(0, 0.8745, 0, 0.75)))
    add("ach_bar", small(root, "paperdollinfoframe/ui-character-skills-bar", 64, 8))
    add("ach_sbar", small(root, "targetingframe/ui-statusbar", 64, 8))

    # The summary's status bar frames (UI-Achievement-Header: Left 32 x 48, Middle, Right mirrored).
    def bar_frame(width):
        c = Canvas(width + 30, 48)
        c.put(tcrop(header, 0.423828125, 0.486, 0.56640625, 0.75), 0, 0, 32, 48)
        c.put(tcrop(header, 0.486, 0.889224609375, 0.56640625, 0.75), 32, 0, width + 30 - 64, 48)
        c.put(tcrop(header, 0.486, 0.423828125, 0.56640625, 0.75), width + 30 - 32, 0, 32, 48)
        return c4444(c.scaled(ACH_SX, ACH_SY))

    add("ach_sumbar", bar_frame(488))
    add("ach_catbar", bar_frame(234))
    rh = a("ui-achievement-recentheader")
    if rh is not None:
        add("ach_recent_hdr", t8(sized(tcrop(rh, 0, 1, 0, 0.71875), *S(549, 20))))
    sb = a("ui-achievement-stat-buttons")
    if sb is not None:
        c = Canvas(496, 23)
        c.put(tcrop(sb, 0, 0.08984375, 0, 0.1796875), 0, 0, 23, 23)
        c.put(tcrop(sb, 0.08984375, 0.91015625, 0, 0.1796875), 23, 0, 450, 23)
        c.put(tcrop(sb, 0.91015625, 1, 0, 0.1796875), 473, 0, 23, 23)
        add("ach_stat_hdr", t8(c.scaled(ACH_SX, ACH_SY)))
        add("ach_stat_odd", t8(sized(tcrop(sb, 0, 1, 0.1875, 0.3671875), *S(496, 24))))
        add("ach_stat_even", t8(sized(tcrop(sb, 0, 1, 0.375, 0.5390625), *S(496, 24))))
    # AchievementFrameTabButtonTemplate: Left 21 + Middle 88 + Right 18 (49 tall; the selected one's "disabled" 59).
    for name, hgt in (("ach_tab", 49), ("ach_tab_sel", 59)):
        c = Canvas(127, hgt)
        c.put(tcrop(header, 0.47265625, 0.513671875, 0.76953125, 1.0), 0, 0, 21, hgt)
        c.put(tcrop(header, 0.513671875, 0.685546875, 0.76953125, 1.0), 21, 0, 88, hgt)
        c.put(tcrop(header, 0.685546875, 0.720703125, 0.76953125, 1.0), 109, 0, 18, hgt)
        add(name, c4444(c.scaled(ACH_SX, ACH_SY)))
    add("ach_tinyshield", small(root, "achievementframe/ui-achievement-tinyshield", 16, 16, crop=(0, 0.625, 0, 0.625)))
    add("ach_mini_border", small(root, "achievementframe/ui-achievement-progressive-iconborder", 24, 24,
                                 crop=(0, 0.65625, 0, 0.65625)))
    add("ach_mini_shield", small(root, "achievementframe/ui-achievement-progressive-shield", 16, 16,
                                 crop=(0, 0.75, 0, 0.75)))
    # AchievementAlertFrame (300 x 88 at 0.6): UI-Achievement-Alert-Background (0, .605, 0, .703).
    al = a("ui-achievement-alert-background")
    if al is not None:
        add("ach_alert_bg", t8(sized(tcrop(al, 0, 0.605, 0, 0.703), 180, 53)))
    return out


# --- Calendar ---------------------------------------------------------------------------------------------------------
# CalendarFrame (659 x 624) at 0.6 x 0.41 (ui_world_calendar.cpp kSX / kSY).
CAL_SX, CAL_SY = 0.6, 0.41


def cal_art(root: str) -> list[tuple[str, int, bytes]]:
    out = []

    def cpng(rel):
        return png(root, "calendar/" + rel)

    tb, sides, cbg = cpng("calendarframe_topandbottom"), cpng("calendarframe_sides"), cpng("calendarbackground")
    if tb is None or sides is None or cbg is None:
        return out

    def add(name, blob):
        if blob:
            out.append((name, KIND_TEXTURE, blob))

    c = Canvas(659, 630)
    # The borders: top (256 + 256 + 147 at -16), the sides (12 / 13 + 11 wide, 256 + 256 + 62), the bottom (9).
    c.put(tcrop(tb, 0, 1, 0.00390625, 0.16015625), 0, 0, 256, 46)
    c.put(tcrop(tb, 0, 1, 0.16796875, 0.32421875), 256, 0, 256, 46)
    c.put(tcrop(tb, 0, 0.57421875, 0.328125, 0.4296875), 512, 16, 147, 30)
    c.put(tcrop(sides, 0, 0.09375, 0, 1), 0, 46, 12, 256)
    c.put(tcrop(sides, 0.09375, 0.1875, 0, 1), 0, 302, 12, 256)
    c.put(tcrop(sides, 0.1953125, 0.28125, 0, 0.2421875), 2, 558, 11, 62)
    c.put(tcrop(sides, 0.296875, 0.3984375, 0, 1), 659 - 13, 46, 13, 256)
    c.put(tcrop(sides, 0.3984375, 0.484375, 0, 1), 659 - 11, 302, 11, 256)
    c.put(tcrop(sides, 0.484375, 0.5703125, 0, 0.2421875), 659 - 11, 558, 11, 62)
    c.put(tcrop(tb, 0, 1, 0.43359375, 0.46875), 2, 620, 256, 9)
    c.put(tcrop(tb, 0, 1, 0.46875, 0.50390625), 258, 620, 256, 9)
    c.put(tcrop(tb, 0, 0.56640625, 0.50390625, 0.5390625), 514, 620, 145, 9)
    # CalendarWeekday1..7Background (91 x 28 from 12,46): CalendarFrame_InitWeekday's coords.
    for i in range(1, 8):
        left = (i & 1) * (90 / 256 - 0.001)
        c.put(tcrop(cbg, left, left + 90 / 256 - 0.001, 180 / 256, 180 / 256 + 28 / 256 - 0.001), 12 + 91 * (i - 1),
              46, 91, 28)
    # CalendarMonthBackground (142 x 36 at TOP -3,-1) and CalendarYearBackground (47 x 24 under it).
    c.put(tcrop(cbg, 0, 0.5546875, 0.828125, 0.96875), 329.5 - 3 - 71, 1, 142, 36)
    c.put(tcrop(cbg, 0, 0.5546875, 0.828125, 0.96875), 329.5 - 3 - 2 - 23.5, 24, 47, 24)
    add("cal_frame", t8(fit256(c.scaled(CAL_SX, CAL_SY))))
    D = (round(91 * CAL_SX), round(91 * CAL_SY))
    # The day button backgrounds: the four 90-unit cells of CalendarBackground (random per button).
    add("cal_daybg", t8(sized(tcrop(cbg, 0, 180 / 256, 0, 180 / 256), D[0] * 2, D[1] * 2)))
    add("cal_dayhl", small(root, "calendar/highlights", D[0], D[1], crop=(0, 0.34375, 0, 0.6875)))
    add("cal_today", small(root, "calendar/currentday", round(145 * CAL_SX), round(140 * CAL_SY),
                           crop=(0.0078125, 0.57421875, 0.0390625, 0.5859375), fmt="t8"))
    add("cal_todayglow", small(root, "calendar/currentday", round(112 * CAL_SX), round(130 * CAL_SY),
                               crop=(0.5625, 1, 0, 0.5078125)))
    add("cal_evbg", small(root, "calendar/calendareventbackground", D[0], round(86 * CAL_SY),
                          crop=(0, 0.671875, 0, 0.671875)))
    add("cal_pending", small(root, "calendar/eventnotification", round(71 * CAL_SX), D[1],
                             crop=(0.03125, 0.6484375, 0.03125, 0.8671875)))
    add("cal_more", small(root, "calendar/morearrow", 10, 6, crop=(0, 0.9375, 0, 0.6875)))
    add("cal_evhl", small(root, "calendar/eventhighlight", 64, 8, crop=(0, 1, 0, 0.5)))
    add("cal_prev", small(root, "buttons/ui-spellbookicon-prevpage-up", 20, 20))
    add("cal_next", small(root, "buttons/ui-spellbookicon-nextpage-up", 20, 20))
    add("cal_prev_dis", small(root, "buttons/ui-spellbookicon-prevpage-disabled", 20, 20))
    add("cal_next_dis", small(root, "buttons/ui-spellbookicon-nextpage-disabled", 20, 20))
    # The event type icons (CALENDAR_EVENTTYPE_TEXTURES) and the holidays (Calendar_<name>[Start / Ongoing / End]).
    for name, rel in (("cal_ev_pvp01", "ui-calendar-event-pvp01"), ("cal_ev_pvp02", "ui-calendar-event-pvp02"),
                      ("cal_ev_meeting", "meetingicon"), ("cal_ev_other", "ui-calendar-event-other")):
        add(name, small(root, "calendar/" + rel, 32, 32))
    hdir = os.path.join(root, "interface", "calendar", "holidays")
    if os.path.isdir(hdir):
        for f in sorted(os.listdir(hdir)):
            if f.endswith(".png"):
                im = Image.open(os.path.join(hdir, f)).convert("RGBA")
                add("calh_" + f[:-4], t8(sized(tcrop(im, 0, 0.7109375, 0, 0.7109375), *D)))
    return out


# --------------------------------------------------------------------------------------------------------------------

def main() -> int:
    repo = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracted", help="extracted client root (dbfilesclient/, interface/)")
    ap.add_argument("--out", default=os.path.join(repo, "psp", "bin", "data", "wotlk.pak"))
    ap.add_argument("--dump", action="store_true", help="list the entries of an existing --out pak and exit")
    ap.add_argument("--preview", help="directory for PNG previews of the composed frames")
    a = ap.parse_args()
    if a.dump:
        return dump(a.out)
    if not a.extracted:
        ap.error("--extracted is required")
    ext = a.extracted
    entries: list[tuple[str, int, bytes]] = []
    lfg, s = build_lfg(ext)
    entries.append(("lfg", KIND_RAW, lfg))
    print(f"  lfg   {len(lfg):7d} B  dungeons={s['dungeons']} groups={s['groups']} coin_icon={s['coin']} "
          f"locale={s['locale']}")
    missing = dict(s["missing"])
    ach, s = build_achievements(ext)
    entries += ach
    print(f"  ach   index {s['index_kb']} KB: {s['categories']} categories ({s['statistic_categories']} statistic), "
          f"{s['achievements']} achievements, {s['criteria']} criteria; ac_* {len(ach) - 1} entries, "
          f"{s['total'] // 1024} KB, biggest {s['biggest']} B; locale {s['locale']}")
    for ch, n in s["missing"].items():
        missing[ch] = missing.get(ch, 0) + n
    hol, s = build_holidays(ext)
    entries.append(("hol", KIND_RAW, hol))
    print(f"  hol   {len(hol):7d} B  holidays={s['holidays']}")
    for ch, n in s["missing"].items():
        missing[ch] = missing.get(ch, 0) + n
    maps, s = build_maps(ext)
    entries.append(("maps", KIND_RAW, maps))
    print(f"  maps  {len(maps):7d} B  instance maps={s['maps']}")
    art = lfd_art(ext) + lfg_small_art(ext) + lfg_texture_art(ext, ext)
    print(f"  dungeon finder art: {len(art)} textures, {sum(len(b) for _, _, b in art) // 1024} KB")
    aart = ach_art(ext)
    print(f"  achievement art: {len(aart)} textures, {sum(len(b) for _, _, b in aart) // 1024} KB")
    cart = cal_art(ext)
    print(f"  calendar art: {len(cart)} textures, {sum(len(b) for _, _, b in cart) // 1024} KB")
    art += aart + cart
    for name, kind, blob in art:
        entries.append((name, kind, blob))
    if a.preview:
        preview(a.preview, art)
    write_pak(a.out, entries)
    print(f"wrote {a.out}: {len(entries)} entries, {os.path.getsize(a.out)} B")
    if missing:
        print(f"  characters not in build_ui_pak.CHARSET (drawn as '?'): "
              f"{dict(sorted(missing.items(), key=lambda kv: -kv[1])[:40])}")
    return 0


def preview(folder: str, art: list[tuple[str, int, bytes]]) -> None:
    """Decodes the T8 / 4444 blobs back to PNG (self-check of the composites)."""
    os.makedirs(folder, exist_ok=True)
    for name, kind, blob in art:
        if kind != KIND_TEXTURE or blob[:4] != b"WTEX":
            continue
        psm, swz = blob[4], blob[5]
        w, h, uw, uh, ncl = struct.unpack_from("<HHHHH", blob, 8)
        clut_off, pix_off = struct.unpack_from("<II", blob, 24)
        bpp = {5: 1, 2: 2}.get(psm)
        if bpp is None:
            continue
        raw = np.frombuffer(blob[pix_off:pix_off + w * h * bpp], dtype=np.uint8)
        wb = w * bpp
        a = raw.reshape(h // 8, wb // 16, 8, 16).transpose(0, 2, 1, 3).reshape(h, wb)  # unswizzle
        if psm == 5:
            clut = np.frombuffer(blob[clut_off:clut_off + ncl * 4], dtype=np.uint8).reshape(ncl, 4)
            img = clut[a]
        else:
            v = a.view("<u2")
            img = np.stack([(v & 15) * 17, ((v >> 4) & 15) * 17, ((v >> 8) & 15) * 17, ((v >> 12) & 15) * 17],
                           -1).astype(np.uint8)
        Image.fromarray(img[:uh, :uw], "RGBA").save(os.path.join(folder, f"{name}.png"))


def dump(path: str) -> int:
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"WPAK":
        print(f"{path}: not a WPAK")
        return 1
    count, toc = struct.unpack_from("<II", data, 8)
    print(f"{path}: {count} entries")
    for i in range(count):
        name, off, size, kind, _ = struct.unpack_from("<48sIIII", data, toc + 64 * i)
        name = name.split(b"\0", 1)[0].decode()
        extra = ""
        if kind == KIND_TEXTURE:
            w, h, uw, uh = struct.unpack_from("<HHHH", data, off + 8)
            extra = f"  {w}x{h} ({uw}x{uh}) psm={data[off + 4]}"
        print(f"  {name:28s} kind={kind} {size:8d} B{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
