#!/usr/bin/env python3
"""psp/bin/data/glue.pak - the glue screens' art that is loaded only on demand
(src/ui/psp/ui_glue.cpp, ui_loading_screen.cpp), kept out of ui.pak (ui.pak is read whole at boot).

  python tools/psp/build_glue_pak.py --extracted <extracted> [--out psp/bin/data/glue.pak]

(build_ui_pak.py runs this too, after ui.pak, unless --no-glue.)

Entries (WPAK, tools/psp/build_ui_pak.py write_pak):
  maps        raw   u32 count; count x {u32 mapId, u32 loadingScreenId, u32 wide} - Map.dbc LoadingScreenID,
                    wide = LoadingScreens.dbc hasWideScreen and the "...Wide" art exists
  ls_<id>     tex   the loading screen of LoadingScreens.dbc id, 512 x 256 T8 (the wide art when there is one;
                    the 4:3 art squashed - the PSP draws it back at 4:3 between black bars)
  bar_*       tex   Interface/Glues/LoadingBar: border, fill, spark, glass
  zones       raw   u32 count; count x {u32 areaId, u32 offset}; then NUL-terminated UTF-8 names - AreaTable.dbc
                    top-level areas (ParentAreaID 0), ruRU name (CharacterSelect location line)
  gl_*        tex   CharacterSelect / RealmList / GlueDialog art (see GLUE_ART)
  cs_<scene>  tex   the character-select background of a scene (UI_<Scene>.m2 rendered by
                    tools/psp/converter/psp_glue_scene_conv into <scenes>/cs_<scene>.png), 512 x 256 T8, drawn
                    over the whole 480 x 272 screen
  cs_scenes   raw   u32 count; count x GlueScene (include/ui/psp/ui_glue_scene.hpp, 104 bytes): name[16], eye[3],
                    target[3], fovy, near, far, stand[3], yaw, shiftX, fog[4], tint[3], pad - the camera the
                    picture was rendered with, for the character drawn over it
  gl_btn_*_b  tex   Glue-Panel-Button-*-Blue (CharacterSelect_DeathKnightSwap), like ui.pak btn_*
  cc_*        CharacterCreate (include/ui/psp/ui_character_create.hpp):
                tex  cc_races / cc_racesround (UI-CharacterCreate-Races / -RacesRound, 256 x 128, cells 32),
                     cc_classes + cc_classes_grey (SetButtonDesaturated) 128 x 128, cc_gender 64 x 32,
                     cc_hl (UI-CharacterCreate-Highlights 64 x 64), cc_shadow (IconShadow 32 x 32),
                     cc_arrow_{l,r}{,_d,_hl} (Glue-Left/RightArrow-Button-*, 16 x 16)
                raw  cc_custom  u32 count; count x {u8 race, sex, skins, faces, hairStyles, hairColors, facials,
                                pad; char hairKey[12]; char facialKey[12]} - CharSections rows flagged playable
                                (0x1) and not death knight (0x4), CharacterFacialHairStyles, ChrRaces
                                Hair/FacialHairCustomization
                     cc_outfits u32 count; count x {u8 race, class, sex, pad; u32 display[24]; u8 invType[24]}
                                - CharStartOutfit.dbc (the outfit the model wears on the creation screen)
                     cc_text    u32 count; count x {u32 keyOff, u32 valueOff} sorted by key, then NUL-terminated
                                UTF-8 strings - GlueStrings.lua (ruRU) keys of the creation screen, race names
                                (RACE_NAME_<id>, RACE_NAME_<id>_FEMALE from ChrRaces.dbc)
  map transfer (include/rendering/psp/psp_map_format.hpp):
  mapdirs     raw   u32 count; count x {u32 mapId, u8 instanceType, u8 flags, u8 expansion, u8 maxPlayers,
                    u16 dirOff, u16 nameOff} by mapId, then NUL-terminated strings - Map.dbc Directory (lower case:
                    the paks' <dir>), MapName_lang ruRU; flags 1 = the WDT names a global WMO (MPHD 0x1), 2 = the
                    WDT lists ADT tiles (WorldSession::mapInfo)
  at_<mapId>  raw   n x {u32 id, u32 mapId, f32 x, y, z, radius, boxLength, boxWidth, boxHeight, boxYaw} -
                    AreaTrigger.dbc of one map (WorldSession::updateAreaTriggers, upstream loadAreaTriggerDbc)
Client art stays out of git: the pak is written under psp/bin/ (ignored).
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_texconv as tc  # noqa: E402

KIND_TEXTURE = 1
KIND_RAW = 3
# The locale slot of a 3.3.5a localized string (enUS 0 koKR frFR deDE zhCN zhTW esES esMX ruRU 8 ...): the tree's own
# language, set by use_locale_of() from the extracted DBCs (Kiln bakes enUS and ruRU trees). The name stays RU
# (build_ui_pak reads it); 8 until a tree is seen.
RU = 8

LS_SIZE = (512, 256)


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


def use_locale_of(root: str) -> int:
    """The text slot of the extracted tree (the slot of ChrClasses' Name_lang with the most names; a client
    carries one language) -> RU. An enUS tree read slot 8 and baked empty zone / race / map names."""
    global RU
    p = os.path.join(root, "dbfilesclient", "chrclasses.dbc")
    if os.path.exists(p):
        rows, string = read_dbc(p)
        counts = [sum(1 for r in rows if 4 + k < len(r) and string(r[4 + k])) for k in range(16)]
        if max(counts) > 0:
            RU = max(range(16), key=lambda k: counts[k])
    print(f"  locale slot {RU}")
    return RU


def _png(root: str, blp_path: str) -> str:
    """Interface\\Glues\\LoadingScreens\\X.blp -> <root>/interface/glues/loadingscreens/x.png"""
    rel = blp_path.replace("\\", "/").lower()
    if rel.endswith(".blp"):
        rel = rel[:-4]
    return os.path.join(root, rel + ".png")


def loading_screens(root: str) -> tuple[list[tuple[str, int, bytes]], int]:
    dbc = os.path.join(root, "dbfilesclient")
    ls_rows, ls_str = read_dbc(os.path.join(dbc, "loadingscreens.dbc"))
    map_rows, _ = read_dbc(os.path.join(dbc, "map.dbc"))
    screens = {}  # id -> (png, wide)
    for r in ls_rows:
        sid, path, has_wide = r[0], ls_str(r[2]), r[3]
        base = _png(root, path)
        wide = base[:-4] + "wide.png"
        if has_wide and os.path.exists(wide):
            screens[sid] = (wide, 1)
        elif os.path.exists(base):
            screens[sid] = (base, 0)
    out = []
    used = set()
    maps = []
    for r in map_rows:
        map_id, ls_id = r[0], r[57]  # Map.dbc 3.3.5a: field 57 LoadingScreenID
        if ls_id in screens:
            maps.append((map_id, ls_id, screens[ls_id][1]))
            used.add(ls_id)
    blob = struct.pack("<I", len(maps)) + b"".join(struct.pack("<III", *m) for m in maps)
    out.append(("maps", KIND_RAW, blob))
    total = len(blob)
    for sid in sorted(used):
        img = Image.open(screens[sid][0]).convert("RGBA").resize(LS_SIZE, Image.LANCZOS)
        img.putalpha(255)
        b = tc.wtex_blob(tc.encode(img, "t8", linear=True))
        out.append((f"ls_{sid}", KIND_TEXTURE, b))
        total += len(b)
    print(f"  loading screens: {len(used)} for {len(maps)} maps")
    return out, total


def loading_bar(root: str) -> list[tuple[str, int, bytes]]:
    d = os.path.join(root, "interface", "glues", "loadingbar")
    out = []
    for name, fn, size in (("bar_border", "loading-barborder", (256, 32)), ("bar_fill", "loading-barfill", (16, 32)),
                           ("bar_spark", "ui-loadingbar-spark", (16, 32)), ("bar_glass", "loading-barglass", (256, 32))):
        p = os.path.join(d, fn + ".png")
        if not os.path.exists(p):
            print(f"  skip {name} (missing {p})")
            continue
        img = Image.open(p).convert("RGBA").resize(size, Image.LANCZOS)
        out.append((name, KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "4444", linear=True))))
    return out


def zones(root: str) -> list[tuple[str, int, bytes]]:
    p = os.path.join(root, "dbfilesclient", "areatable.dbc")
    if not os.path.exists(p):
        return []
    rows, string = read_dbc(p)
    names = []
    for r in rows:
        if r[2] != 0:  # ParentAreaID: zones only (SMSG_CHAR_ENUM sends the zone)
            continue
        name = string(r[11 + RU])  # AreaName_lang
        if name:
            names.append((r[0], name.encode("utf-8")))
    names.sort()
    head = struct.pack("<I", len(names))
    table = b""
    strings = b""
    base = 4 + 8 * len(names)
    for area, nm in names:
        table += struct.pack("<II", area, base + len(strings))
        strings += nm + b"\0"
    print(f"  zones: {len(names)}")
    return [("zones", KIND_RAW, head + table + strings)]


def _img(root: str, rel: str) -> Image.Image | None:
    p = os.path.join(root, rel)
    return Image.open(p).convert("RGBA") if os.path.exists(p) else None


# name, path, size, flip
GLUE_ART = [
    # CharSelectCharacterButtonTemplate HighlightTexture (ADD), 256 x 74 units over a 256 x 70 button.
    ("gl_cs_hl", "interface/glues/characterselect/glue-characterselect-highlight.png", (128, 32), False),
    # CharacterSelectRotateLeft / Right: UI-RotationRight-Big-Up (the left one mirrored by its TexCoords).
    ("gl_rot_r", "interface/glues/charactercreate/ui-rotationright-big-up.png", (32, 32), False),
    ("gl_rot_l", "interface/glues/charactercreate/ui-rotationright-big-up.png", (32, 32), True),
    # GlueDialogAlertIcon / CharacterDeleteAlertIcon.
    ("gl_alert", "interface/dialogframe/ui-dialog-icon-alertnew.png", (32, 32), False),
    # RealmSortButtonTemplate (WhoFrame-ColumnTabs), RealmListHighlight (UI-QuestLogTitleHighlight, ADD).
    ("gl_col_tab", "interface/friendsframe/whoframe-columntabs.png", (64, 32), False),
    ("gl_rl_hl", "interface/questframe/ui-questlogtitlehighlight.png", (64, 16), False),
]

# RealmListBackground: 640 x 512 units (HelpFrame quarters), HitRectInsets right 44 bottom 13 -> 596 x 499 drawn,
# UI-DialogBox-Header 256 x 64 at TOP (-12, 12) sticks out 12 above. Baked at RL_SX x RL_SY (ui_glue.cpp).
RL_SX, RL_SY, RL_W, RL_H, RL_TOP = 0.765, 0.524, 596, 511, 12


def realm_frame(root: str) -> list[tuple[str, int, bytes]]:
    h = "interface/helpframe/helpframe-"
    parts = {k: _img(root, h + k + ".png") for k in ("topleft", "top", "topright", "botleft", "bottom", "botright")}
    if any(v is None for v in parts.values()):
        print("  skip gl_rl_frame (HelpFrame art missing)")
        return []
    c = Image.new("RGBA", (640, 512 + RL_TOP), (0, 0, 0, 0))
    c.alpha_composite(parts["topleft"], (0, RL_TOP))
    c.alpha_composite(parts["top"], (256, RL_TOP))
    c.alpha_composite(parts["topright"], (512, RL_TOP))
    c.alpha_composite(parts["botleft"], (0, 256 + RL_TOP))
    c.alpha_composite(parts["bottom"], (256, 256 + RL_TOP))
    c.alpha_composite(parts["botright"], (512, 256 + RL_TOP))
    hdr = _img(root, "interface/dialogframe/ui-dialogbox-header.png")
    if hdr is not None:
        c.alpha_composite(hdr, (320 - 12 - 128, 0))
    img = c.crop((0, 0, RL_W, RL_H)).resize((round(RL_W * RL_SX), round(RL_H * RL_SY)), Image.LANCZOS)
    return [("gl_rl_frame", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True)))]


def glue_art(root: str) -> list[tuple[str, int, bytes]]:
    out = []
    for name, rel, size, flip in GLUE_ART:
        img = _img(root, rel)
        if img is None:
            print(f"  skip {name} (missing {rel})")
            continue
        img = img.resize(size, Image.LANCZOS)
        if flip:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        out.append((name, KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "4444", linear=True))))
    return out + realm_frame(root)


# The character-select backgrounds. GlueParent.lua SetBackgroundModel loads
# Interface\Glues\Models\UI_<name>\UI_<name>.m2; psp_glue_scene_conv renders each through its camera
# into <scenes>/cs_<name>.png + a line of <scenes>/cs_scenes.txt.
CS_SIZE = (512, 256)
CS_SCENE_FMT = "<16s3f3f3f3f2f4f3ff"  # GlueScene, 104 bytes


def char_select_scenes(scenes_dir: str) -> list[tuple[str, int, bytes]]:
    meta = os.path.join(scenes_dir, "cs_scenes.txt")
    if not os.path.exists(meta):
        print(f"  character-select scenes: none ({meta} missing - run psp_glue_scene_conv)")
        return []
    out = []
    rows = []
    with open(meta, encoding="utf-8") as f:
        for line in f:
            t = line.split()
            if not t:
                continue
            name = t[0]
            kv = {}
            i = 1
            while i < len(t):
                key = t[i]
                n = {"eye": 3, "target": 3, "stand": 3, "fog": 4, "tint": 3}.get(key, 1)
                kv[key] = [float(x) for x in t[i + 1:i + 1 + n]]
                i += 1 + n
            png = os.path.join(scenes_dir, f"cs_{name}.png")
            if not os.path.exists(png):
                print(f"  skip cs_{name} (missing {png})")
                continue
            # PSP: T8 with Floyd-Steinberg over a median-cut palette (skies band without the dither).
            img = Image.open(png).convert("RGB").resize(CS_SIZE, Image.LANCZOS)
            img = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
            img = img.convert("RGBA")
            out.append((f"cs_{name}", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
            rows.append(struct.pack(CS_SCENE_FMT, name.encode("ascii")[:15], *kv["eye"], *kv["target"],
                                    kv["fovy"][0], kv["near"][0], kv["far"][0], *kv["stand"], kv["yaw"][0],
                                    kv["shiftx"][0], *kv["fog"], *kv["tint"], 0.0))
    assert all(len(r) == 104 for r in rows)
    out.append(("cs_scenes", KIND_RAW, struct.pack("<I", len(rows)) + b"".join(rows)))
    print(f"  character-select scenes: {len(rows)}")
    return out


def blue_buttons(root: str) -> list[tuple[str, int, bytes]]:
    """CharacterSelect_DeathKnightSwap: Glue-Panel-Button-{Up,Down,Highlight}-Blue, cut like ui.pak btn_*."""
    from build_ui_pak import build_texture  # noqa: E402 (same folder)
    out = []
    for name, fn in (("gl_btn_up_b", "up"), ("gl_btn_down_b", "down"), ("gl_btn_hl_b", "highlight")):
        rel = f"interface/glues/common/glue-panel-button-{fn}-blue.png"
        if not os.path.exists(os.path.join(root, rel)):
            print(f"  skip {name} (missing {rel})")
            continue
        out.append((name, KIND_TEXTURE, build_texture(root, "button", rel, dict(crop=(0, 0, 148, 48), scale=0.5,
                                                                                    cap=10))))
    return out


# --- CharacterCreate ------------------------------------------------------------------------------

CC_TEXT_PREFIXES = ("RACE_INFO_", "ABILITY_INFO_", "CLASS_", "CHAR_CUSTOMIZATION", "HAIR_", "FACIAL_HAIR_")
CC_TEXT_KEYS = ("ALLIANCE", "HORDE", "CHARACTER_CREATE_ACCEPT", "RANDOMIZE", "NAME", "MALE", "FEMALE", "BACK")


def _lua_strings(path: str) -> dict[str, str]:
    """KEY = "value"; lines of GlueStrings.lua, Lua escapes and |n turned into the characters."""
    import re
    out = {}
    pat = re.compile(r'^([A-Z0-9_]+)\s*=\s*"(.*)";\s*$')
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = pat.match(line.rstrip("\r\n"))
            if not m:
                continue
            v = m.group(2)
            v = v.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\").replace("|n", "\n")
            out[m.group(1)] = v
    return out


def _dbc_rows(path: str):
    with open(path, "rb") as f:
        d = f.read()
    _, n, fc, rs, ss = struct.unpack_from("<4s4I", d, 0)
    rows = [d[20 + i * rs:20 + (i + 1) * rs] for i in range(n)]
    strings = d[20 + n * rs:]

    def string(off: int) -> str:
        e = strings.index(b"\0", off)
        return strings[off:e].decode("utf-8", "replace")
    return rows, fc, string


def char_create(root: str) -> list[tuple[str, int, bytes]]:
    out = []
    cc = "interface/glues/charactercreate/"
    common = "interface/glues/common/"

    def tex(name, rel, size, fmt="4444", grey=False):
        img = _img(root, rel)
        if img is None:
            print(f"  skip {name} (missing {rel})")
            return
        img = img.resize(size, Image.LANCZOS)
        if grey:  # SetButtonDesaturated: luminance, alpha kept
            a = img.getchannel("A")
            img = img.convert("L").convert("RGBA")
            img.putalpha(a)
        out.append((name, KIND_TEXTURE, tc.wtex_blob(tc.encode(img, fmt, linear=True))))

    tex("cc_races", cc + "ui-charactercreate-races.png", (256, 128))
    tex("cc_racesround", cc + "ui-charactercreate-racesround.png", (256, 128))
    tex("cc_classes", cc + "ui-charactercreate-classes.png", (128, 128))
    tex("cc_classes_grey", cc + "ui-charactercreate-classes.png", (128, 128), grey=True)
    tex("cc_gender", cc + "ui-charactercreate-gender.png", (64, 32))
    tex("cc_hl", cc + "ui-charactercreate-highlights.png", (64, 64))
    tex("cc_shadow", cc + "ui-charactercreate-iconshadow.png", (32, 32))
    for side, fn in (("l", "left"), ("r", "right")):
        tex(f"cc_arrow_{side}", common + f"glue-{fn}arrow-button-up.png", (16, 16))
        tex(f"cc_arrow_{side}_d", common + f"glue-{fn}arrow-button-down.png", (16, 16))
        tex(f"cc_arrow_{side}_hl", common + f"glue-{fn}arrow-button-highlight.png", (16, 16))

    dbc = os.path.join(root, "dbfilesclient")
    # Customization ranges: CharSections Flags 0x1 = offered at creation, 0x4 = death knight only.
    sec_rows, sec_fc, _ = _dbc_rows(os.path.join(dbc, "charsections.dbc"))
    rng = {}
    for r in sec_rows:
        f = struct.unpack_from(f"<{sec_fc}I", r)
        race, sex, base, flags, var, col = f[1], f[2], f[3], f[7], f[8], f[9]
        if not (flags & 0x1) or (flags & 0x4):
            continue
        e = rng.setdefault((race, sex), [0, 0, 0, 0])
        if base == 0:
            e[0] = max(e[0], col + 1)       # skin colour
        elif base == 1:
            e[1] = max(e[1], var + 1)       # face
        elif base == 3:
            e[2] = max(e[2], var + 1)       # hair style
            e[3] = max(e[3], col + 1)       # hair colour
    fh_rows, fh_fc, _ = _dbc_rows(os.path.join(dbc, "characterfacialhairstyles.dbc"))
    facial = {}
    for r in fh_rows:
        f = struct.unpack_from(f"<{fh_fc}I", r)
        facial[(f[0], f[1])] = max(facial.get((f[0], f[1]), 0), f[2] + 1)
    race_rows, race_fc, race_str = _dbc_rows(os.path.join(dbc, "chrraces.dbc"))
    keys = {}
    names = {}
    for r in race_rows:
        f = struct.unpack_from(f"<{race_fc}I", r)
        rid = f[0]
        keys[rid] = (race_str(f[67]).upper(), race_str(f[65]).upper(), race_str(f[66]).upper())
        male = race_str(f[14 + RU])
        female = race_str(f[31 + RU]) or male
        names[f"RACE_NAME_{rid}"] = male
        names[f"RACE_NAME_{rid}_FEMALE"] = female
    rows = []
    for (race, sex), e in sorted(rng.items()):
        hair, fm, ff = keys.get(race, ("NORMAL", "NORMAL", "NORMAL"))
        rows.append(struct.pack("<8B12s12s", race, sex, max(1, e[0]), max(1, e[1]), max(1, e[2]), max(1, e[3]),
                                max(1, facial.get((race, sex), 1)), 0, hair.encode()[:11],
                                (ff if sex else fm).encode()[:11]))
    out.append(("cc_custom", KIND_RAW, struct.pack("<I", len(rows)) + b"".join(rows)))

    of_rows, _, _ = _dbc_rows(os.path.join(dbc, "charstartoutfit.dbc"))
    orows = []
    for r in of_rows:
        race, cls, sex = r[4], r[5], r[6]
        disp = struct.unpack_from("<24i", r, 8 + 96)
        inv = struct.unpack_from("<24i", r, 8 + 192)
        orows.append(struct.pack("<4B24I24B", race, cls, sex, 0, *[max(0, d) for d in disp],
                                 *[max(0, min(255, i)) for i in inv]))
    out.append(("cc_outfits", KIND_RAW, struct.pack("<I", len(orows)) + b"".join(orows)))

    gs = _lua_strings(os.path.join(root, "interface", "gluexml", "gluestrings.lua"))
    text = {k: v for k, v in gs.items() if k.startswith(CC_TEXT_PREFIXES) or k in CC_TEXT_KEYS or
            k.endswith("_DISABLED")}
    text.update(names)
    try:  # characters the PSP fonts lack (build_ui_pak.py CHARSET) would print as "?"
        from build_ui_pak import CHARSET  # noqa: E402 (same folder)
        missing = sorted({ch for v in text.values() for ch in v if ch not in CHARSET and ch != "\n"})
        if missing:
            print("  cc_text: characters not in the font CHARSET:", " ".join(repr(c) for c in missing))
    except ImportError:
        pass
    items = sorted(text.items())
    head = struct.pack("<I", len(items))
    table, strings = b"", b""
    base = 4 + 8 * len(items)
    for k, v in items:
        ko = base + len(strings)
        strings += k.encode() + b"\0"
        vo = base + len(strings)
        strings += v.encode("utf-8") + b"\0"
        table += struct.pack("<II", ko, vo)
    out.append(("cc_text", KIND_RAW, head + table + strings))
    print(f"  character create: {len(rows)} race/sex ranges, {len(orows)} outfits, {len(items)} strings")
    return out


# --- map transfer ----------------------------------------------------------------------------------------------------
# Map.dbc 3.3.5a: 0 ID, 1 Directory, 2 InstanceType, 3 Flags, 4 PVP, 5..21 MapName_lang (ruRU slot 8), ...,
# 57 LoadingScreenID, 63 ExpansionID, 65 MaxPlayers. AreaTrigger.dbc: 0 ID, 1 MapID, 2..4 x y z (server
# coordinates: field 2 = the wire's X), 5 radius, 6..8 box length / width / height, 9 box yaw.
def _wdt_flags(root: str, directory: str) -> int:
    p = os.path.join(root, "world", "maps", directory.lower(), directory.lower() + ".wdt")
    if not os.path.exists(p):
        return 0
    with open(p, "rb") as f:
        d = f.read()
    off, mphd, global_wmo, tiles = 0, 0, False, False
    while off + 8 <= len(d):
        magic, size = d[off:off + 4], struct.unpack_from("<I", d, off + 4)[0]
        body = d[off + 8:off + 8 + size]
        if magic == b"DHPM" and size >= 4:  # MPHD (chunk ids are stored reversed)
            mphd = struct.unpack_from("<I", body)[0]
        elif magic == b"OMWM":  # MWMO
            global_wmo = len(body.split(b"\0")[0]) > 0
        elif magic == b"NIAM":  # MAIN: 64 x 64 x {u32 flags, u32 async}
            tiles = any(struct.unpack_from("<I", body, i * 8)[0] & 1 for i in range(min(4096, size // 8)))
        off += 8 + size
    return (1 if (mphd & 1) and global_wmo else 0) | (2 if tiles else 0)


def map_transfer(root: str) -> list[tuple[str, int, bytes]]:
    dbc = os.path.join(root, "dbfilesclient")
    out = []
    p = os.path.join(dbc, "map.dbc")
    if os.path.exists(p):
        rows, string = read_dbc(p)
        recs, pool = [], b""
        for r in sorted(rows, key=lambda r: r[0]):
            directory = string(r[1])
            if not directory:
                continue
            dir_off = len(pool)
            pool += directory.lower().encode("ascii", "replace") + b"\0"
            name_off = len(pool)
            pool += string(r[5 + RU]).encode("utf-8") + b"\0"
            if len(pool) > 0xFFFF:
                raise ValueError("mapdirs: string pool over 64 KB")
            recs.append(struct.pack("<IBBBBHH", r[0], min(r[2], 255), _wdt_flags(root, directory), min(r[63], 255),
                                    min(r[65], 255), dir_off, name_off))
        blob = struct.pack("<I", len(recs)) + b"".join(recs) + pool
        out.append(("mapdirs", KIND_RAW, blob))
        print(f"  mapdirs: {len(recs)} maps, {len(blob)} B")
    p = os.path.join(dbc, "areatrigger.dbc")
    if os.path.exists(p):
        rows, _ = read_dbc(p)
        by_map: dict[int, list[bytes]] = {}
        for r in rows:
            by_map.setdefault(r[1], []).append(struct.pack("<II", r[0], r[1]) + struct.pack("<8I", *r[2:10]))
        for map_id in sorted(by_map):
            out.append((f"at_{map_id}", KIND_RAW, b"".join(by_map[map_id])))
        print(f"  area triggers: {len(rows)} in {len(by_map)} maps")
    return out


def build(root: str, out_path: str, scenes_dir: str | None = None) -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build_ui_pak import write_pak  # noqa: E402 (same folder)
    use_locale_of(root)

    if scenes_dir is None:  # psp_glue_scene_conv's default output (ignored, next to the paks)
        scenes_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(out_path))), "glue_scenes")
    entries, total = loading_screens(root)
    for e in (loading_bar(root) + zones(root) + glue_art(root) + char_select_scenes(scenes_dir) + blue_buttons(root) +
              char_create(root) +
              map_transfer(root)):  # map transfer: mapdirs, at_<map>
        entries.append(e)
        total += len(e[2])
    write_pak(out_path, entries)
    print(f"wrote {out_path}: {len(entries)} entries, {total} B payload, {os.path.getsize(out_path)} B file")
    return 0


def main() -> int:
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracted", required=True, help="asset_extract output root (PNG sidecars + DBFilesClient)")
    ap.add_argument("--out", default=os.path.join(repo, "psp", "bin", "data", "glue.pak"))
    ap.add_argument("--scenes", default=None,
                    help="psp_glue_scene_conv output (cs_scenes.txt + cs_*.png); default psp/bin/glue_scenes")
    args = ap.parse_args()
    return build(args.extracted, args.out, args.scenes)


if __name__ == "__main__":
    sys.exit(main())
