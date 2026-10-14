#!/usr/bin/env python3
"""Builds psp/bin/data/ui.pak - the PSP UI's textures and fonts - from the
player's own extracted 3.3.5a client (never from anything in this repo).

  1. Extract Interface\\ and Fonts\\ with PNG sidecars (upstream asset_extract):
       asset_extract --mpq-dir "<client>/Data" --output <extracted> --expansion wotlk \\
                     --locale ruRU --skip-dbc --emit-png --include "interface\\" --include "fonts\\"
  2. python tools/psp/build_ui_pak.py --extracted <extracted> [--out psp/bin/data/ui.pak]

Client art stays out of git: the pak is written under psp/bin/ (ignored).
Needs Pillow + numpy. Pack/texture/font layouts are documented in
include/platform/psp/psp_pak.hpp, psp_texture.hpp and include/ui/psp/ui_font.hpp.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_texconv as tc  # noqa: E402

KIND_TEXTURE = 1
KIND_FONT = 2

# Characters baked into every font: ASCII, Cyrillic (ruRU client), and the
# typographic marks Russian UI text uses.
CHARSET = (
    [chr(c) for c in range(0x20, 0x7F)]
    + [chr(c) for c in range(0x410, 0x450)]
    + ["–", "’", "“", "„", "·"]  # en dash (QUEST_MONSTERS_KILLED), quotes, middle dot
    + ["Ё", "ё", "«", "»", "—", "…", "№", " "]
    # The Latin-1 letters of the client languages written with marks (deDE frFR esES esMX:
    # ä ö ü ß é è ê ç ñ á í ó ú ...) and a few marks (¡ ¿ ª º °, French Œ œ) - +13 KB of
    # resident fonts. A letter the TTF lacks comes from the client's arialn.ttf (build_font); in neither: "?".
    + [chr(c) for c in range(0xC0, 0x100)]
    + ["¡", "¿", "°", "ª", "º", "Œ", "œ"]
)

# ---------------------------------------------------------------------------
# What goes into ui.pak. Paths are relative to the extracted root, lowercase
# as asset_extract writes them.
# ---------------------------------------------------------------------------
TEXTURES = [
    # name, path, kind, options
    ("bg_login", "interface/glues/loadingscreens/loadscreennorthrendwide.png", "image",
     dict(size=(512, 256), fmt="t8", linear=True)),
    ("logo_wotlk", "interface/glues/common/glues-wow-wotlklogo.png", "image",
     dict(scale=0.5, fmt="4444", linear=True)),
    ("dlg_border", "interface/dialogframe/ui-dialogbox-border.png", "edge",
     dict(edge=12)),
    ("dlg_bg", "interface/dialogframe/ui-dialogbox-background.png", "image",
     dict(scale=0.5, fmt="4444")),
    ("tip_border", "interface/glues/common/glue-tooltip-border.png", "edge",
     dict(edge=8)),
    ("tip_bg", "interface/glues/common/glue-tooltip-background.png", "image",
     dict(scale=0.5, fmt="4444")),
    # Glue buttons use texcoords (0,0)-(0.578,0.75) of a 256x64 image.
    ("btn_up", "interface/glues/common/glue-panel-button-up.png", "button",
     dict(crop=(0, 0, 148, 48), scale=0.5, cap=10)),
    ("btn_down", "interface/glues/common/glue-panel-button-down.png", "button",
     dict(crop=(0, 0, 148, 48), scale=0.5, cap=10)),
    ("btn_hl", "interface/glues/common/glue-panel-button-highlight.png", "button",
     dict(crop=(0, 0, 148, 48), scale=0.5, cap=10)),
    ("cursor_point", "interface/cursor/point.png", "image", dict(scale=0.75, fmt="4444")),
    ("icon_sword", "interface/icons/inv_sword_04.png", "image", dict(size=(32, 32), fmt="4444", linear=True)),
    ("icon_holybolt", "interface/icons/spell_holy_holybolt.png", "image", dict(size=(32, 32), fmt="4444", linear=True)),
    ("icon_fireball", "interface/icons/spell_fire_flamebolt.png", "image", dict(size=(32, 32), fmt="4444", linear=True)),
    ("icon_frostbolt", "interface/icons/spell_frost_frostbolt02.png", "image", dict(size=(32, 32), fmt="4444", linear=True)),
    ("icon_heal", "interface/icons/spell_holy_heal.png", "image", dict(size=(32, 32), fmt="4444", linear=True)),
    ("icon_potion", "interface/icons/inv_potion_54.png", "image", dict(size=(32, 32), fmt="4444", linear=True)),
    ("icon_hearth", "interface/icons/inv_misc_rune_01.png", "image", dict(size=(32, 32), fmt="4444", linear=True)),
    ("icon_mount", "interface/icons/ability_mount_ridinghorse.png", "image", dict(size=(32, 32), fmt="4444", linear=True)),
    ("icon_border", "interface/buttons/ui-quickslot2.png", "image", dict(size=(48, 48), fmt="4444", linear=True)),
] + [
    # Combat - unit frames (src/ui/psp/ui_world_target.cpp). TargetFrame.xml
    # uses texcoords (0.09375, 0)-(1, 0.78125) of the 256x128 image = 232x100;
    # drawn at 0.6 (139x60). The player frame is the same art mirrored
    # (PlayerFrame.xml). Optional: without them the PSP draws plain fills.
    ("uf_target", "interface/targetingframe/ui-targetingframe.png", "image",
     dict(crop=(24, 0, 256, 100), size=(139, 60), fmt="4444", linear=True, optional=True)),
    ("uf_player", "interface/targetingframe/ui-targetingframe.png", "image",
     dict(crop=(24, 0, 256, 100), size=(139, 60), fmt="4444", linear=True, flip=True, optional=True)),
    ("uf_bar", "interface/targetingframe/ui-statusbar.png", "image",
     dict(size=(64, 16), fmt="4444", linear=True, optional=True)),
    # controls: PlayerAttackIcon = UI-StateIcon texcoords (0.5, 0)-(1, 0.5) of 64x64 (crossed swords).
    ("uf_state_combat", "interface/characterframe/ui-stateicon.png", "image",
     dict(crop=(32, 0, 64, 32), size=(32, 32), fmt="4444", linear=True, optional=True)),
] + [
    # TargetFrame / PlayerFrame details (src/ui/psp/ui_world_target.cpp). Optional.
    # TargetFrame_CheckClassification: the whole frame with the dragon, same texcoords as uf_target
    # (T8: 17 KB each instead of 32). TargetFrameNameBackground = UI-TargetingFrame-LevelBackground
    # (tinted UnitSelectionColor), TargetFrameTextureFrameHighLevelTexture = the "??" skull.
    (f"uf_target_{n}", f"interface/targetingframe/ui-targetingframe-{src}.png", "image",
     dict(crop=(24, 0, 256, 100), size=(139, 60), fmt="t8", linear=True, optional=True))
    for n, src in (("elite", "elite"), ("rare", "rare"), ("rareelite", "rare-elite"))
] + [
    ("uf_namebg", "interface/targetingframe/ui-targetingframe-levelbackground.png", "image",
     dict(size=(64, 16), fmt="4444", linear=True, optional=True)),
    ("uf_skull", "interface/targetingframe/ui-targetingframe-skull.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    # PlayerFrame.xml: PlayerRestIcon = UI-StateIcon (0, .5, 0, .421875), PlayerRestGlow (0, .5, .5, 1)
    # and PlayerAttackGlow (.5, 1, .5, 1) (ADD); PlayerStatusTexture = UI-Player-Status
    # (0, .74609375, 0, .53125), ADD, black = nothing; PlayerAttackBackground.
    ("uf_state_rest", "interface/characterframe/ui-stateicon.png", "image",
     dict(crop=(0, 0, 32, 27), size=(32, 27), fmt="4444", linear=True, optional=True)),
    ("uf_glow_rest", "interface/characterframe/ui-stateicon.png", "image",
     dict(crop=(0, 32, 32, 64), size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("uf_glow_combat", "interface/characterframe/ui-stateicon.png", "image",
     dict(crop=(32, 32, 64, 64), size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("uf_status", "interface/characterframe/ui-player-status.png", "image",
     dict(crop=(0, 0, 191, 68), size=(128, 46), fmt="t8", linear=True, optional=True)),
    ("uf_attackbg", "interface/targetingframe/ui-targetingframe-attackbackground.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
] + [
    # Combat - player portraits in the frames' round hole (the
    # client's fallback portraits; masked round here). Optional.
    (f"uf_port_{race}_{sex}", f"interface/characterframe/temporaryportrait-{sexname}-{racename}.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, mask="circle", optional=True))
    for race, racename in ((1, "human"), (2, "orc"), (3, "dwarf"), (4, "nightelf"), (5, "scourge"),
                           (6, "tauren"), (7, "gnome"), (8, "troll"), (10, "bloodelf"), (11, "draenei"))
    for sex, sexname in ((0, "male"), (1, "female"))
] + [
    # spells: action bar, cast bar and aura art (src/ui/psp/ui_world_spells.cpp). Optional.
    # ActionButtonTemplate: NormalTexture UI-Quickslot2 (66 around a 36 icon); empty slot
    # UI-Quickslot; ButtonHilight-Square (ADD) for pushed / checked; UI-QuickslotRed for
    # the auto-attack flash. CastingBarFrame: border / flash 256x64, spark 32x32 (ADD).
    # Debuff border: UI-Debuff-Overlays texcoords (0.296875, 0)-(0.5703125, 0.515625),
    # tinted with DebuffTypeColor.
    ("ab_slot", "interface/buttons/ui-quickslot2.png", "image",
     dict(size=(64, 64), fmt="4444", linear=True, optional=True)),
    ("ab_empty", "interface/buttons/ui-quickslot.png", "image",
     dict(size=(64, 64), fmt="4444", linear=True, optional=True)),
    ("ab_hl", "interface/buttons/buttonhilight-square.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("ab_red", "interface/buttons/ui-quickslotred.png", "image",
     dict(size=(64, 64), fmt="4444", linear=True, optional=True)),
    ("cast_border", "interface/castingbar/ui-castingbar-border.png", "image",
     dict(size=(160, 40), fmt="4444", linear=True, optional=True)),
    ("cast_border_s", "interface/castingbar/ui-castingbar-border-small.png", "image",
     dict(size=(128, 32), fmt="4444", linear=True, optional=True)),
    ("cast_spark", "interface/castingbar/ui-castingbar-spark.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("cast_flash", "interface/castingbar/ui-castingbar-flash.png", "image",
     dict(size=(160, 40), fmt="4444", linear=True, optional=True)),
    ("debuff_border", "interface/buttons/ui-debuff-overlays.png", "image",
     dict(crop=(38, 0, 73, 33), size=(32, 32), fmt="4444", linear=True, optional=True)),
] + [
    # Map - minimap cluster art (src/ui/psp/ui_world_minimap.cpp). Optional: without
    # them the PSP draws a dark ring and upstream's chevron arrow. Minimap.xml: MinimapBorder =
    # UI-Minimap-Border texcoords (0.25, 0.125)-(1, 0.875) of the 256 x 256 file = the 192 px
    # ring around the 140 px map (drawn at 104 px around the 76 px PSP disc; offset keys
    # minimapborderdx/dy if the ring's hole is not centred). MinimapArrow: the player arrow
    # (points up), also upstream's world map player marker.
    ("mm_border", "interface/minimap/ui-minimap-border.png", "image",
     dict(crop=(64, 32, 256, 224), size=(128, 128), fmt="4444", linear=True, optional=True)),
    ("mm_arrow", "interface/minimap/minimaparrow.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
] + [
    # PSP button glyphs (Kenney Input Prompts, CC0 - in the repo, tools/psp/art/input_prompts/):
    # UiKit::glyph, e.g. the action bar hotkeys and prompts. Absolute paths: not from <extracted>.
    (f"pad_{name}", os.path.join(os.path.dirname(os.path.abspath(__file__)), "art", "input_prompts", f"{src}.png"),
     "image", dict(tight=True, size=(32, 32), fmt="4444", linear=True, optional=True))
    for name, src in (("square", "playstation_button_color_square"), ("triangle", "playstation_button_color_triangle"),
                      ("circle", "playstation_button_color_circle"), ("cross", "playstation_button_color_cross"),
                      ("up", "playstation_dpad_up"), ("down", "playstation_dpad_down"),
                      ("left", "playstation_dpad_left"), ("right", "playstation_dpad_right"),
                      ("dpad", "playstation_dpad_none"), ("l", "switch_button_l"), ("r", "switch_button_r"),
                      ("select", "playstation3_button_select"), ("start", "playstation3_button_start"),
                      ("stick", "playstation_stick_l"))
]

# The world map of the original - WorldMapFrame.xml / .lua full-size view
# (src/ui/psp/ui_world_map.cpp). The frame (WorldMapFrameTexture1..12 = UI-WorldMap-Top/Middle/Bottom1..4,
# plus the WorldMapFrame_SetFullMapView patches 13..17 and the black line 18) is composed by worldmap_frame()
# below and cut into the parts the map does not cover; the PSP lays it out 4:3 in the middle of the screen,
# as the client does on a wide monitor (BlackoutWorld black at the sides). WM_* = ui_world_map.cpp kWm*.
# Map 1002 x 668 units -> 345 x 230 px (WM_S); the top band (title bar + the dropdown row, 69 units) gets
# 32 px (PSP fonts), the bottom band 10, the sides 3 and 4 px. All optional: without them the window draws
# plain fills. Loaded with the window, freed when it closes.
WM_S = 345.0 / 1002.0
WM_FRAME_W, WM_TOP_H, WM_BOTTOM_H, WM_SIDE_L, WM_SIDE_R, WM_MAP_H = 352, 32, 10, 3, 4, 230
WM_HL_W, WM_HL_H = 63, 42  # WorldMapHighlight: the used 125.25 x 83.5 texels of the 128 x 128 art, at half size
WM_MINI_S = 0.42  # the mini mode's px per unit (ui_world_map.cpp kMiniS)
TEXTURES += [
    # UIDropDownMenuTemplate: CharacterCreate-LabelFrame, its visible box (x 10..117, y 13..51 of 128 x 64)
    # at half size (three slices at run time: caps 8 and 7 texels); the arrow button
    # (UI-ChatIcon-ScrollDown-Up / -Down, HighlightTexture UI-Common-MouseHilight); UIDropDownListTemplate's
    # backdrop UI-DialogBox-Background-Dark (border: the kit's dlg_border); the checked item's UI-CheckBox-Check.
    ("wm_dd", "interface/glues/charactercreate/charactercreate-labelframe.png", "image",
     dict(crop=(10, 13, 117, 51), size=(54, 19), fmt="4444", linear=True, optional=True)),
    ("wm_dd_btn", "interface/chatframe/ui-chaticon-scrolldown-up.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("wm_dd_btn_dn", "interface/chatframe/ui-chaticon-scrolldown-down.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("wm_dd_hl", "interface/buttons/ui-common-mousehilight.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("wm_dd_bg", "interface/dialogframe/ui-dialogbox-background-dark.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("wm_dd_check", "interface/buttons/ui-checkbox-check.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    # WorldMapCorpseTemplate (WorldMapCorpse, WorldMapDeathRelease): Interface\Minimap\POIIcons TexCoords
    # 0.56640625..0.6328125 x 0.00390625..0.0703125; WorldMapPartyUnitTemplate: WorldMapPartyIcon.
    ("wm_corpse", "interface/minimap/poiicons.png", "image",
     dict(crop=(145, 1, 162, 18), size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("wm_party", "interface/worldmap/worldmappartyicon.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    # WorldMapFrameSizeDownButton / SizeUpButton (UI-Panel-SmallerButton / -BiggerButton, Up and Down)
    # and their UI-Panel-MinimizeButton-Highlight (ADD).
    ("wm_smaller", "interface/buttons/ui-panel-smallerbutton-up.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("wm_smaller_dn", "interface/buttons/ui-panel-smallerbutton-down.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("wm_bigger", "interface/buttons/ui-panel-biggerbutton-up.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("wm_bigger_dn", "interface/buttons/ui-panel-biggerbutton-down.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("wm_size_hl", "interface/buttons/ui-panel-minimizebutton-highlight.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
]


def worldmap_frame(root: str) -> list[tuple[str, int, bytes]]:
    """World map: "wm_top" / "wm_bottom" / "wm_sides" - WorldMapFrame's frame art, composed at its
    1024 x 768 units (Texture1..12 on a 256 grid, the full-map view's patches 13..17 at their XML anchors, the
    690 x 4 black line 18) and cut: the top band y 0..69 (title bar + dropdown row), the bottom band y 737..768,
    the sides x 0..10 / 1012..1024 beside the map (y 69..737), T8. "wm_font" - WorldMapTextFont
    (SystemFont_OutlineThick_WTF, FRIZQT 62 units = 21 px at WM_S) at 20 px for WorldMapFrameAreaLabel.
    "wm_conts" (raw) - GetMapContinents in WorldMapContinent order (map 1, 0, 530, 571) with the client's
    Map.dbc names, "<map>\\t<name>" lines. "wmhl_<folder>" - every zone's WorldMapHighlight
    (Interface\\WorldMap\\<Zone>\\<Zone>Highlight, added: ADD), ordered by continent so one read takes a
    continent's set. Optional parts are skipped."""
    out: list[tuple[str, int, bytes]] = []
    w = "interface/worldmap/"
    tiles = [f"ui-worldmap-{p}{i}" for p in ("top", "middle", "bottom") for i in range(1, 5)]
    if all(os.path.exists(os.path.join(root, w + t + ".png")) for t in tiles):
        c = Image.new("RGBA", (1024, 768), (0, 0, 0, 255))
        for k, t in enumerate(tiles):
            c.alpha_composite(_part(root, w + t + ".png", size=(256, 256)), ((k % 4) * 256, (k // 4) * 256))
        # WorldMapFrame_SetFullMapView shows Texture13..18 (anchors: Texture9 +4,-13; Texture11 +188,-220;
        # Texture12 +220,-201; Texture3 +187,-59; Texture4 +220,-62; Texture9 +12,-223 black 690 x 4).
        for name, x, y, s in (("bottom1-full", 4, 525, 16), ("bottom3-full", 700, 732, 16),
                              ("bottom4-full", 988, 713, 32), ("top3-full", 699, 59, 16), ("top4-full", 988, 62, 32)):
            p = w + f"ui-worldmap-{name}.png"
            if os.path.exists(os.path.join(root, p)):
                c.alpha_composite(_part(root, p, size=(s, s)), (x, y))
        ImageDraw.Draw(c).rectangle((12, 735, 12 + 690 - 1, 735 + 4 - 1), fill=(0, 0, 0, 255))
        top = c.crop((0, 0, 1024, 69)).resize((WM_FRAME_W, WM_TOP_H), Image.LANCZOS)
        bottom = c.crop((0, 737, 1024, 768)).resize((WM_FRAME_W, WM_BOTTOM_H), Image.LANCZOS)
        sides = Image.new("RGBA", (WM_SIDE_L + WM_SIDE_R + 1, WM_MAP_H), (0, 0, 0, 255))
        sides.paste(c.crop((0, 69, 10, 737)).resize((WM_SIDE_L, WM_MAP_H), Image.LANCZOS), (0, 0))
        sides.paste(c.crop((1012, 69, 1024, 737)).resize((WM_SIDE_R, WM_MAP_H), Image.LANCZOS), (WM_SIDE_L + 1, 0))
        for name, img in (("wm_top", top), ("wm_bottom", bottom), ("wm_sides", sides)):
            out.append((name, KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    if os.path.exists(os.path.join(root, "fonts/frizqt__.ttf")):
        out.append(("wm_font", KIND_FONT, build_font(root, "fonts/frizqt__.ttf", 20)))
    dbc = os.path.join(root, "dbfilesclient")
    if os.path.exists(os.path.join(dbc, "map.dbc")) and os.path.exists(os.path.join(dbc, "worldmaparea.dbc")):
        import build_glue_pak  # noqa: E402 (same folder: read_dbc, the ruRU slot)
        maps, mstr = build_glue_pak.read_dbc(os.path.join(dbc, "map.dbc"))
        mname = {r[0]: mstr(r[5 + build_glue_pak.RU]) or mstr(r[5]) or mstr(r[1]) for r in maps}
        lines = "".join(f"{m}\t{mname.get(m, '')}\n" for m in (1, 0, 530, 571))
        out.append(("wm_conts", 3, lines.encode("utf-8")))
        # WorldMapArea: id, map, area, folder, l, r, t, b, displayMapID, defaultDungeonFloor, parentWorldMapID.
        wma, wstr = build_glue_pak.read_dbc(os.path.join(dbc, "worldmaparea.dbc"))
        order = {1: 0, 0: 1, 530: 2, 571: 3}
        hl = []
        for r in wma:
            folder = wstr(r[3])
            if r[2] == 0 or not folder:
                continue  # continents: their highlight is for the world view (not baked)
            disp = struct.unpack("<i", struct.pack("<I", r[8]))[0]
            cont = disp if disp >= 0 else r[1]
            p = w + f"{folder.lower()}/{folder.lower()}highlight.png"
            if os.path.exists(os.path.join(root, p)):
                hl.append((order.get(cont, 9), cont, folder.lower(), p))
        for _, _, folder, p in sorted(set(hl)):
            img = _part(root, p).convert("RGB")
            if max(v for band in img.getextrema() for v in band) <= 2:
                continue  # all black: nothing to add (the cities)
            img = img.crop((0, 0, 125, 84)).resize((WM_HL_W, WM_HL_H), Image.LANCZOS)
            out.append((f"wmhl_{folder}", KIND_TEXTURE, tc.wtex_blob(_hl_t8(img))))
    # The mini mode (WorldMap_ToggleSizeDown): UI-WorldMapSmall-Left (512) + -Right (128) = the 640 x 512
    # border, its drawn part 593 x 440, at WM_MINI_S (ui_world_map.cpp kMiniS), T8.
    l, r = w + "ui-worldmapsmall-left.png", w + "ui-worldmapsmall-right.png"
    if os.path.exists(os.path.join(root, l)) and os.path.exists(os.path.join(root, r)):
        c = Image.new("RGBA", (640, 512), (0, 0, 0, 0))
        c.alpha_composite(_part(root, l), (0, 0))
        c.alpha_composite(_part(root, r), (512, 0))
        c = c.crop((0, 0, 593, 440)).resize((round(593 * WM_MINI_S), round(440 * WM_MINI_S)), Image.LANCZOS)
        out.append(("wm_mini", KIND_TEXTURE, tc.wtex_blob(tc.encode(c, "t8", linear=True))))
    return out


def _hl_t8(img: Image.Image) -> tc.Encoded:
    """A highlight as T8 with a median-cut palette: the glow is dark (RGB up to ~0.45, added), and the shared
    octree quantizer (tc.encode) keeps only ~6 levels of it - visible bands. Black pads the storage."""
    q = img.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = (q.getpalette() or [])[: 255 * 3]
    pal += [0, 0, 0] * (256 - len(pal) // 3)  # index 255 (and any unused) = black: the padding
    w, h = tc.pow2_at_least(img.width, 16), tc.pow2_at_least(img.height, 8)
    idx = np.full((h, w), 255, dtype=np.uint8)
    idx[: img.height, : img.width] = np.asarray(q, dtype=np.uint8)
    clut = b"".join(struct.pack("<4B", pal[3 * i], pal[3 * i + 1], pal[3 * i + 2], 255) for i in range(256))
    return tc.Encoded(tc.PSM_T8, w, h, img.width, img.height, tc.swizzle(idx.tobytes(), w, h), clut, tc.FLAG_LINEAR)


# Taxi: TaxiFrame.xml of the original (src/ui/psp/ui_world_taxi.cpp). The frame
# (UI-TaxiFrame-TopLeft / TopRight / BotLeft / BotRight, 384 x 512 units, drawn part 350 x 440) is composed by
# taxi_frame() and cut into the four pieces around TaxiMap (316 x 352 at 21, 75) - the map covers the rest - at
# TAXI_S (GossipFrame's 0.6: 210 x 264 px). The node buttons (TaxiButtonTypes: Green = CURRENT, White = REACHABLE,
# Yellow = DISTANT; 16 x 16 units), their HighlightTexture (32 x 32) and the route line (UI-Taxi-Line, drawn
# rotated). The TaxiMap pictures and the flight data are tools/psp/build_taxi_pak.py (data/taxi.pak). Optional;
# loaded with the window, freed when it closes.
TAXI_S = 0.6
TAXI_MAP = (21, 75, 316, 352)  # TaxiMap: TOP -13,-75 of the 384-wide frame
TEXTURES += [
    ("taxi_ic_cur", "interface/taxiframe/ui-taxi-icon-green.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("taxi_ic_reach", "interface/taxiframe/ui-taxi-icon-white.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("taxi_ic_far", "interface/taxiframe/ui-taxi-icon-yellow.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("taxi_ic_hl", "interface/taxiframe/ui-taxi-icon-highlight.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("taxi_line", "interface/taxiframe/ui-taxi-line.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
]


def taxi_frame(root: str) -> list[tuple[str, bytes]]:
    """Taxi: "taxi_top" (y 0..75: the portrait ring, the title bar), "taxi_left" / "taxi_right" (beside the
    map), "taxi_bottom" (y 427..440) - TaxiFrame's quarters composed at 384 x 512 units and cut around TaxiMap,
    each piece at TAXI_S (rounded px), T8. Optional."""
    d = "interface/taxiframe/"
    parts = {k: d + f"ui-taxiframe-{k}.png" for k in ("topleft", "topright", "botleft", "botright")}
    if not all(os.path.exists(os.path.join(root, v)) for v in parts.values()):
        return []
    full = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
    full.alpha_composite(_part(root, parts["topleft"], size=(256, 256)), (0, 0))
    full.alpha_composite(_part(root, parts["topright"], size=(128, 256)), (256, 0))
    full.alpha_composite(_part(root, parts["botleft"], size=(256, 256)), (0, 256))
    full.alpha_composite(_part(root, parts["botright"], size=(128, 256)), (256, 256))
    mx, my, mw, mh = TAXI_MAP
    out = []
    for name, box in (("taxi_top", (0, 0, 350, my)), ("taxi_left", (0, my, mx, my + mh)),
                      ("taxi_right", (mx + mw, my, 350, my + mh)), ("taxi_bottom", (0, my + mh, 350, 440))):
        w = max(1, round((box[2] - box[0]) * TAXI_S))
        h = max(1, round((box[3] - box[1]) * TAXI_S))
        img = full.crop(box).resize((w, h), Image.LANCZOS)
        out.append((name, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    return out
# taxi (end)

# The action bar = MainMenuBar.xml of the original (src/ui/psp/
# ui_world_mainmenubar.cpp). One 256 x 64 T8 atlas "mmb_art" at 0.5 (MainMenuBar units -> px: the
# ActionButtons' 36 units = 18 px, 42 apart = 21 px), drawn 1:1 (nearest, no atlas bleeding):
#   MainMenuBarTexture0 / 1   UI-MainMenuBar-Dwarf texcoords 0.83203125-1 / 0.58203125-0.75: the twelve
#                             ActionButton slots (2 x 256 x 43 units) -> 2 x 128 x 22 (0,0) (0,22)
#   MainMenuXPBarTexture0 / 1 0.79296875-0.83203125 / 0.54296875-0.58203125 (2 x 256 x 10) -> (0,44) (0,49)
#   MainMenuMaxLevelBar0 / 1  UI-MainMenuBar-MaxLevel 0-0.21875 / 0.25-0.46875 (2 x 256 x 7) -> (0,54) (0,58)
#   MainMenuBarLeftEndCap     UI-MainMenuBar-EndCap-Dwarf (the gryphon; 128 x 128, only its drawn rows
#                             52..127) -> 64 x 38 (128,0); RightEndCap = the same, TexCoords 1,0 (mirrored) (192,0)
#   ExhaustionTickNormal / Highlight (32 x 32) -> 16 x 16 (128,40) (144,40)
# Positions: kMmb* in ui_world_mainmenubar.cpp. The PSP bar is the twelve buttons only (no micro buttons,
# bags, page arrows: MainMenuBarTexture2/3), so the right cap ends the bar as the left one starts it.
def mainmenubar_art(root: str) -> list[tuple[str, bytes]]:
    m = "interface/mainmenubar/"
    need = {k: m + f"ui-mainmenubar-{k}.png" for k in ("dwarf", "endcap-dwarf", "maxlevel")}
    if not all(os.path.exists(os.path.join(root, v)) for v in need.values()):
        return []
    bar = Image.open(os.path.join(root, need["dwarf"])).convert("RGBA")
    cap = Image.open(os.path.join(root, need["endcap-dwarf"])).convert("RGBA")
    mx = Image.open(os.path.join(root, need["maxlevel"])).convert("RGBA")
    img = Image.new("RGBA", (256, 64), (0, 0, 0, 0))

    def put(src: Image.Image, box: tuple, size: tuple, at: tuple) -> None:
        img.alpha_composite(src.crop(box).resize(size, Image.LANCZOS), at)

    put(bar, (0, 213, 256, 256), (128, 22), (0, 0))   # MainMenuBarTexture0
    put(bar, (0, 149, 256, 192), (128, 22), (0, 22))  # MainMenuBarTexture1
    put(bar, (0, 203, 256, 213), (128, 5), (0, 44))   # MainMenuXPBarTexture0
    put(bar, (0, 139, 256, 149), (128, 5), (0, 49))   # MainMenuXPBarTexture1
    put(mx, (0, 0, 256, 7), (128, 4), (0, 54))        # MainMenuMaxLevelBar0
    put(mx, (0, 8, 256, 15), (128, 4), (0, 58))       # MainMenuMaxLevelBar1
    capv = cap.crop((0, 52, 128, 128)).resize((64, 38), Image.LANCZOS)
    img.alpha_composite(capv, (128, 0))                                  # MainMenuBarLeftEndCap
    img.alpha_composite(capv.transpose(Image.FLIP_LEFT_RIGHT), (192, 0))  # MainMenuBarRightEndCap
    for i, k in enumerate(("normal", "highlight")):
        rel = m + f"ui-exhaustiontick{k}.png"
        if os.path.exists(os.path.join(root, rel)):
            img.alpha_composite(_part(root, rel, size=(16, 16)), (128 + 16 * i, 40))
    return [("mmb_art", tc.wtex_blob(tc.encode(img, "t8", linear=False)))]

# The loot window of the original (LootFrame.xml, src/ui/psp/ui_world_loot.cpp): the
# UI-LootPanel parchment (256 x 256 frame, drawn at 0.72), the dead-target skull over its portrait
# circle, the item name plate (UI-QuestItemNameFrame, 130 x 62 in the frame), the close button and
# the page arrows. The item button frame is ab_slot (UI-Quickslot2). Optional: without them the
# window falls back to a plain panel.
TEXTURES += [
    ("loot_panel", "interface/lootframe/ui-lootpanel.png", "image",
     dict(size=(256, 256), fmt="t8", linear=True, optional=True)),
    ("loot_dead", "interface/targetingframe/targetdead.png", "image",
     dict(size=(48, 48), fmt="4444", linear=True, optional=True)),
    ("loot_namefr", "interface/questframe/ui-questitemnameframe.png", "image",
     dict(size=(128, 64), fmt="4444", linear=True, optional=True)),
    ("loot_close", "interface/buttons/ui-panel-minimizebutton-up.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("loot_close_dn", "interface/buttons/ui-panel-minimizebutton-down.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("loot_up", "interface/chatframe/ui-chaticon-scrollup-up.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("loot_down", "interface/chatframe/ui-chaticon-scrolldown-up.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
]

# The GM / DEV flag of a chat line (ChatFrame.lua pflag
# "|TInterface\ChatFrame\UI-ChatIcon-Blizz.blp:0:2:0:-3|t ", src/ui/psp/ui_world_chat.cpp), 32 x 16,
# 1 KB, loaded on the first GM line. Optional: without it the flag is CHAT_FLAG_GM "<GM>".
TEXTURES += [
    ("chat_blizz", "interface/chatframe/ui-chaticon-blizz.png", "image",
     dict(size=(32, 16), fmt="4444", linear=True, optional=True)),
]

# GameTooltip (src/ui/psp/ui_world_tooltip.cpp) - the backdrop edge of
# GameTooltipTemplate / ShoppingTooltipTemplate (Interface\Tooltips\UI-Tooltip-Border, edgeSize 16 -> 8 px; the
# background UI-Tooltip-Background x TOOLTIP_DEFAULT_BACKGROUND_COLOR is a plain fill) and the item tooltip's
# empty socket icons (EMPTY_SOCKET_*: Interface\ItemSocketingFrame\UI-EmptySocket-<colour>, 16 x 16). ~3 KB,
# loaded with the first tooltip, released when the interface mode is left. Optional: without them the glue
# tooltip border and no socket icons.
TEXTURES += [
    ("gt_border", "interface/tooltips/ui-tooltip-border.png", "edge", dict(edge=8, optional=True)),
] + [
    (f"gt_sock_{n}", f"interface/itemsocketingframe/ui-emptysocket{s}.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True))
    for n, s in (("meta", "-meta"), ("red", "-red"), ("yellow", "-yellow"), ("blue", "-blue"), ("none", ""))
]

# ActionButtonTemplate's $parentBorder (Interface\Buttons\UI-ActionButton-Border, 62 x 62
# around the 36 button, ADD, SetVertexColor(0, 1, 0, 0.35)) - the green frame of a worn item on the action bar
# (IsEquippedAction, src/ui/psp/ui_world_item_use.cpp). 32 x 32 4444, 2 KB, loaded with the first worn item button.
# Optional: without it no border.
TEXTURES += [
    ("ab_border", "interface/buttons/ui-actionbutton-border.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
]

# The selection circle under the target (src/ui/psp/ui_world_target.cpp). The client
# draws it itself; upstream uses a shader (assets/shaders/selection_circle.frag.glsl: a ring at
# 0.93..1.0 of the radius and a faint fill). tools/psp/art/selection_circle.png is that shader's
# ring baked by us (a little thicker for the small screen) - no client art. White, tinted by the
# reaction colour at draw time.
TEXTURES += [
    ("sel_circle", os.path.join(os.path.dirname(os.path.abspath(__file__)), "art", "selection_circle.png"), "image",
     dict(size=(64, 64), fmt="4444", linear=True, optional=True)),
]

# The NPC windows of the original (GossipFrame.xml, QuestFrame.xml, src/ui/psp/
# ui_world_npc_frame.cpp): the UI-QuestGreeting frame (composed below as npc_frame), the gossip
# option / quest icons (Interface/GossipFrame/*GossipIcon, *QuestIcon), the title highlight, the
# panel buttons (UIPanelButtonTemplate), the scroll arrows and the money icons (MoneyFrame).
_GOSSIP_ICONS = ("gossip", "vendor", "taxi", "trainer", "healer", "binder", "banker", "petition",
                 "tabard", "battlemaster", "unlearn")
TEXTURES += [
    (f"npc_ic_{n}", f"interface/gossipframe/{n}gossipicon.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)) for n in _GOSSIP_ICONS
] + [
    (f"npc_ic_{n}", f"interface/gossipframe/{n}questicon.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True))
    for n in ("available", "active", "incomplete", "daily", "dailyactive")
] + [
    ("npc_titlehl", "interface/questframe/ui-questtitlehighlight.png", "image",
     dict(size=(128, 16), fmt="4444", linear=True, optional=True)),
    ("pbtn_up", "interface/buttons/ui-panel-button-up.png", "image",
     dict(crop=(0, 0, 80, 22), size=(64, 16), fmt="4444", linear=True, optional=True)),
    ("pbtn_dn", "interface/buttons/ui-panel-button-down.png", "image",
     dict(crop=(0, 0, 80, 22), size=(64, 16), fmt="4444", linear=True, optional=True)),
    ("pbtn_dis", "interface/buttons/ui-panel-button-disabled.png", "image",
     dict(crop=(0, 0, 80, 22), size=(64, 16), fmt="4444", linear=True, optional=True)),
    ("pbtn_hl", "interface/buttons/ui-panel-button-highlight.png", "image",
     dict(crop=(0, 0, 80, 22), size=(64, 16), fmt="4444", linear=True, optional=True)),
    ("scr_up", "interface/buttons/ui-scrollbar-scrollupbutton-up.png", "image",
     dict(crop=(6, 7, 26, 25), size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("scr_dn", "interface/buttons/ui-scrollbar-scrolldownbutton-up.png", "image",
     dict(crop=(6, 7, 26, 25), size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("scr_up_dis", "interface/buttons/ui-scrollbar-scrollupbutton-disabled.png", "image",
     dict(crop=(6, 7, 26, 25), size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("scr_dn_dis", "interface/buttons/ui-scrollbar-scrolldownbutton-disabled.png", "image",
     dict(crop=(6, 7, 26, 25), size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("money_icons", "interface/moneyframe/ui-moneyicons.png", "image",
     dict(size=(64, 16), fmt="4444", linear=True, optional=True)),
]


def npc_frame(root: str) -> list[tuple[str, bytes]]:
    """GossipFrame / QuestFrame's UI-QuestGreeting-TopLeft/TopRight/BotLeft/BotRight
    (256+128 x 256+256 = 384 x 512 frame units) composed, cut to the drawn part (384 x 448) and
    baked at 0.6 (230 x 269, the PSP screen is 272 high) as T8 "npc_frame". Optional."""
    d = os.path.join(root, "interface", "questframe")
    parts = {k: os.path.join(d, f"ui-questgreeting-{k}.png") for k in ("topleft", "topright", "botleft", "botright")}
    if not all(os.path.exists(v) for v in parts.values()):
        return []
    full = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
    full.alpha_composite(Image.open(parts["topleft"]).convert("RGBA").resize((256, 256)), (0, 0))
    full.alpha_composite(Image.open(parts["topright"]).convert("RGBA").resize((128, 256)), (256, 0))
    full.alpha_composite(Image.open(parts["botleft"]).convert("RGBA").resize((256, 256)), (0, 256))
    full.alpha_composite(Image.open(parts["botright"]).convert("RGBA").resize((128, 256)), (256, 256))
    img = full.crop((0, 0, 384, 448)).resize((230, 269), Image.LANCZOS)
    return [("npc_frame", tc.wtex_blob(tc.encode(img, "t8", linear=True)))]


# MerchantFrame.xml and QuestLogFrame.xml of the original (src/ui/psp/ui_world_merchant.cpp,
# ui_world_questlog.cpp). The frames are composed here from their FrameXML layout - the frame
# quarters, the item slots (UI-EmptySlot + UI-Merchant-LabelSlots), the bottom border, the tabs
# (CharacterFrameTabButtonTemplate) - so the PSP draws one texture plus the dynamic parts (icons,
# names, prices, focus). The small dynamic art below. Optional: without them the old windows.
TEXTURES += [
    ("merch_repair", "interface/merchantframe/ui-merchant-repairicons.png", "image",
     dict(crop=(0, 0, 72, 36), size=(64, 32), fmt="4444", linear=True, optional=True)),
    ("merch_bbicon", "interface/merchantframe/ui-buyback-icon.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("ql_plus", "interface/buttons/ui-plusbutton-up.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("ql_minus", "interface/buttons/ui-minusbutton-up.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("ql_titlehl", "interface/questframe/ui-questlogtitlehighlight.png", "image",
     dict(size=(64, 16), fmt="4444", linear=True, optional=True)),
]

# MerchantFrame: 384 x 512 frame units; the drawn part is x -4 (tab 1 sticks out) .. 349 (HitRectInsets
# right 35), y 0 .. 482 (the tabs' bottom). Baked at 0.6 x 0.55 - 212 x 265 px: the PSP screen is 272 high
# and the frame keeps its width for the item names (ui_world_merchant.cpp uses the same numbers).
MERCH_SX, MERCH_SY, MERCH_X0, MERCH_W, MERCH_H = 0.6, 0.55, -4, 353, 482
# QuestLogFrame: UI-QuestLogDualPane-Left/-Right (512 + 170 x 445 units), baked at 0.57 = 389 x 254 px.
QLOG_S, QLOG_W, QLOG_H = 0.57, 682, 445


def _part(root: str, rel: str, size=None, crop=None) -> Image.Image:
    im = Image.open(os.path.join(root, rel)).convert("RGBA")
    if crop:
        im = im.crop(crop)
    if size:
        im = im.resize(size, Image.LANCZOS)
    return im


def merchant_frames(root: str) -> list[tuple[str, bytes]]:
    """Merchant: "merch_frame" (the merchant tab: 10 item slots, the buyback slot, the bottom
    border, tab 1 up) and "merch_buyback" (the buyback tab: UI-BuyBack quarters, 12 slots, tab 2
    up), each 212 x 265 T8. Positions from MerchantFrame.xml / MerchantFrame_UpdateBuybackInfo."""
    m, b = "interface/merchantframe/", "interface/buttons/"
    pd = "interface/paperdollinfoframe/"
    need = [m + f"ui-merchant-{k}.png" for k in ("topleft", "topright", "botleft", "botright", "bottomborder",
                                                 "labelslots")]
    need += [m + f"ui-buyback-{k}.png" for k in ("topleft", "topright", "botleft", "botright")]
    need += [b + "ui-emptyslot.png", pd + "ui-character-activetab.png", pd + "ui-character-inactivetab.png"]
    if not all(os.path.exists(os.path.join(root, r)) for r in need):
        return []
    out = []
    for tab in (1, 2):
        c = Image.new("RGBA", (400, 560), (0, 0, 0, 0))

        def put(im: Image.Image, x: int, y: int) -> None:
            c.alpha_composite(im, (x - MERCH_X0, y))

        put(_part(root, m + "ui-merchant-topleft.png"), 0, 0)
        put(_part(root, m + "ui-merchant-topright.png"), 256, 0)
        put(_part(root, m + "ui-merchant-botleft.png"), 0, 256)
        put(_part(root, m + "ui-merchant-botright.png"), 256, 256)
        slot = _part(root, b + "ui-emptyslot.png")
        label = _part(root, m + "ui-merchant-labelslots.png", size=(128, 78))
        if tab == 1:
            border = m + "ui-merchant-bottomborder.png"
            put(_part(root, border, crop=(0, 0, 256, 61)), 14, 366)    # BOTTOMLEFT 14,85; TexCoords 0..0.4766
            put(_part(root, border, crop=(0, 61, 76, 122)), 270, 366)  # its RIGHT; 0..0.2969 x 0.4766..0.9531
            for i in range(10):  # MerchantItem1..10: TOPLEFT 24,-80, 165 apart, rows 52 apart
                x, y = 24 + (i % 2) * 165, 80 + (i // 2) * 52
                put(slot, x - 13, y - 13)
                put(label, x + 42, y - 2)
            put(slot, 189 - 13, 385 - 13)  # MerchantBuyBackItem: Item10's BOTTOMLEFT 0,-53
            put(_part(root, m + "ui-merchant-labelslots.png", size=(128, 64)), 231, 382)
        else:
            put(_part(root, m + "ui-buyback-topleft.png"), 19, 71)
            put(_part(root, m + "ui-buyback-topright.png"), 275, 71)
            put(_part(root, m + "ui-buyback-botleft.png"), 19, 327)
            put(_part(root, m + "ui-buyback-botright.png"), 275, 327)
            for i in range(12):  # rows 59 apart (BOTTOMLEFT 0,-15)
                x, y = 24 + (i % 2) * 165, 80 + (i // 2) * 59
                put(slot, x - 13, y - 13)
                put(label, x + 42, y - 2)
        # Tabs: Tab1 CENTER at BOTTOMLEFT 60,46 (128 x 32), Tab2 its RIGHT -16; the selected one is
        # ActiveTab raised 5 (the template's "Disabled" textures).
        for t, cx in ((1, 60), (2, 60 + 64 + 64 - 16)):
            act = t == tab
            im = _part(root, pd + ("ui-character-activetab.png" if act else "ui-character-inactivetab.png"))
            put(im, cx - 64, 466 - 16 - (5 if act else 0))
        img = c.crop((0, 0, MERCH_W, MERCH_H)).resize((round(MERCH_W * MERCH_SX), round(MERCH_H * MERCH_SY)),
                                                      Image.LANCZOS)
        out.append(("merch_frame" if tab == 1 else "merch_buyback", tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    # The page buttons (32 x 32: UI-PageButton-Background under the spell book arrows), up / disabled.
    bg_rel = b + "ui-pagebutton-background.png"
    for name, rel in (("merch_prev", "ui-spellbookicon-prevpage-up"),
                      ("merch_prev_dis", "ui-spellbookicon-prevpage-disabled"),
                      ("merch_next", "ui-spellbookicon-nextpage-up"),
                      ("merch_next_dis", "ui-spellbookicon-nextpage-disabled")):
        if not os.path.exists(os.path.join(root, b + rel + ".png")):
            continue
        im = _part(root, bg_rel) if os.path.exists(os.path.join(root, bg_rel)) else Image.new("RGBA", (32, 32))
        im.alpha_composite(_part(root, b + rel + ".png", size=im.size))
        out.append((name, tc.wtex_blob(tc.encode(im.resize((32, 32), Image.LANCZOS), "4444", linear=True))))
    return out


def questlog_frames(root: str) -> list[tuple[str, bytes]]:
    """Quest log: "qlog_frame" - QuestLogFrame: the book icon (TOPLEFT 3,-4), the dual pane art
    (TexCoords bottom 0.86914 = 445 of 512), the QuestLogCount box (TOPLEFT 80,-41, 120 x 20,
    Common-Input-Border), 389 x 254 T8; "qlog_empty" - EmptyQuestLogFrame (TOPLEFT 19,-73,
    302 x 356). Optional."""
    q = "interface/questframe/"
    need = [q + "ui-questlogdualpane-left.png", q + "ui-questlogdualpane-right.png"]
    if not all(os.path.exists(os.path.join(root, r)) for r in need):
        return []
    c = Image.new("RGBA", (QLOG_W, QLOG_H), (0, 0, 0, 0))
    if os.path.exists(os.path.join(root, q + "ui-questlog-bookicon.png")):
        c.alpha_composite(_part(root, q + "ui-questlog-bookicon.png"), (3, 4))
    c.alpha_composite(_part(root, need[0], crop=(0, 0, 512, 445)), (0, 0))
    c.alpha_composite(_part(root, need[1], crop=(0, 0, 170, 445)), (512, 0))
    box = "interface/common/common-input-border.png"
    if os.path.exists(os.path.join(root, box)):
        src = _part(root, box, crop=(0, 0, 128, 20))
        edge = 8
        piece = Image.new("RGBA", (120, 20), (0, 0, 0, 0))  # left cap, stretched middle, right cap
        piece.alpha_composite(src.crop((0, 0, edge, 20)), (0, 0))
        piece.alpha_composite(src.crop((edge, 0, 128 - edge, 20)).resize((120 - 2 * edge, 20)), (edge, 0))
        piece.alpha_composite(src.crop((128 - edge, 0, 128, 20)), (120 - edge, 0))
        c.alpha_composite(piece, (80, 41))
    out = [("qlog_frame", tc.wtex_blob(tc.encode(
        c.resize((round(QLOG_W * QLOG_S), round(QLOG_H * QLOG_S)), Image.LANCZOS), "t8", linear=True)))]
    parts = {k: os.path.join(root, q + f"ui-questlog-empty-{k}.png") for k in ("topleft", "topright", "botleft",
                                                                              "botright")}
    if all(os.path.exists(v) for v in parts.values()):
        e = Image.new("RGBA", (302, 356), (0, 0, 0, 0))
        e.alpha_composite(Image.open(parts["topleft"]).convert("RGBA"), (0, 0))
        e.alpha_composite(Image.open(parts["topright"]).convert("RGBA").crop((0, 0, 46, 256)), (256, 0))
        e.alpha_composite(Image.open(parts["botleft"]).convert("RGBA").crop((0, 0, 256, 100)), (0, 256))
        e.alpha_composite(Image.open(parts["botright"]).convert("RGBA").crop((0, 0, 46, 100)), (256, 256))
        out.append(("qlog_empty", tc.wtex_blob(tc.encode(
            e.resize((round(302 * QLOG_S), round(356 * QLOG_S)), Image.LANCZOS), "t8", linear=True))))
    return out


# ContainerFrame.xml and CharacterFrame / PaperDollFrame.xml of the original
# (src/ui/psp/ui_world_bags.cpp, ui_world_paperdoll.cpp) and the tab art all the framed windows share
# (CharacterFrameTabButtonTemplate, drawn in three slices by WorldScreen::drawFrameTab).
BAG_S = 0.55  # ContainerFrame units -> px (ui_world_bags.cpp kS)
PD_SX, PD_SY, PD_W, PD_H = 0.6, 0.55, 352, 482  # CharacterFrame units -> px (ui_world_paperdoll.cpp)
# PaperDollItemSlotButton empty textures (GetInventorySlotInfo), in EquipSlot order 0..18 + bag + ammo.
PD_SLOTS = ("head", "neck", "shoulder", "shirt", "chest", "waist", "legs", "feet", "wrists", "hands", "finger",
            "rfinger", "trinket", "trinket", "chest", "mainhand", "secondaryhand", "ranged", "tabard")
TEXTURES += [
    # WatchFrame's collapse / expand button, the quest log's "watched" check.
    ("watch_hide", "interface/buttons/ui-panel-questhidebutton.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("ql_check", "interface/buttons/ui-checkbox-check.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("tab_active", "interface/paperdollinfoframe/ui-character-activetab.png", "image",
     dict(size=(64, 16), fmt="4444", linear=True, optional=True)),
    ("tab_inactive", "interface/paperdollinfoframe/ui-character-inactivetab.png", "image",
     dict(size=(64, 16), fmt="4444", linear=True, optional=True)),
    ("bag_btn_backpack", "interface/buttons/button-backpack-up.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("pd_slot_bag", "interface/paperdoll/ui-paperdoll-slot-bag.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("pd_slot_ammo", "interface/paperdoll/ui-paperdoll-slot-ammo.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("pd_rot_left", "interface/buttons/ui-rotationleft-button-up.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("pd_rot_right", "interface/buttons/ui-rotationright-button-up.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
] + [
    (f"pd_slot_{n}", f"interface/paperdoll/ui-paperdoll-slot-{n}.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True))
    for n in sorted(set(PD_SLOTS))
]


# InspectFrame (the client's InspectUI addon, src/ui/psp/ui_world_inspect.cpp). The empty ranged
# slot of a paladin / shaman / druid / death knight (InspectPaperDollItemSlotButton_Update: UnitHasRelicSlot).
TEXTURES += [
    ("pd_slot_relic", "interface/paperdoll/ui-paperdoll-slot-relic.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
]
# InspectPVPFrame (InspectPVPFrame.xml, PVPFrameTemplates.xml): the pieces of UI-Character-PVP-Elements by their XML
# TexCoords (x 512) - PVPTeamBorderTemplate Top / Bottom / Left / Right, PVPTeamStandardTemplate's Bar, the honour
# block's line (also PVPTeamDataTemplate's) - the arena points icon, the faction icons (UI-PVP-Alliance / -Horde), the
# team banners of the three sizes, their six borders each and the 102 emblems (Icons\PVP-Banner-Emblem-N): drawn in
# the team's colours. InspectTalentFrame's TabButtonTemplate tabs (HelpFrameTab-Active / -Inactive).
_PVP_E = "interface/pvpframe/ui-character-pvp-elements.png"
TEXTURES += [
    ("insp_pvp_top", _PVP_E, "image", dict(crop=(0, 435, 279, 446), size=(256, 8), fmt="4444", linear=True,
                                           optional=True)),
    ("insp_pvp_bottom", _PVP_E, "image", dict(crop=(0, 446, 279, 464), size=(256, 16), fmt="4444", linear=True,
                                              optional=True)),
    ("insp_pvp_left", _PVP_E, "image", dict(crop=(285, 361, 295, 422), size=(8, 32), fmt="4444", linear=True,
                                            optional=True)),
    ("insp_pvp_right", _PVP_E, "image", dict(crop=(295, 361, 305, 422), size=(8, 32), fmt="4444", linear=True,
                                             optional=True)),
    ("insp_pvp_bar", _PVP_E, "image", dict(crop=(0, 466, 51, 479), size=(32, 8), fmt="4444", linear=True,
                                           optional=True)),
    ("insp_pvp_line", _PVP_E, "image", dict(crop=(0, 390, 212, 392), size=(256, 4), fmt="4444", linear=True,
                                            optional=True)),
    ("insp_pvp_arena", "interface/pvpframe/pvp-arenapoints-icon.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("insp_pvp_alliance", "interface/targetingframe/ui-pvp-alliance.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("insp_pvp_horde", "interface/targetingframe/ui-pvp-horde.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("insp_htab_active", "interface/helpframe/helpframetab-active.png", "image",
     dict(size=(64, 32), fmt="4444", linear=True, optional=True)),
    ("insp_htab_inactive", "interface/helpframe/helpframetab-inactive.png", "image",
     dict(size=(64, 32), fmt="4444", linear=True, optional=True)),
] + [
    (f"insp_banner_{n}", f"interface/pvpframe/pvp-banner-{n}.png", "image",
     dict(size=(32, 64), fmt="4444", linear=True, optional=True))
    for n in (2, 3, 5)
] + [
    (f"insp_border_{n}_{b}", f"interface/pvpframe/pvp-banner-{n}-border-{b}.png", "image",
     dict(size=(32, 64), fmt="4444", linear=True, optional=True))
    for n in (2, 3, 5) for b in range(1, 7)
] + [
    (f"insp_emblem_{e}", f"interface/pvpframe/icons/pvp-banner-emblem-{e}.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True))
    for e in range(1, 103)
]


def inspect_frames(root: str) -> list[tuple[str, bytes]]:
    """Inspect: "insp_frame" - InspectPaperDollFrame: UI-Character-CharacterTab-L1/R1/BottomLeft/BottomRight
    (the paperdoll's quarters without its stat blocks and resistance icons - InspectModelFrame is 300 units tall),
    cut like pd_frame (x 0..352, y 0..482) at 0.6 x 0.55, T8."""
    d = "interface/paperdollinfoframe/"
    q = {k: d + f"ui-character-charactertab-{k}.png" for k in ("l1", "r1", "bottomleft", "bottomright")}
    if not all(os.path.exists(os.path.join(root, v)) for v in q.values()):
        return []
    c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
    c.alpha_composite(_part(root, q["l1"]), (0, 0))
    c.alpha_composite(_part(root, q["r1"]), (256, 0))
    c.alpha_composite(_part(root, q["bottomleft"]), (0, 256))
    c.alpha_composite(_part(root, q["bottomright"]), (256, 256))
    img = c.crop((0, 0, PD_W, PD_H)).resize((round(PD_W * PD_SX), round(PD_H * PD_SY)), Image.LANCZOS)
    out = [("insp_frame", tc.wtex_blob(tc.encode(img, "t8", linear=True)))]
    # "insp_pvp" - InspectPVPFrame: UI-Character-General-TopLeft/TopRight/BottomLeft/BottomRight (2,-1 / 258,-1 /
    # 2,-257 / 258,-257) and InspectPVPFrameBackground (UI-Character-PVP, 512 x 512 at 14,-36), cut the same way.
    g = {k: d + f"ui-character-general-{k}.png" for k in ("topleft", "topright", "bottomleft", "bottomright")}
    pvp = "interface/pvpframe/ui-character-pvp.png"
    if all(os.path.exists(os.path.join(root, v)) for v in list(g.values()) + [pvp]):
        c = Image.new("RGBA", (384 + 4, 512 + 4), (0, 0, 0, 0))
        c.alpha_composite(_part(root, g["topleft"]), (2, 1))
        c.alpha_composite(_part(root, g["topright"]), (258, 1))
        c.alpha_composite(_part(root, g["bottomleft"]), (2, 257))
        c.alpha_composite(_part(root, g["bottomright"]), (258, 257))
        big = Image.new("RGBA", (14 + 512, 36 + 512), (0, 0, 0, 0))
        big.alpha_composite(c, (0, 0))
        big.alpha_composite(_part(root, pvp), (14, 36))
        img = big.crop((0, 0, PD_W, PD_H)).resize((round(PD_W * PD_SX), round(PD_H * PD_SY)), Image.LANCZOS)
        out.append(("insp_pvp", tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    return out


def bag_frames(root: str) -> list[tuple[str, bytes]]:
    """Bags: "bag_backpack" (UI-BackpackBackground) and "bag_comps" (UI-Bag-Components: the tops, the
    middle rows, the bottom - sliced at run time by the TexCoords of ContainerFrame_GenerateFrame). The
    textures are 256 wide and anchored TOPRIGHT of the 192-unit frame: only x 64..256 is the frame. Baked at
    BAG_S, T8."""
    c = "interface/containerframe/"
    out = []
    for name, rel, h in (("bag_backpack", c + "ui-backpackbackground.png", 256),
                         ("bag_comps", c + "ui-bag-components.png", 512)):
        if not os.path.exists(os.path.join(root, rel)):
            continue
        img = _part(root, rel, crop=(64, 0, 256, h))
        img = img.resize((round(192 * BAG_S), round(h * BAG_S)), Image.LANCZOS)
        out.append((name, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    return out


def paperdoll_frame(root: str) -> list[tuple[str, bytes]]:
    """Paperdoll: "pd_frame" - PaperDollFrame: UI-Character-CharacterTab-L1/R1/BottomLeft/BottomRight, the two
    stat blocks (CharacterAttributesFrame TOPLEFT 67,-291: UI-Character-StatBackground top / middle / bottom,
    115 wide, the right one beside), the five resistance icons (CharacterResistanceFrame TOPLEFT 265,-77, 32 x 29
    each: arcane, fire, nature, frost, shadow), cut to x 0..352, y 0..482 and baked at 0.6 x 0.55, T8."""
    d = "interface/paperdollinfoframe/"
    q = {k: d + f"ui-character-charactertab-{k}.png" for k in ("l1", "r1", "bottomleft", "bottomright")}
    if not all(os.path.exists(os.path.join(root, v)) for v in q.values()):
        return []
    c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
    c.alpha_composite(_part(root, q["l1"]), (0, 0))
    c.alpha_composite(_part(root, q["r1"]), (256, 0))
    c.alpha_composite(_part(root, q["bottomleft"]), (0, 256))
    c.alpha_composite(_part(root, q["bottomright"]), (256, 256))
    sb = d + "ui-character-statbackground.png"
    if os.path.exists(os.path.join(root, sb)):
        top = _part(root, sb, crop=(0, 0, 115, 16))
        mid = _part(root, sb, crop=(0, 16, 115, 25), size=(115, 53))
        bot = _part(root, sb, crop=(0, 62, 115, 78))
        for x in (67, 67 + 115):
            c.alpha_composite(top, (x, 291))
            c.alpha_composite(mid, (x, 307))
            c.alpha_composite(bot, (x, 360))
    ri = d + "ui-character-resistanceicons.png"
    if os.path.exists(os.path.join(root, ri)):
        for k, row in enumerate((2, 0, 1, 3, 4)):  # MagicResFrame1..5 TexCoords rows (29 px each)
            c.alpha_composite(_part(root, ri, crop=(0, row * 29, 32, row * 29 + 29)), (265, 77 + k * 29))
    img = c.crop((0, 0, PD_W, PD_H)).resize((round(PD_W * PD_SX), round(PD_H * PD_SY)), Image.LANCZOS)
    return [("pd_frame", tc.wtex_blob(tc.encode(img, "t8", linear=True)))]


# Character tabs: CharacterFrame's ReputationFrame.xml and SkillFrame.xml of the original
# (src/ui/psp/ui_world_char_tabs.cpp). The two frames at the paperdoll's 0.6 x 0.55 (PD_*) with the tabs drawn by
# the PSP like pd_frame; the parts that come and go apart (FauxScrollFrame shows its scroll bar only for a long
# list, the skill details' bar only when the text overflows); ReputationDetailFrame - a dialog box (Backdrop
# UI-DialogBox) - at the dialogs' 0.8 (POPUP_S, WorldScreen::kDialogScale); the small dynamic art (the bars, the
# highlights, the check boxes, the unlearn button). All optional: without them the tabs say "rebake ui.pak".
REPD_S, REPD_W, REPD_H = 0.8, 212, 203  # ReputationDetailFrame units -> px (= POPUP_S)
TEXTURES += [
    ("rep_bar", "interface/paperdollinfoframe/ui-character-reputationbar.png", "image",          # rows, bar ends
     dict(size=(128, 32), fmt="4444", linear=True, optional=True)),
    ("rep_bar_hl", "interface/paperdollinfoframe/ui-character-reputationbar-highlight.png", "image",  # Highlight1/2
     dict(size=(128, 32), fmt="4444", linear=True, optional=True)),
    ("skill_bar", "interface/paperdollinfoframe/ui-character-skills-bar.png", "image",         # the StatusBars' fill
     dict(size=(128, 16), fmt="4444", linear=True, optional=True)),
    ("skill_border", "interface/paperdollinfoframe/ui-character-skills-barborder.png", "image",
     dict(size=(128, 16), fmt="4444", linear=True, optional=True)),
    ("skill_border_hl", "interface/paperdollinfoframe/ui-character-skills-barborderhighlight.png", "image",
     dict(size=(128, 16), fmt="4444", linear=True, optional=True)),
] + [
    (name, f"interface/buttons/{f}.png", "image", dict(size=(32, 32), fmt="4444", linear=True, optional=True))
    for name, f in (("cb_up", "ui-checkbox-up"), ("cb_hl", "ui-checkbox-highlight"),
                    ("cb_sword", "ui-checkbox-swordcheck"), ("cb_check_dis", "ui-checkbox-check-disabled"),
                    ("unlearn_btn", "cancelbutton-up"), ("unlearn_btn_dn", "cancelbutton-down"),
                    ("unlearn_btn_hl", "cancelbutton-highlight"))
]


def _backdrop(root: str, w: int, h: int, bg_rel: str, edge_rel: str, insets: tuple[int, int, int, int],
              tile: int = 32, edge: int = 32) -> Image.Image:
    """A FrameXML Backdrop (tile="true"): the background tiled at `tile` units inside the insets (left, right,
    top, bottom), the 8-cell edge file at `edge` units (corners, the edges stretched between them - uniform
    along their length, as the popups' 9-slice)."""
    c = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    l, r, t, b = insets
    if os.path.exists(os.path.join(root, bg_rel)):
        bg = _part(root, bg_rel, size=(tile, tile))
        inner = Image.new("RGBA", (w - l - r, h - t - b), (0, 0, 0, 0))
        for y in range(0, inner.height, tile):
            for x in range(0, inner.width, tile):
                inner.alpha_composite(bg.crop((0, 0, min(tile, inner.width - x), min(tile, inner.height - y))), (x, y))
        c.alpha_composite(inner, (l, t))
    if os.path.exists(os.path.join(root, edge_rel)):
        nine = edge_to_nine(load_rgba(root, edge_rel), edge)
        e = edge
        cell = lambda i, j: nine.crop((i * e, j * e, (i + 1) * e, (j + 1) * e))  # noqa: E731
        c.alpha_composite(cell(1, 0).resize((w - 2 * e, e)), (e, 0))
        c.alpha_composite(cell(1, 2).resize((w - 2 * e, e)), (e, h - e))
        c.alpha_composite(cell(0, 1).resize((e, h - 2 * e)), (0, e))
        c.alpha_composite(cell(2, 1).resize((e, h - 2 * e)), (w - e, e))
        c.alpha_composite(cell(0, 0), (0, 0))
        c.alpha_composite(cell(2, 0), (w - e, 0))
        c.alpha_composite(cell(0, 2), (0, h - e))
        c.alpha_composite(cell(2, 2), (w - e, h - e))
    return c


def reputation_frames(root: str) -> list[tuple[str, bytes]]:
    """Character tabs: "rep_frame" - ReputationFrame: UI-Character-General-TopLeft/TopRight/BottomLeft/BottomRight
    at 2,-1 (the column labels, the rows and the tree lines are drawn by the PSP), cut like pd_frame (352 x 482)
    at 0.6 x 0.55, T8; "rep_scroll" - ReputationListScrollFrame's bar (UI-Character-ScrollBar 31 x 256 at the
    scroll frame's TOPRIGHT -2,5 + 31 x 108 at its BOTTOMRIGHT -2,-4: x 316, y 71..434), T8; "rep_detail" -
    ReputationDetailFrame (212 x 203: the Backdrop, UI-Character-Reputation-DetailBackground at 11,-11,
    UI-DialogBox-Corner at TOPRIGHT -6,-7, UI-DialogBox-Divider at 9,-131) at 0.8, T8."""
    d = "interface/paperdollinfoframe/"
    q = {k: d + f"ui-character-general-{k}.png" for k in ("topleft", "topright", "bottomleft", "bottomright")}
    if not all(os.path.exists(os.path.join(root, v)) for v in q.values()):
        return []
    c = Image.new("RGBA", (384 + 4, 512 + 4), (0, 0, 0, 0))
    c.alpha_composite(_part(root, q["topleft"]), (2, 1))
    c.alpha_composite(_part(root, q["topright"]), (258, 1))
    c.alpha_composite(_part(root, q["bottomleft"]), (2, 257))
    c.alpha_composite(_part(root, q["bottomright"]), (258, 257))
    img = c.crop((0, 0, PD_W, PD_H)).resize((round(PD_W * PD_SX), round(PD_H * PD_SY)), Image.LANCZOS)
    out = [("rep_frame", tc.wtex_blob(tc.encode(img, "t8", linear=True)))]
    sb = d + "ui-character-scrollbar.png"
    if os.path.exists(os.path.join(root, sb)):
        strip = Image.new("RGBA", (31, 434 - 71), (0, 0, 0, 0))
        strip.alpha_composite(_part(root, sb, crop=(0, 0, 31, 256)), (0, 0))      # TexCoords 0..0.484 x 0..1
        strip.alpha_composite(_part(root, sb, crop=(33, 0, 64, 108)), (0, 326 - 71))  # 0.516..1 x 0..0.422
        img = strip.resize((round(31 * PD_SX), round(strip.height * PD_SY)), Image.LANCZOS)
        out.append(("rep_scroll", tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    dlg = "interface/dialogframe/"
    det = _backdrop(root, REPD_W, REPD_H, dlg + "ui-dialogbox-background.png", dlg + "ui-dialogbox-border.png",
                    (11, 12, 12, 11))
    for rel, xy in ((d + "ui-character-reputation-detailbackground.png", (11, 11)),
                    (dlg + "ui-dialogbox-corner.png", (REPD_W - 6 - 32, 7)),
                    (dlg + "ui-dialogbox-divider.png", (9, 131))):
        if os.path.exists(os.path.join(root, rel)):
            part = _part(root, rel)
            det.alpha_composite(part.crop((0, 0, min(part.width, REPD_W - xy[0]), min(part.height, REPD_H - xy[1]))),
                                xy)
    img = det.resize((round(REPD_W * REPD_S), round(REPD_H * REPD_S)), Image.LANCZOS)
    out.append(("rep_detail", tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    return out


def skill_frames(root: str) -> list[tuple[str, bytes]]:
    """Character tabs: "skill_frame" - SkillFrame: UI-Character-General-TopLeft (TOPLEFT 2,-1) / -TopRight
    (TOPRIGHT 2,-1), SkillFrame-BotLeft (BOTTOMLEFT 2,-1) / -BotRight (BOTTOMRIGHT 2,-1), SkillFrameHorizontalBarLeft
    (UI-ClassTrainer-HorizontalBar 256 x 16 at 15,-290 + its 75 x 16 tail), SkillFrameExpandButtonFrame's tab
    (TOPLEFT 70,-49: UI-QuestLogSortTab-Left / -Middle / -Right at 0,6, the frame GetTextWidth(ALL) + 45 wide), cut
    like pd_frame at 0.6 x 0.55, T8; "skill_scroll" - SkillListScrollFrame's bar (UI-ClassTrainer-ScrollBar 30 x 128
    at TOPRIGHT -3,5 and BOTTOMRIGHT -3,-2 of the list x 21..317, y 75..295: y 70..297), "skill_dscroll" -
    SkillDetailScrollFrame's (30 x 100 + 30 x 100 of the details y 303..410: y 298..412), T8."""
    d, ct, qf = "interface/paperdollinfoframe/", "interface/classtrainerframe/", "interface/questframe/"
    need = [d + "ui-character-general-topleft.png", d + "ui-character-general-topright.png",
            d + "skillframe-botleft.png", d + "skillframe-botright.png"]
    if not all(os.path.exists(os.path.join(root, v)) for v in need):
        return []

    def has(rel: str) -> bool:
        return os.path.exists(os.path.join(root, rel))

    c = Image.new("RGBA", (384 + 4, 512 + 4), (0, 0, 0, 0))
    c.alpha_composite(_part(root, need[0]), (2, 1))
    c.alpha_composite(_part(root, need[1]), (384 + 2 - 128, 1))
    c.alpha_composite(_part(root, need[2]), (2, 512 + 1 - 256))
    c.alpha_composite(_part(root, need[3]), (384 + 2 - 128, 512 + 1 - 256))
    bar = ct + "ui-classtrainer-horizontalbar.png"
    if has(bar):
        c.alpha_composite(_part(root, bar, crop=(0, 0, 256, 16)), (15, 290))
        c.alpha_composite(_part(root, bar, crop=(0, 16, 75, 32)), (15 + 256, 290))
    tabs = [qf + f"ui-questlogsorttab-{k}.png" for k in ("left", "middle", "right")]
    if all(has(t) for t in tabs):
        fnt = os.path.join(root, "fonts", "frizqt__.ttf")
        tw = ImageFont.truetype(fnt, 12).getlength("Все") if os.path.exists(fnt) else 22  # ALL, GameFontHighlightLeft
        w = round(tw) + 45
        c.alpha_composite(_part(root, tabs[0]), (70, 49 - 6))
        c.alpha_composite(_part(root, tabs[1], size=(w - 16, 32)), (70 + 8, 49 - 6))
        c.alpha_composite(_part(root, tabs[2]), (70 + w - 8, 49 - 6))
    img = c.crop((0, 0, PD_W, PD_H)).resize((round(PD_W * PD_SX), round(PD_H * PD_SY)), Image.LANCZOS)
    out = [("skill_frame", tc.wtex_blob(tc.encode(img, "t8", linear=True)))]
    sb = ct + "ui-classtrainer-scrollbar.png"
    if has(sb):
        for name, y0, y1, top, bottom in (("skill_scroll", 70, 297, (0, 0, 30, 128), (34, 0, 64, 128)),
                                          ("skill_dscroll", 298, 412, (0, 0, 30, 100), (34, 28, 64, 128))):
            strip = Image.new("RGBA", (30, y1 - y0), (0, 0, 0, 0))
            strip.alpha_composite(_part(root, sb, crop=top), (0, 0))
            strip.alpha_composite(_part(root, sb, crop=bottom), (0, strip.height - (bottom[3] - bottom[1])))
            img = strip.resize((round(30 * PD_SX), round(strip.height * PD_SY)), Image.LANCZOS)
            out.append((name, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    return out


# SpellBookFrame.xml of the original (src/ui/psp/ui_world_spellbook.cpp).
SB_SX, SB_SY, SB_W, SB_H = 0.7, 0.55, 352, 470  # SpellBookFrame units -> px (ui_world_spellbook.cpp kSX / kSY)
TEXTURES += [
    ("sb_tab", "interface/spellbook/spellbook-skilllinetab.png", "image",
     dict(size=(64, 64), fmt="4444", linear=True, optional=True)),
    ("sb_checked", "interface/buttons/checkbuttonhilight.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
] + [
    (f"sb_{n}", f"interface/buttons/ui-spellbookicon-{f}.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True))
    for n, f in (("prev", "prevpage-up"), ("prev_dis", "prevpage-disabled"), ("next", "nextpage-up"),
                 ("next_dis", "nextpage-disabled"))
]


def spellbook_frame(root: str) -> list[tuple[str, bytes]]:
    """Spellbook: "sb_frame" - SpellBookFrame: the book icon (TOPLEFT 10,-8, 58 x 58, under the panel), the
    UI-SpellbookPanel quarters, the twelve SpellButtons' UI-Spellbook-SpellBackground (64 x 64 at -3,3 of the
    37-unit buttons: TOPLEFT 34,-85, 157 apart, rows 51 apart), SpellBookFrameTabButton1 selected
    (UI-SpellBook-Tab1-Selected, CENTER at BOTTOMLEFT 79,61), cut to 352 x 470 and baked at 0.7 x 0.55, T8."""
    s = "interface/spellbook/"
    q = {k: s + f"ui-spellbookpanel-{k}.png" for k in ("topleft", "topright", "botleft", "botright")}
    if not all(os.path.exists(os.path.join(root, v)) for v in q.values()):
        return []
    c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
    if os.path.exists(os.path.join(root, s + "spellbook-icon.png")):
        c.alpha_composite(_part(root, s + "spellbook-icon.png", size=(58, 58)), (10, 8))
    c.alpha_composite(_part(root, q["topleft"]), (0, 0))
    c.alpha_composite(_part(root, q["topright"]), (256, 0))
    c.alpha_composite(_part(root, q["botleft"]), (0, 256))
    c.alpha_composite(_part(root, q["botright"]), (256, 256))
    bg = s + "ui-spellbook-spellbackground.png"
    if os.path.exists(os.path.join(root, bg)):
        im = _part(root, bg)
        for i in range(12):
            c.alpha_composite(im, (34 + (i // 6) * 157 - 3, 85 + (i % 6) * 51 - 3))
    tab = s + "ui-spellbook-tab1-selected.png"
    if os.path.exists(os.path.join(root, tab)):
        c.alpha_composite(_part(root, tab), (79 - 64, 451 - 32))
    img = c.crop((0, 0, SB_W, SB_H)).resize((round(SB_W * SB_SX), round(SB_H * SB_SY)), Image.LANCZOS)
    return [("sb_frame", tc.wtex_blob(tc.encode(img, "t8", linear=True)))]


# Talents (src/ui/psp/ui_world_trainer.cpp). The rank box of a talent
# button (TalentFrame-RankBorder, 32x32). Optional.
TEXTURES += [
    ("tal_rank", "interface/talentframe/talentframe-rankborder.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
]


def talent_backgrounds(root: str) -> list[tuple[str, bytes]]:
    """Every talent tree background of the client (TalentTab.dbc
    BackgroundFile: Interface/TalentFrame/<name>-TopLeft/-TopRight/-BottomLeft/
    -BottomRight, 256x256 + 64x256 + 256x128 + 64x128 = 320x384), composed and
    baked 128x128 T8 as "tb_<name lowercase>". The PSP loads only its class's
    three. Optional: without them the tree has a dark fill."""
    d = os.path.join(root, "interface", "talentframe")
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith("-topleft.png"):
            continue
        base = fn[:-len("-topleft.png")]
        parts = {k: os.path.join(d, f"{base}-{k}.png") for k in ("topleft", "topright", "bottomleft", "bottomright")}
        if not all(os.path.exists(p) for p in parts.values()):
            print(f"  skip tb_{base} (a quarter is missing)")
            continue
        full = Image.new("RGBA", (320, 384), (0, 0, 0, 255))
        full.paste(Image.open(parts["topleft"]).convert("RGBA").resize((256, 256)), (0, 0))
        full.paste(Image.open(parts["topright"]).convert("RGBA").resize((64, 256)), (256, 0))
        full.paste(Image.open(parts["bottomleft"]).convert("RGBA").resize((256, 128)), (0, 256))
        full.paste(Image.open(parts["bottomright"]).convert("RGBA").resize((64, 128)), (256, 256))
        img = full.resize((128, 128), Image.LANCZOS)
        out.append((f"tb_{base}", tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    return out


# StaticPopup.xml / GameMenuFrame.xml of the original (src/ui/psp/ui_world_popup.cpp). The dialog
# boxes are drawn at POPUP_S of their units (WorldScreen::kDialogScale): the Backdrop's 32-unit border as a
# 9-slice of 26 px edges (the edges are uniform along their length - stretching = the original's tiling; the
# background is plain black at alpha 0.6, drawn as a fill), UI-DialogBox-Header cut to its visible 144 x 40
# (x 56..200), StaticPopupButtonTemplate's UI-DialogBox-Button-* (TexCoords bottom 0.71875 = 128 x 23) at
# the 120 x 21-unit button size. Optional: without them the generic panel / glue buttons.
POPUP_S = 0.8
TEXTURES += [
    ("sp_border", "interface/dialogframe/ui-dialogbox-border.png", "edge",
     dict(edge=round(32 * POPUP_S), fmt="t8", optional=True)),
    ("sp_header", "interface/dialogframe/ui-dialogbox-header.png", "image",
     dict(crop=(56, 0, 200, 40), size=(round(144 * POPUP_S), round(40 * POPUP_S)), fmt="4444", linear=True,
          optional=True)),
] + [
    (f"sp_btn_{n}", f"interface/buttons/ui-dialogbox-button-{f}.png", "image",
     dict(crop=(0, 0, 128, 23), size=(round(120 * POPUP_S), round(21 * POPUP_S)), fmt="4444", linear=True,
          optional=True))
    for n, f in (("up", "up"), ("dn", "down"), ("dis", "disabled"), ("hl", "highlight"))
]


# ClassTrainerFrame (the client's TrainerUI addon XML) and PlayerTalentFrame (its TalentUI addon XML +
# TalentFrameTemplates.xml) of the original (src/ui/psp/ui_world_trainer.cpp). The frames are composed
# from their FrameXML layout like the merchant's; the small dynamic art below. Optional.
TEXTURES += [
    ("ct_hl", "interface/buttons/ui-listbox-highlight2.png", "image",            # ClassTrainerSkillHighlight
     dict(size=(128, 16), fmt="4444", linear=True, optional=True)),
    ("ct_plushl", "interface/buttons/ui-plusbutton-hilight.png", "image",       # the header's highlight (ADD)
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("ct_slot", "interface/buttons/ui-emptyslot.png", "image",                  # ClassTrainerSkillIcon's slot
     dict(size=(64, 64), fmt="4444", linear=True, optional=True)),
    ("tal_slot", "interface/buttons/ui-emptyslot-white.png", "image",           # TalentButtonTemplate $parentSlot
     dict(size=(64, 64), fmt="4444", linear=True, optional=True)),
    ("tal_branches", "interface/talentframe/ui-talentbranches.png", "image",    # TalentBranchTemplate
     dict(size=(256, 64), fmt="4444", linear=True, optional=True)),
    ("tal_arrows", "interface/talentframe/ui-talentarrows.png", "image",        # TalentArrowTemplate
     dict(size=(64, 64), fmt="4444", linear=True, optional=True)),
]
# ClassTrainerFrame: 384 x 512 units, drawn x 0..360 (HitRectInsets right 34; the filter dropdown's right cap
# ends at 358), y 0..437 (bottom 75); baked at 0.6 x 0.55 = 216 x 240 px (ui_world_trainer.cpp kSX / kSY).
CT_SX, CT_SY, CT_W, CT_H = 0.6, 0.55, 360, 437
# PlayerTalentFrame: drawn x 0..356, y 0..465 (the bottom tabs are drawn by the PSP over its last units), 0.6 x 0.55
# = 214 x 256 px (one T8 256 x 256).
TAL_SX, TAL_SY, TAL_W, TAL_H = 0.6, 0.55, 356, 465


def trainer_frame(root: str) -> list[tuple[str, bytes]]:
    """Trainer: "ct_frame" - ClassTrainerFrame: UI-ClassTrainer-TopLeft/TopRight/BotLeft/BotRight, the
    horizontal bar between the list and the details (TOPLEFT 15,-275: 256 x 16 + 75 x 16 by TexCoords),
    ClassTrainerExpandButtonFrame's tab (TOPLEFT 15,-70: ExpandTab-Left, QuestLogSortTab-Middle sized to
    ALL + 24, -Right), ClassTrainerFrameFilterDropDown's label frame (TOPRIGHT -26,-64, width 130 + 25 + 25:
    CharacterCreate-LabelFrame in three slices) and the list's scroll bar (UI-ClassTrainer-ScrollBar at the
    scroll frame's TOPRIGHT -3,2 / BOTTOMRIGHT -3,-2), cut to 360 x 437 and baked at 0.6 x 0.55, T8."""
    d = "interface/classtrainerframe/"
    q = {k: d + f"ui-classtrainer-{k}.png" for k in ("topleft", "topright", "botleft", "botright")}
    if not all(os.path.exists(os.path.join(root, v)) for v in q.values()):
        return []
    c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))

    def has(rel: str) -> bool:
        return os.path.exists(os.path.join(root, rel))

    c.alpha_composite(_part(root, q["topleft"]), (0, 0))
    c.alpha_composite(_part(root, q["topright"]), (256, 0))
    c.alpha_composite(_part(root, q["botleft"]), (0, 256))
    c.alpha_composite(_part(root, q["botright"]), (256, 256))
    bar = d + "ui-classtrainer-horizontalbar.png"
    if has(bar):  # ClassTrainerHorizontalBarLeft (class trainer: TOPLEFT 15,-275) + its 75-unit tail
        c.alpha_composite(_part(root, bar, crop=(0, 0, 256, 16)), (15, 275))
        c.alpha_composite(_part(root, bar, crop=(0, 16, 75, 32)), (15 + 256, 275))
    qf = "interface/questframe/"
    if has(d + "ui-classtrainer-expandtab-left.png") and has(qf + "ui-questlogsorttab-middle.png"):
        c.alpha_composite(_part(root, d + "ui-classtrainer-expandtab-left.png"), (15, 70))
        mid_w = 44  # ClassTrainerExpandTabMiddle:SetWidth(GetTextWidth(ALL) + 24)
        c.alpha_composite(_part(root, qf + "ui-questlogsorttab-middle.png", size=(mid_w, 32)), (23, 64))
        if has(qf + "ui-questlogsorttab-right.png"):
            c.alpha_composite(_part(root, qf + "ui-questlogsorttab-right.png"), (23 + mid_w, 64))
    lf = "interface/glues/charactercreate/charactercreate-labelframe.png"
    if has(lf):  # UIDropDownMenuTemplate Left / Middle (SetWidth 130) / Right, TOPLEFT 0,17 of the frame
        x0, y0 = 384 - 26 - (130 + 50), 64 - 17
        c.alpha_composite(_part(root, lf, crop=(0, 0, 25, 64)), (x0, y0))
        c.alpha_composite(_part(root, lf, crop=(25, 0, 103, 64), size=(130, 64)), (x0 + 25, y0))
        c.alpha_composite(_part(root, lf, crop=(103, 0, 128, 64)), (x0 + 155, y0))
    sb = d + "ui-classtrainer-scrollbar.png"
    if has(sb):  # ClassTrainerListScrollFrameTemplate: 30 x 120 (top) and 30 x 123 (bottom) by TexCoords
        c.alpha_composite(_part(root, sb, crop=(0, 3, 30, 123)), (317 - 3, 96 - 2))
        c.alpha_composite(_part(root, sb, crop=(34, 4, 64, 128), size=(30, 123)), (317 - 3, 280 + 2 - 123))
    img = c.crop((0, 0, CT_W, CT_H)).resize((round(CT_W * CT_SX), round(CT_H * CT_SY)), Image.LANCZOS)
    return [("ct_frame", tc.wtex_blob(tc.encode(img, "t8", linear=True)))]


def talent_frame(root: str) -> list[tuple[str, bytes]]:
    """Talents: "tal_frame" - PlayerTalentFrame: UI-Character-General-TopLeft/TopRight/BottomLeft/BottomRight
    (offset 2,-1), PlayerTalentFramePointsBar (LEFT 16, RIGHT -36, BOTTOM 81, 26 high: UI-Button-Borders2
    background + left / middle / right border by TexCoords) and the scroll bar of PlayerTalentFrameScrollFrame
    (UI-Character-ScrollBar 31 x 256 at its TOPRIGHT -2,5 and 31 x 106 at its BOTTOMRIGHT -2,-2), cut to
    356 x 465 and baked at 0.6 x 0.55, T8. The tree background (tb_*) is drawn over it by the PSP."""
    d = "interface/paperdollinfoframe/"
    q = {k: d + f"ui-character-general-{k}.png" for k in ("topleft", "topright", "bottomleft", "bottomright")}
    if not all(os.path.exists(os.path.join(root, v)) for v in q.values()):
        return []
    c = Image.new("RGBA", (384 + 4, 512 + 4), (0, 0, 0, 0))
    c.alpha_composite(_part(root, q["topleft"]), (2, 1))
    c.alpha_composite(_part(root, q["topright"]), (258, 1))
    c.alpha_composite(_part(root, q["bottomleft"]), (2, 257))
    c.alpha_composite(_part(root, q["bottomright"]), (258, 257))
    bb = "interface/buttons/ui-button-borders2.png"
    if os.path.exists(os.path.join(root, bb)):  # 512 x 128; the bar is x 16..348, y 405..431
        c.alpha_composite(_part(root, bb, crop=(0, 27, 331, 53), size=(332, 26)), (16, 405))
        c.alpha_composite(_part(root, bb, crop=(0, 54, 6, 72), size=(6, 16)), (20, 410))
        c.alpha_composite(_part(root, bb, crop=(6, 54, 156, 72), size=(312, 16)), (26, 410))
        c.alpha_composite(_part(root, bb, crop=(156, 54, 162, 72), size=(6, 16)), (338, 410))
    sb = d + "ui-character-scrollbar.png"
    if os.path.exists(os.path.join(root, sb)):  # the scroll frame is x 23..319, y 77..405
        c.alpha_composite(_part(root, sb, crop=(0, 0, 31, 256)), (319 - 2, 77 - 5))
        c.alpha_composite(_part(root, sb, crop=(33, 0, 64, 106)), (319 - 2, 405 + 2 - 106))
    img = c.crop((0, 0, TAL_W, TAL_H)).resize((round(TAL_W * TAL_SX), round(TAL_H * TAL_SY)), Image.LANCZOS)
    return [("tal_frame", tc.wtex_blob(tc.encode(img, "t8", linear=True)))]


# MinimapCluster of the original - Minimap.xml / .lua, GameTime.xml (GameTimeFrame)
# and the client's TimeManager addon XML (TimeManagerClockButton) - at the scale of the PSP disc (src/ui/psp/
# ui_world_minimap.cpp): the 140-unit Minimap is the 76 px disc, so every piece is its XML size x MM_K, baked at
# the size it is drawn and drawn 1:1 (nearest). One T8 atlas "mm_cluster" (256 x 128, always on screen: 33 KB),
# replacing "mm_border" (4444 128 x 128, 32 KB; still read when the atlas is missing). MM_ATLAS = the kSrc* rects
# of ui_world_minimap.cpp: name, file, crop box (source texels = the XML TexCoords), size, atlas position.
MM_K = 76.0 / 140.0
MM_ATLAS = [
    # MinimapBorder (UI-Minimap-Border 0.25-1 x 0.125-0.875, the 192-unit MinimapBackdrop) and MinimapBorderTop
    # (0.25-1 x 0-0.125, 192 x 32 units)
    ("ring", "interface/minimap/ui-minimap-border.png", (64, 32, 256, 224), (104, 104), (0, 0)),
    ("bar", "interface/minimap/ui-minimap-border.png", (64, 0, 256, 32), (104, 17), (0, 106)),
    # MinimapZoomIn / MinimapZoomOut (32 units): Normal / Pushed / Disabled, HighlightTexture (ADD)
    ("zin_up", "interface/minimap/ui-minimap-zoominbutton-up.png", None, (17, 17), (106, 0)),
    ("zin_dn", "interface/minimap/ui-minimap-zoominbutton-down.png", None, (17, 17), (124, 0)),
    ("zin_dis", "interface/minimap/ui-minimap-zoominbutton-disabled.png", None, (17, 17), (142, 0)),
    ("zout_up", "interface/minimap/ui-minimap-zoomoutbutton-up.png", None, (17, 17), (160, 0)),
    ("zout_dn", "interface/minimap/ui-minimap-zoomoutbutton-down.png", None, (17, 17), (178, 0)),
    ("zout_dis", "interface/minimap/ui-minimap-zoomoutbutton-disabled.png", None, (17, 17), (196, 0)),
    ("zoom_hl", "interface/minimap/ui-minimap-zoombutton-highlight.png", None, (17, 17), (214, 0)),
    # MiniMap-TrackingBorder (its ring is texels 0..40 of 64; 54 units on MiniMapTracking, 52 on the mail and
    # world map buttons), UI-Minimap-Background (25 units), Tracking\None (20), ComboPoint 0.5625-1 (the
    # tracking shine, 27 units), UI-World-Icon (20), INV_Letter_15 (MiniMapMailIcon, 18), CompassNorthTag (16)
    ("trk_border", "interface/minimap/minimap-trackingborder.png", (0, 0, 40, 40), (18, 18), (106, 18)),
    ("trk_bg", "interface/minimap/ui-minimap-background.png", None, (14, 14), (125, 18)),
    ("trk_none", "interface/minimap/tracking/none.png", None, (11, 11), (140, 18)),
    ("trk_shine", "interface/comboframe/combopoint.png", (18, 0, 32, 16), (15, 15), (152, 18)),
    ("world_icon", "interface/worldmap/ui-world-icon.png", None, (11, 11), (168, 18)),
    ("mail_icon", "interface/icons/inv_letter_15.png", None, (10, 10), (180, 18)),
    ("north", "interface/minimap/compassnorthtag.png", None, (9, 9), (191, 18)),
    # GameTimeFrame: UI-Calendar-Button 0-0.390625 x 0-0.78125 (Normal) / 0.5-0.890625 (Pushed), 40 units;
    # GameTimeCalendarInvitesGlow (48, ADD), GameTimeCalendarInvitesTexture (EventNotification
    # 0.03125-0.6484375 x 0.03125-0.8671875, 28 x 32); TimeManagerClockButton's ClockBackground
    # (0.015625-0.8125 x 0.015625-0.390625, 60 x 28)
    ("cal_up", "interface/calendar/ui-calendar-button.png", (0, 0, 50, 50), (22, 22), (106, 37)),
    ("cal_dn", "interface/calendar/ui-calendar-button.png", (64, 0, 114, 50), (22, 22), (129, 37)),
    ("cal_glow", "interface/calendar/eventnotificationglow.png", None, (26, 26), (152, 37)),
    ("cal_invite", "interface/calendar/eventnotification.png", (4, 4, 83, 111), (15, 17), (179, 37)),
    ("clock_bg", "interface/timemanager/clockbackground.png", (1, 1, 52, 25), (33, 15), (195, 37)),
]
MM_ATLAS_SIZE = (256, 128)
# MinimapCompassTexture (rotateMinimap=1 only, read with that setting): CompassRing 365 units around the map, its
# ring cut out symmetrically (texels 52..204 of 256) = 216.7 units = 118 px.
MM_COMPASS = ("interface/minimap/compassring.png", (52, 52, 204, 204), (118, 118))
# GetZonePVPInfo's data: AreaTable.dbc 4 Flags (0x800 AREA_FLAG_SANCTUARY, 0x80 AREA_FLAG_ARENA), 28
# FactionGroupMask (2 Alliance, 4 Horde) -> "mm_pvp": u32 count + u32 (id << 8 | bits) by id, rows with any bit.
MM_PVP_ALLIANCE, MM_PVP_HORDE, MM_PVP_SANCTUARY, MM_PVP_ARENA = 1, 2, 4, 8


def minimap_cluster(root: str) -> list[tuple[str, int, bytes]]:
    out: list[tuple[str, int, bytes]] = []
    if all(os.path.exists(os.path.join(root, rel)) for _, rel, _, _, _ in MM_ATLAS):
        atlas = Image.new("RGBA", MM_ATLAS_SIZE, (0, 0, 0, 0))
        boxes = []
        for name, rel, crop, size, pos in MM_ATLAS:
            img = load_rgba(root, rel)
            if crop:
                img = img.crop(crop)
            img = img.resize(size, Image.LANCZOS)
            box = (pos[0], pos[1], pos[0] + size[0], pos[1] + size[1])
            assert box[2] <= MM_ATLAS_SIZE[0] and box[3] <= MM_ATLAS_SIZE[1], name
            assert all(box[2] <= b[0] or b[2] <= box[0] or box[3] <= b[1] or b[3] <= box[1] for b in boxes), name
            boxes.append(box)
            atlas.paste(img, pos)
        out.append(("mm_cluster", KIND_TEXTURE, tc.wtex_blob(tc.encode(atlas, "t8"))))
    rel, crop, size = MM_COMPASS
    if os.path.exists(os.path.join(root, rel)):
        img = load_rgba(root, rel).crop(crop).resize(size, Image.LANCZOS)
        out.append(("mm_compass", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    dbc = os.path.join(root, "dbfilesclient", "areatable.dbc")
    if os.path.exists(dbc):
        import build_glue_pak  # noqa: E402 (same folder: read_dbc)
        rows, _ = build_glue_pak.read_dbc(dbc)
        recs = []
        for r in rows:
            bits = ((MM_PVP_ALLIANCE if r[28] & 2 else 0) | (MM_PVP_HORDE if r[28] & 4 else 0)
                    | (MM_PVP_SANCTUARY if r[4] & 0x800 else 0) | (MM_PVP_ARENA if r[4] & 0x80 else 0))
            if bits and r[0] < (1 << 24):
                recs.append(r[0] << 8 | bits)
        recs.sort()
        out.append(("mm_pvp", 3, struct.pack(f"<I{len(recs)}I", len(recs), *recs)))
    return out
# PartyMemberFrame1..4 of the original (PartyFrameTemplates.xml, src/ui/psp/
# ui_world_party.cpp) at the unit frames' 0.6: UI-PartyFrame (128 x 64, T8, drawn 77 x 38), the leader and master
# looter icons (UI-Group-LeaderIcon / -MasterLooter, also PlayerLeaderIcon / PlayerMasterIcon and TargetFrame's
# leader icon), Disconnect-Icon (64 units -> 32 px); the unit menus (UnitPopup as UIDropDownMenu "MENU": the kit's
# tooltip backdrop) take UI-CheckBox-Check (the checked loot method / threshold) and ChatFrameExpandArrow (a nested
# item); their highlight is the existing npc_titlehl. Loaded while in a group (or a menu is open), freed after.
# All optional: without them plain fills and text.
TEXTURES += [
    ("pf_frame", "interface/targetingframe/ui-partyframe.png", "image",
     dict(size=(128, 64), fmt="t8", linear=True, optional=True)),
    ("pf_leader", "interface/groupframe/ui-group-leadericon.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("pf_master", "interface/groupframe/ui-group-masterlooter.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("pf_disconnect", "interface/characterframe/disconnect-icon.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("pf_check", "interface/buttons/ui-checkbox-check.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
    ("pf_arrow", "interface/chatframe/chatframeexpandarrow.png", "image",
     dict(size=(16, 16), fmt="4444", linear=True, optional=True)),
] + [
    # GroupLootFrame (LootFrame.xml LootRollButtonTemplate): the need / greed / disenchant buttons' NormalTexture and
    # HighlightTexture (ADD; pushed = the normal one moved a pixel), 32 units = 19 px at 0.6. While a roll is up.
    (f"gl_{n}{s}", f"interface/buttons/ui-grouploot-{f}-{t}.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True))
    for n, f in (("need", "dice"), ("greed", "coin"), ("de", "de"))
    for s, t in (("", "up"), ("_hl", "highlight"))
]

# Pets: PetFrame.xml, PetActionBarFrame.xml, PetStable.xml and PetPaperDollFrame.xml of the original
# (src/ui/psp/ui_world_pets.cpp). PetFrame at the unit frames' 0.6: UI-SmallTargetingFrame (-NoMana), UI-PetHappiness
# (the three faces, texcoords 0 .. 0.5625 x 0 .. 0.359375: 72 x 23), PetAttackModeTexture (UI-Player-AttackStatus
# 0.703125 .. 1); PetActionBarFrame at MainMenuBar's 0.5: UI-PetBar's two rows stacked (256 x 44 and 184 x 44,
# "pet_bar", composed below), UI-AutoCastableOverlay, the AutoCastShine sparkle (UI-ItemSockets 0.3984375 ..
# 0.4453125 x 0.40234375 .. 0.44921875). The buttons reuse ab_slot / ab_empty / ab_hl / ab_red, the checked state
# sb_checked (CheckButtonHilight), the stable slots ct_slot (UI-EmptySlot), the spec tabs sb_tab, the pet's talent trees
# tb_hunterpet*. All optional: without them plain fills.
TEXTURES += [
    ("pet_frame", "interface/targetingframe/ui-smalltargetingframe.png", "image",
     dict(size=(128, 64), fmt="t8", linear=True, optional=True)),
    ("pet_frame_nomana", "interface/targetingframe/ui-smalltargetingframe-nomana.png", "image",
     dict(size=(128, 64), fmt="t8", linear=True, optional=True)),
    ("pet_happy", "interface/petpaperdollframe/ui-pethappiness.png", "image",
     dict(crop=(0, 0, 72, 23), size=(72, 23), fmt="4444", linear=True, optional=True)),
    ("pet_attack", "interface/targetingframe/ui-player-attackstatus.png", "image",
     dict(crop=(180, 0, 256, 64), size=(64, 64), fmt="4444", linear=True, optional=True)),
    ("pet_autocast", "interface/buttons/ui-autocastableoverlay.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("pet_sparkle", "interface/itemsocketingframe/ui-itemsockets.png", "image",
     dict(crop=(102, 103, 114, 115), size=(16, 16), fmt="4444", linear=True, optional=True)),
] + [
    # The two CreatureFamily IconFiles with no SpellIcon.dbc row (spells.pak keeps icons by SpellIcon id): Warp Stalker
    # (32) and Chimaera (38) - GetPetIcon / GetStablePetInfo's icon; text.pak pet_text gives them icon id 0.
    (f"pet_icon_{fam}", f"interface/icons/{icon}.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True))
    for fam, icon in ((32, "ability_hunter_pet_warpstalker"), (38, "ability_hunter_pet_chimera"))
]


def pet_frames(root: str) -> list[tuple[str, bytes]]:
    """Pets: "pet_bar" - UI-PetBar's SlidingActionBarTexture0 (0 .. 1 x 0.015625 .. 0.359375 = 256 x 44) over
    SlidingActionBarTexture1 (0 .. 0.71875 x 0.375 .. 0.71875 = 184 x 44, left-aligned), at the bar's 0.5 (128 x 44 T8);
    "pet_stable" - PetStableFrame: UI-PetStable-TopLeft / -TopRight / -BottomLeft / -BottomRight (384 x 512) cut to the
    frame's hit rect (HitRectInsets right 34, bottom 75: 350 x 437) at the panels' 0.6 x 0.55, T8; "pet_pd_frame" -
    PetPaperDollFrame: UI-Character-General-TopLeft / -TopRight (2,-1), UI-PetPaperDollFrame-BotLeft / -BotRight
    (2,-257 / 258,-257), PetAttributesFrame's two UI-Character-StatBackground (114 x 78 at 67,-320 and 182,-320), the
    five resistance icons (PetResistanceFrame TOPRIGHT 347,-77: 32 x 29 each, arcane, fire, nature, frost, shadow), cut
    to the paper doll's 352 x 482 at 0.6 x 0.55 (as pd_frame), T8."""
    out = []
    bar = "interface/petactionbar/ui-petbar.png"
    if os.path.exists(os.path.join(root, bar)):
        im = Image.open(os.path.join(root, bar)).convert("RGBA")
        c = Image.new("RGBA", (256, 88), (0, 0, 0, 0))
        c.paste(im.crop((0, 2, 256, 46)), (0, 0))
        c.paste(im.crop((0, 48, 184, 92)), (0, 44))
        out.append(("pet_bar", tc.wtex_blob(tc.encode(c.resize((128, 44), Image.LANCZOS), "t8", linear=True))))
    st = {k: f"interface/petstableframe/ui-petstable-{k}.png" for k in ("topleft", "topright", "bottomleft", "bottomright")}
    if all(os.path.exists(os.path.join(root, v)) for v in st.values()):
        c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
        c.alpha_composite(_part(root, st["topleft"]), (0, 0))
        c.alpha_composite(_part(root, st["topright"]), (256, 0))
        c.alpha_composite(_part(root, st["bottomleft"]), (0, 256))
        c.alpha_composite(_part(root, st["bottomright"]), (256, 256))
        img = c.crop((0, 0, 350, 437)).resize((round(350 * PD_SX), round(437 * PD_SY)), Image.LANCZOS)
        out.append(("pet_stable", tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    d, pd = "interface/paperdollinfoframe/", "interface/petpaperdollframe/"
    q = [d + "ui-character-general-topleft.png", d + "ui-character-general-topright.png",
         pd + "ui-petpaperdollframe-botleft.png", pd + "ui-petpaperdollframe-botright.png"]
    if all(os.path.exists(os.path.join(root, v)) for v in q):
        c = Image.new("RGBA", (384 + 4, 512 + 4), (0, 0, 0, 0))
        c.alpha_composite(_part(root, q[0]), (2, 1))
        c.alpha_composite(_part(root, q[1]), (258, 1))
        c.alpha_composite(_part(root, q[2]), (2, 257))
        c.alpha_composite(_part(root, q[3]), (258, 257))
        sb = d + "ui-character-statbackground.png"
        if os.path.exists(os.path.join(root, sb)):
            s = _part(root, sb, size=(114, 78))
            c.alpha_composite(s, (67, 320))
            c.alpha_composite(s, (67 + 115, 320))
        ri = d + "ui-character-resistanceicons.png"
        if os.path.exists(os.path.join(root, ri)):
            for k, row in enumerate((2, 0, 1, 3, 4)):  # PetMagicResFrame1..5 TexCoords rows (as MagicResFrame1..5)
                c.alpha_composite(_part(root, ri, crop=(0, row * 29, 32, row * 29 + 29)), (315, 77 + k * 29))
        img = c.crop((0, 0, PD_W, PD_H)).resize((round(PD_W * PD_SX), round(PD_H * PD_SY)), Image.LANCZOS)
        out.append(("pet_pd_frame", tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    return out


# BankFrame.xml and MailFrame.xml (InboxFrame / SendMailFrame / OpenMailFrame) of the
# original (src/ui/psp/ui_world_bank.cpp, ui_world_mail.cpp). The frames are composed from their FrameXML layout
# like the merchant's; the parts that move (the letter's paper and the bar under it, the scroll bar, the slot
# art, the coins) apart. Data from the client's DBC: BankBagSlotPrices.dbc (GetBankSlotCost) and AuctionHouse.dbc
# (the auction house's name as a letter's sender). All optional: without them the windows draw plain panels.
BANK_S, BANK_W, BANK_H = 0.55, 400, 425  # BankFrame units -> px (ui_world_bank.cpp kS): the slots stay square
MAIL_SX, MAIL_SY, MAIL_W, MAIL_H = 0.6, 0.55, 356, 445  # MailFrame / OpenMailFrame units -> px (ui_world_mail.cpp)
# Stationery.dbc texture names (upstream game/stationery.hpp): one paper each, the two halves side by side.
MAIL_PAPERS = ("StationeryTest", "GMStationery", "AuctionStationery", "Stationery_Val", "Stationery_Chr",
               "Stationery_Orp")
TEXTURES += [
    ("mail_slotw", "interface/buttons/ui-emptyslot-white.png", "image",        # MailItem's $parentSlot (tinted)
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("mail_slotbg", "interface/buttons/ui-slot-background.png", "image",       # SendMailAttachment (TexCoords .64)
     dict(crop=(0, 0, 41, 41), size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("mail_letter", "interface/icons/inv_letter_15.png", "image",              # a letter's icon (upstream's)
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("mail_radio", "interface/buttons/ui-radiobutton.png", "image",            # UIRadioButtonTemplate (4 states)
     dict(size=(64, 16), fmt="4444", linear=True, optional=True)),
    ("mail_alert", "interface/dialogframe/ui-dialog-icon-alertnew.png", "image",  # InboxTooMuchMail
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("mail_coin_g", "interface/icons/inv_misc_coin_01.png", "image",           # GetCoinIcon: gold / silver / copper
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("mail_coin_s", "interface/icons/inv_misc_coin_03.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("mail_coin_c", "interface/icons/inv_misc_coin_05.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
]


def _input_border(root: str, c: Image.Image, x: int, y: int, mid: int, right_at: int | None = None) -> None:
    """Common-Input-Border at (x, y): the 8-wide left cap, the middle `mid` wide, the 8-wide right cap (TexCoords
    0..0.0625 / 0.0625..0.9375 / 0.9375..1 x 0..0.625 of 128 x 32); right_at: the right cap's left edge."""
    box = "interface/common/common-input-border.png"
    if not os.path.exists(os.path.join(root, box)):
        return
    c.alpha_composite(_part(root, box, crop=(0, 0, 8, 20)), (x, y))
    c.alpha_composite(_part(root, box, crop=(8, 0, 120, 20), size=(mid, 20)), (x + 8, y))
    c.alpha_composite(_part(root, box, crop=(120, 0, 128, 20)), (right_at if right_at is not None else x + 8 + mid, y))


def bank_mail_frames(root: str) -> list[tuple[str, int, bytes]]:
    """Bank / mail: "bank_frame" (UI-BankFrame, the slots in it, x 0..400 y 0..425 at BANK_S, T8),
    "mail_inbox" (Mail-Icon, UI-ItemText-TopLeft / -BotLeft + UI-SpellbookPanel-TopRight / -BotRight, the seven
    MailItemBorders and their lines), "mail_send" (Mail-Icon, UI-ClassTrainer quarters at 2,-1, the bar at -350,
    the name / subject boxes, the money boxes and their coins), "mail_open" (Mail-Icon 60 at 9,-6, UI-ClassTrainer
    + UI-OpenMail-BotLeft) - each x 0..356 y 0..445 at MAIL_SX x MAIL_SY, T8; "mail_paper_<stationery>" (the two
    halves, 252 + 64 x 256 -> 190 x 128, T8), "mail_bar" (UI-ClassTrainer-HorizontalBar 256 + 75 x 16), "mail_scroll"
    (UI-Character-ScrollBar: the top 31 x 256 and the bottom 31 x 106 stacked); raw "bank_prices" (u32 by
    BankBagSlotPrices.dbc id - 1) and "mail_ah" (AuctionHouse.dbc ruRU, "id\tname" lines)."""
    out: list[tuple[str, int, bytes]] = []

    def ex(rel: str) -> bool:
        return os.path.exists(os.path.join(root, rel))

    bank = "interface/bankframe/ui-bankframe.png"
    if ex(bank):
        img = _part(root, bank, crop=(0, 0, BANK_W, BANK_H))
        img = img.resize((round(BANK_W * BANK_S), round(BANK_H * BANK_S)), Image.LANCZOS)
        out.append(("bank_frame", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    m, it, sb, ct = "interface/mailframe/", "interface/itemtextframe/", "interface/spellbook/", "interface/classtrainerframe/"
    icon = m + "mail-icon.png"
    hbar = ct + "ui-classtrainer-horizontalbar.png"

    def mail_canvas(icon_at: tuple[int, int], icon_size: int) -> Image.Image:
        c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
        if ex(icon):
            c.alpha_composite(_part(root, icon, size=(icon_size, icon_size)), icon_at)
        return c

    def bake(c: Image.Image) -> bytes:
        img = c.crop((0, 0, MAIL_W, MAIL_H)).resize((round(MAIL_W * MAIL_SX), round(MAIL_H * MAIL_SY)), Image.LANCZOS)
        return tc.wtex_blob(tc.encode(img, "t8", linear=True))

    inbox_parts = [it + "ui-itemtext-topleft.png", sb + "ui-spellbookpanel-topright.png", it + "ui-itemtext-botleft.png",
                   sb + "ui-spellbookpanel-botright.png"]
    if all(ex(r) for r in inbox_parts):
        c = mail_canvas((10, 8), 58)
        for rel, at in zip(inbox_parts, ((0, 0), (256, 0), (0, 256), (256, 256))):
            c.alpha_composite(_part(root, rel), at)
        border = m + "mailitemborder.png"
        line = Image.new("RGBA", (322, 2), (84, 41, 0, 77))  # Color 0.33, 0.16, 0, a .3
        for i in range(7):  # MailItem1..7: TOPLEFT 28,-80, 45 apart (305 x 45)
            y = 80 + 45 * i
            if ex(border):
                c.alpha_composite(_part(root, border, crop=(0, 0, 42, 48)), (28, y))
                c.alpha_composite(_part(root, border, crop=(42, 0, 256, 48), size=(263, 48)), (28 + 305 - 263, y))
            c.alpha_composite(line, (28 + 152 - 161, y + 45 - 2))
        out.append(("mail_inbox", KIND_TEXTURE, bake(c)))
    ct_parts = [ct + f"ui-classtrainer-{k}.png" for k in ("topleft", "topright", "botleft", "botright")]
    if all(ex(r) for r in ct_parts):
        c = mail_canvas((10, 8), 58)
        for rel, at in zip(ct_parts, ((2, 1), (258, 1), (2, 257), (258, 257))):  # MailFrameTopLeft at 2,-1
            c.alpha_composite(_part(root, rel), at)
        if ex(hbar):  # SendMailHorizontalBarLeft (TOPLEFT 15,-350) + its 75-wide end
            c.alpha_composite(_part(root, hbar, crop=(0, 0, 256, 16)), (15, 350))
            c.alpha_composite(_part(root, hbar, crop=(0, 16, 75, 32)), (271, 350))
        _input_border(root, c, 97, 46, 100)  # SendMailNameEditBox (105,-46, 109 wide)
        _input_border(root, c, 97, 69, 221)  # SendMailSubjectEditBox (3 under it, 220 wide)
        # SendMailMoney (MoneyInputFrameTemplate) at 35,-383: gold 58 wide, silver 30 (+26), copper 30 (+16); the
        # right caps at RIGHT 0 / -10 / -10; the coins (UI-MoneyIcons quarters, 13 x 13) after each box.
        _input_border(root, c, 30, 383, 47, right_at=85)
        _input_border(root, c, 114, 383, 9, right_at=131)
        _input_border(root, c, 160, 383, 9, right_at=177)
        coins = "interface/moneyframe/ui-moneyicons.png"
        if ex(coins):
            for k, x in enumerate((95, 141, 187)):
                c.alpha_composite(_part(root, coins, crop=(16 * k, 0, 16 * k + 16, 16), size=(13, 13)), (x, 386))
        out.append(("mail_send", KIND_TEXTURE, bake(c)))
        open_parts = [ct_parts[0], ct_parts[1], m + "ui-openmail-botleft.png", ct_parts[3]]
        if all(ex(r) for r in open_parts):
            c = mail_canvas((9, 6), 60)
            for rel, at in zip(open_parts, ((0, 0), (256, 0), (0, 256), (256, 256))):
                c.alpha_composite(_part(root, rel), at)
            out.append(("mail_open", KIND_TEXTURE, bake(c)))
    for name in MAIL_PAPERS:
        left, right = f"interface/stationery/{name.lower()}1.png", f"interface/stationery/{name.lower()}2.png"
        if not (ex(left) and ex(right)):
            continue
        c = Image.new("RGBA", (316, 256), (0, 0, 0, 0))
        c.alpha_composite(_part(root, left, size=(252, 256)), (0, 0))
        c.alpha_composite(_part(root, right, size=(64, 256)), (252, 0))
        img = c.resize((190, 128), Image.LANCZOS)
        out.append((f"mail_paper_{name.lower()}", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    if ex(hbar):
        c = Image.new("RGBA", (331, 16), (0, 0, 0, 0))
        c.alpha_composite(_part(root, hbar, crop=(0, 0, 256, 16)), (0, 0))
        c.alpha_composite(_part(root, hbar, crop=(0, 16, 75, 32)), (256, 0))
        img = c.resize((round(331 * MAIL_SX), round(16 * MAIL_SY)), Image.LANCZOS)
        out.append(("mail_bar", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "4444", linear=True))))
    scroll = "interface/paperdollinfoframe/ui-character-scrollbar.png"
    if ex(scroll):
        c = Image.new("RGBA", (19, 199), (0, 0, 0, 0))
        c.alpha_composite(_part(root, scroll, crop=(0, 0, 31, 256), size=(19, 141)), (0, 0))
        c.alpha_composite(_part(root, scroll, crop=(33, 0, 64, 106), size=(19, 58)), (0, 141))
        out.append(("mail_scroll", KIND_TEXTURE, tc.wtex_blob(tc.encode(c, "4444", linear=True))))
    dbc = os.path.join(root, "dbfilesclient")
    import build_glue_pak  # noqa: E402 (same folder: read_dbc, the ruRU slot)
    if os.path.exists(os.path.join(dbc, "bankbagslotprices.dbc")):
        rows, _ = build_glue_pak.read_dbc(os.path.join(dbc, "bankbagslotprices.dbc"))
        prices = {r[0]: r[1] for r in rows}
        n = max(prices) if prices else 0
        out.append(("bank_prices", 3, struct.pack(f"<{n}I", *[prices.get(i + 1, 0) for i in range(n)])))
    if os.path.exists(os.path.join(dbc, "auctionhouse.dbc")):
        rows, sstr = build_glue_pak.read_dbc(os.path.join(dbc, "auctionhouse.dbc"))
        lines = ""
        for r in rows:  # ID, FactionID, DepositRate, ConsignmentRate, Name[16 locales + flags]
            name = sstr(r[4 + build_glue_pak.RU]) or next((sstr(r[4 + k]) for k in range(16) if sstr(r[4 + k])), "")
            if name:
                lines += f"{r[0]}\t{name}\n"
        out.append(("mail_ah", 3, lines.encode("utf-8")))
    return out


# TradeSkillFrame (the client's TradeSkillUI addon XML / .lua) of the original
# (src/ui/psp/ui_world_tradeskill.cpp). 384 x 512 units like ClassTrainerFrame, drawn x 0..360 (the inventory
# slot dropdown's right cap ends at 359), y 0..437, baked at the trainer's 0.6 x 0.55 (CT_*) = 216 x 240 px.
# The parts that come and go are separate: the list's FauxScrollFrame bar (only for a list longer than the
# rows), the detail header (it scrolls with TradeSkillDetailScrollChildFrame), the bottom of
# UI-ClassTrainer-BotLeft for a recipe with an AlternateVerb (TradeSkillFrame_SetSelection swaps the bottom
# left texture when the Create All / count controls hide), TradeSkillInputBox's border. The small dynamic art
# is shared with the other windows (ct_hl, ct_plushl, ql_plus / ql_minus, cb_*, skill_bar / skill_border,
# loot_namefr, sb_prev / sb_next, pbtn_*, scr_*). Optional: without ts_frame the window says "rebake ui.pak".
def tradeskill_frames(root: str) -> list[tuple[str, bytes]]:
    """Trade skill: "ts_frame" - UI-ClassTrainer-TopLeft / TopRight, UI-TradeSkill-BotLeft,
    UI-ClassTrainer-BotRight, TradeSkillHorizontalBarLeft (15,-221: 256 x 16 + its 75 x 16 tail by TexCoords),
    TradeSkillExpandButtonFrame's tab (15,-71: ExpandTab-Left, QuestLogSortTab-Middle 38 wide at 0,6, -Right),
    the two UIDropDownMenuTemplate label frames (UIDropDownMenu_SetWidth 120: TradeSkillInvSlotDropDown at
    TOPRIGHT -25,-66, TradeSkillSubClassDropDown RIGHT at its LEFT +35; CharacterCreate-LabelFrame in three
    slices at TOPLEFT 0,17), TradeSkillDetailScrollFrame's bar background (ClassTrainerDetailScrollFrameTemplate
    $parentTop / $parentBottom of the 297 x 176 frame at 20,-234 - always shown, only the bar hides); cut to
    360 x 437 at 0.6 x 0.55, T8. "ts_lscroll" - TradeSkillListScrollFrame's bar (ClassTrainerListScrollFrameTemplate
    of the 296 x 130 frame at TOPRIGHT -67,-96: x 314, y 94..228), "ts_dheader" - UI-ClassTrainer-DetailHeaderLeft
    + -Right (256 + 64 x 64), "ts_altbar" - UI-ClassTrainer-BotLeft x 10..212, y 146..182 (frame y 402..438),
    "ts_input" - TradeSkillInputBox's Common-Input-Border (Left 8 at -5, Middle, Right 8: 35 x 20)."""
    ct, ts = "interface/classtrainerframe/", "interface/tradeskillframe/"
    need = [ct + "ui-classtrainer-topleft.png", ct + "ui-classtrainer-topright.png", ts + "ui-tradeskill-botleft.png",
            ct + "ui-classtrainer-botright.png"]
    if not all(os.path.exists(os.path.join(root, v)) for v in need):
        return []

    def has(rel: str) -> bool:
        return os.path.exists(os.path.join(root, rel))

    def t8(img: Image.Image) -> bytes:
        return tc.wtex_blob(tc.encode(img, "t8", linear=True))

    def px(w: float, h: float) -> tuple[int, int]:
        return max(1, round(w * CT_SX)), max(1, round(h * CT_SY))

    c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
    c.alpha_composite(_part(root, need[0]), (0, 0))
    c.alpha_composite(_part(root, need[1]), (256, 0))
    c.alpha_composite(_part(root, need[2]), (0, 256))
    c.alpha_composite(_part(root, need[3]), (256, 256))
    bar = ct + "ui-classtrainer-horizontalbar.png"
    if has(bar):
        c.alpha_composite(_part(root, bar, crop=(0, 0, 256, 16)), (15, 221))
        c.alpha_composite(_part(root, bar, crop=(0, 16, 75, 32)), (15 + 256, 221))
    qf = "interface/questframe/"
    if has(ct + "ui-classtrainer-expandtab-left.png") and has(qf + "ui-questlogsorttab-middle.png"):
        c.alpha_composite(_part(root, ct + "ui-classtrainer-expandtab-left.png"), (15, 71))
        c.alpha_composite(_part(root, qf + "ui-questlogsorttab-middle.png", size=(38, 32)), (23, 65))
        if has(qf + "ui-questlogsorttab-right.png"):
            c.alpha_composite(_part(root, qf + "ui-questlogsorttab-right.png"), (61, 65))
    lf = "interface/glues/charactercreate/charactercreate-labelframe.png"
    if has(lf):
        for x0 in (54, 189):  # TradeSkillSubClassDropDown, TradeSkillInvSlotDropDown (170 units wide)
            c.alpha_composite(_part(root, lf, crop=(0, 0, 25, 64)), (x0, 49))
            c.alpha_composite(_part(root, lf, crop=(25, 0, 103, 64), size=(120, 64)), (x0 + 25, 49))
            c.alpha_composite(_part(root, lf, crop=(103, 0, 128, 64)), (x0 + 145, 49))
    sb = ct + "ui-classtrainer-scrollbar.png"
    if has(sb):  # the detail frame's bar background: x 315, y 229..352 and 288..411
        c.alpha_composite(_part(root, sb, crop=(0, 0, 30, 123)), (315, 229))
        c.alpha_composite(_part(root, sb, crop=(34, 4, 64, 128), size=(30, 123)), (315, 288))
    img = c.crop((0, 0, CT_W, CT_H)).resize(px(CT_W, CT_H), Image.LANCZOS)
    out = [("ts_frame", t8(img))]
    if has(sb):
        strip = Image.new("RGBA", (30, 228 - 94), (0, 0, 0, 0))
        strip.alpha_composite(_part(root, sb, crop=(0, 3, 30, 123)), (0, 0))
        strip.alpha_composite(_part(root, sb, crop=(34, 4, 64, 128), size=(30, 123)), (0, strip.height - 123))
        out.append(("ts_lscroll", t8(strip.resize(px(30, strip.height), Image.LANCZOS))))
    hl, hr = ct + "ui-classtrainer-detailheaderleft.png", ct + "ui-classtrainer-detailheaderright.png"
    if has(hl) and has(hr):
        h = Image.new("RGBA", (320, 64), (0, 0, 0, 0))
        h.alpha_composite(_part(root, hl), (0, 0))
        h.alpha_composite(_part(root, hr), (256, 0))
        out.append(("ts_dheader", t8(h.resize(px(320, 64), Image.LANCZOS))))
    alt = ct + "ui-classtrainer-botleft.png"
    if has(alt):
        out.append(("ts_altbar", t8(_part(root, alt, crop=(10, 146, 212, 182)).resize(px(202, 36), Image.LANCZOS))))
    ib = "interface/common/common-input-border.png"
    if has(ib):
        box = Image.new("RGBA", (35, 20), (0, 0, 0, 0))
        box.alpha_composite(_part(root, ib, crop=(0, 0, 8, 20)), (0, 0))
        box.alpha_composite(_part(root, ib, crop=(8, 0, 120, 20), size=(19, 20)), (8, 0))
        box.alpha_composite(_part(root, ib, crop=(120, 0, 128, 20)), (27, 0))
        out.append(("ts_input", t8(box.resize(px(35, 20), Image.LANCZOS))))
    return out


# The raid and the battlegrounds of the original (src/ui/psp/ui_world_raid.cpp,
# ui_world_battleground.cpp, ui_world_worldstate.cpp). FriendsFrame's Raid tab and
# BattlefieldFrame are composed like the character frame (RF_* / BF_*, 0.6 x 0.55), ReadyCheckFrame at the dialogs'
# 0.8 (POPUP_S); the pieces that come and go (the group outlines, the buttons, the icons) are small textures drawn
# at run time; WorldStateScoreFrame is one 3-slice texture (its width follows the battleground's columns, the
# middle stretches). Data: raw "bg_data" (BattlemasterList, the battlegrounds' Map.dbc names and descriptions,
# ChrClasses / ChrRaces - ruRU) and raw "ws_ui" (WorldStateUI.dbc) with the icons it names in the "ws_icons" atlas.
# All optional: without them the frames draw plain panels.
RF_SX, RF_SY, RF_W, RF_H = 0.6, 0.55, 352, 482  # FriendsFrame (the Raid tab) units -> px (ui_world_raid.cpp)
BF_SX, BF_SY, BF_W, BF_H = 0.6, 0.55, 350, 437  # BattlefieldFrame: HitRectInsets right 34, bottom 75
WS_ICON = 32                                     # "ws_icons" cell, texels (8 per row)
TEXTURES += [
    (name, rel, "image", dict(opts, linear=True, optional=True))
    for name, rel, opts in (
        # RaidGroupTemplate's UI-RaidFrame-GroupOutline (162 x 80 of its 170 x 80 TexCoords), RaidGroupButtonTemplate's
        # Normal / Highlight (156 x 14), RaidClassButtonTemplate's tab and icons, the role icons (UI-Group-*Icon).
        ("rf_group", "interface/raidframe/ui-raidframe-groupoutline.png", dict(crop=(0, 0, 170, 80), size=(97, 44),
                                                                                 fmt="t8")),
        ("rf_btn", "interface/raidframe/ui-raidframe-groupbutton.png", dict(crop=(0, 0, 164, 14), size=(96, 8),
                                                                              fmt="4444")),
        ("rf_btn_hl", "interface/raidframe/ui-raidframe-groupbutton.png", dict(crop=(0, 15, 164, 29), size=(96, 8),
                                                                                 fmt="4444")),
        ("rf_tab", "interface/spellbook/spellbook-skilllinetab.png", dict(size=(32, 32), fmt="4444")),
        ("rf_classes", "interface/glues/charactercreate/ui-charactercreate-classes.png", dict(size=(64, 64), fmt="4444")),
        ("rf_pets", "interface/raidframe/ui-raidframe-pets.png", dict(size=(16, 16), fmt="4444")),
        ("rf_mt_btn", "interface/raidframe/ui-raidframe-maintank.png", dict(size=(16, 16), fmt="4444")),
        ("rf_ma_btn", "interface/raidframe/ui-raidframe-mainassist.png", dict(size=(16, 16), fmt="4444")),
        ("rf_assist", "interface/groupframe/ui-group-assistanticon.png", dict(size=(16, 16), fmt="4444")),
        ("rf_mt", "interface/groupframe/ui-group-maintankicon.png", dict(size=(16, 16), fmt="4444")),
        ("rf_ma", "interface/groupframe/ui-group-mainassisticon.png", dict(size=(16, 16), fmt="4444")),
        # RaidPulloutButtonTemplate's bar frame (UI-RaidFrame-HealthBar, TexCoords 0..0.75 x 0..0.25 = 70 x 8 units).
        ("rf_hpframe", "interface/raidframe/ui-raidframe-healthbar.png", dict(crop=(0, 0, 96, 16), size=(48, 8),
                                                                                fmt="4444")),
        # ReadyCheck.lua's three states (READY_CHECK_AFK_TEXTURE = the not-ready one) and ReadyCheckFrame (323 x 97).
        ("rc_wait", "interface/raidframe/readycheck-waiting.png", dict(size=(32, 32), fmt="4444")),
        ("rc_ready", "interface/raidframe/readycheck-ready.png", dict(size=(32, 32), fmt="4444")),
        ("rc_notready", "interface/raidframe/readycheck-notready.png", dict(size=(32, 32), fmt="4444")),
        # (drawn 258 x 78 at POPUP_S; baked 256 x 77 - one 256-wide texture instead of a 512-wide one)
        ("rc_frame", "interface/raidframe/ui-readycheckframe.png", dict(crop=(0, 0, 323, 97), size=(256, 77),
                                                                        fmt="t8")),
        # UI-RaidTargetingIcons: the eight marks are its two top rows (64 texels each -> 32).
        ("rt_icons", "interface/targetingframe/ui-raidtargetingicons.png", dict(crop=(0, 0, 256, 128), size=(128, 64),
                                                                                  fmt="4444")),
        # BattlefieldFrame: BattlefieldButtonTemplate's highlight (tinted gold), the honor / arena point icons of the
        # random battleground's rewards, the queue status icons (the faction's currency, UI-StateIcon's crossed
        # swords); MiniMapBattlefieldIcon (Battleground-Alliance / -Horde).
        ("bf_hl", "interface/buttons/ui-listbox-highlight2.png", dict(size=(64, 8), fmt="4444")),
        ("bf_cur_a", "interface/pvpframe/pvp-currency-alliance.png", dict(size=(16, 16), fmt="4444")),
        ("bf_cur_h", "interface/pvpframe/pvp-currency-horde.png", dict(size=(16, 16), fmt="4444")),
        ("bf_arena", "interface/pvpframe/pvp-arenapoints-icon.png", dict(size=(16, 16), fmt="4444")),
        ("bf_confirm", "interface/characterframe/ui-stateicon.png", dict(crop=(29, 0, 61, 32), size=(16, 16),
                                                                         fmt="4444")),
        ("bf_mm_a", "interface/battlefieldframe/battleground-alliance.png", dict(size=(32, 32), fmt="4444")),
        ("bf_mm_h", "interface/battlefieldframe/battleground-horde.png", dict(size=(32, 32), fmt="4444")),
        # WorldStateCaptureBarTemplate (all its pieces in WorldState-CaptureBar, half size), WorldStateScoreTemplate's
        # row highlight / the winner banner, the score frame's title band, Icons-Classes.
        ("ws_capbar", "interface/worldstateframe/worldstate-capturebar.png", dict(size=(128, 32), fmt="4444")),
        ("ws_sc_hl", "interface/worldstateframe/worldstatefinalscore-highlight.png", dict(size=(128, 8), fmt="4444")),
        ("ws_sc_bg", "interface/worldstateframe/worldstatefinalscoreframe-topbackground.png",
         dict(size=(128, 32), fmt="4444")),
        ("ws_classes", "interface/worldstateframe/icons-classes.png", dict(size=(64, 64), fmt="4444")),
    )
]


def raid_bg_frames(root: str) -> list[tuple[str, int, bytes]]:
    """Raid: "rf_frame" (FriendsFrame for the Raid tab: FriendsFrameScrollIcon 60 at 7,-6 under the
    UI-Character-General quarters, x 0..352 y 0..482 at RF_SX x 256 px, T8), "bf_frame" (BattlefieldFrame:
    UI-Battlefield-Icon 60 at 7,-6 under UI-Battlefield-TopLeft / TopRight / BotLeft / BotRight, PvpRandomBg 333 x 241
    at BOTTOM -11,100 over them, x 0..350 y 0..437 at BF_SX x BF_SY, T8), "ws_score" (WorldStateFinalScoreFrame-
    TopLeft | Top | TopRight over BotLeft | Bot | BotRight = 128 + 256 + 256 x 512 units at 0.4 x 0.5, T8 - drawn in
    three slices), "ws_icons" + raw "ws_ui" (world_state_ui) and raw "bg_data" (BattlemasterList: "B id type maxGroup
    minLevel maxLevel maps name holidayWorldState", Map: "M id area name descAlliance descHorde", ChrClasses:
    "C id name female", ChrRaces: "R id name female faction" - tab-separated, newlines in the texts as \\n)."""
    out: list[tuple[str, int, bytes]] = []

    def ex(rel: str) -> bool:
        return os.path.exists(os.path.join(root, rel))

    def quarters(c: Image.Image, prefix: str, names: tuple[str, str, str, str]) -> None:
        tl, tr, bl, br = (prefix + n + ".png" for n in names)
        c.alpha_composite(_part(root, tl, size=(256, 256)), (0, 0))
        c.alpha_composite(_part(root, tr, size=(128, 256)), (256, 0))
        c.alpha_composite(_part(root, bl, size=(256, 256)), (0, 256))
        c.alpha_composite(_part(root, br, size=(128, 256)), (256, 256))

    g = "interface/paperdollinfoframe/ui-character-general-"
    if all(ex(g + n + ".png") for n in ("topleft", "topright", "bottomleft", "bottomright")):
        c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
        icon = "interface/friendsframe/friendsframescrollicon.png"
        if ex(icon):
            c.alpha_composite(_part(root, icon, size=(60, 60)), (7, 6))
        quarters(c, g, ("topleft", "topright", "bottomleft", "bottomright"))
        # 211 x 256 (drawn 211 x 265: one 256 x 256 texture instead of 256 x 512; the text is not in it).
        img = c.crop((0, 0, RF_W, RF_H)).resize((round(RF_W * RF_SX), 256), Image.LANCZOS)
        out.append(("rf_frame", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    b = "interface/battlefieldframe/ui-battlefield-"
    if all(ex(b + n + ".png") for n in ("topleft", "topright", "botleft", "botright")):
        c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
        if ex(b + "icon.png"):
            c.alpha_composite(_part(root, b + "icon.png", size=(60, 60)), (7, 6))
        quarters(c, b, ("topleft", "topright", "botleft", "botright"))
        bg = "interface/pvpframe/pvprandombg.png"
        if ex(bg):  # TexCoords 0..0.6504 x 0..0.9414 of 512 x 256; BOTTOM -11,100 -> x 14.5, y 171
            c.alpha_composite(_part(root, bg, crop=(0, 0, 333, 241)), (14, 171))
        img = c.crop((0, 0, BF_W, BF_H)).resize((round(BF_W * BF_SX), round(BF_H * BF_SY)), Image.LANCZOS)
        out.append(("bf_frame", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    w = "interface/worldstateframe/worldstatefinalscoreframe-"
    if all(ex(w + n + ".png") for n in ("topleft", "top", "topright", "botleft", "bot", "botright")):
        c = Image.new("RGBA", (640, 512), (0, 0, 0, 0))
        for n, x, y, sw in (("topleft", 0, 0, 128), ("top", 128, 0, 256), ("topright", 384, 0, 256),
                            ("botleft", 0, 256, 128), ("bot", 128, 256, 256), ("botright", 384, 256, 256)):
            c.alpha_composite(_part(root, w + n + ".png", size=(sw, 256)), (x, y))
        # 0.4 x 0.5 (256 x 256: one texture; the PSP draws it at <= 0.5 x 0.55, the text is not in it).
        out.append(("ws_score", KIND_TEXTURE, tc.wtex_blob(tc.encode(c.resize((256, 256), Image.LANCZOS), "t8",
                                                                     linear=True))))
    out += world_state_ui(root)
    dbcs = os.path.join(root, "dbfilesclient")
    if all(os.path.exists(os.path.join(dbcs, f)) for f in ("battlemasterlist.dbc", "map.dbc", "chrclasses.dbc",
                                                           "chrraces.dbc")):
        import build_glue_pak  # noqa: E402 (same folder: read_dbc)

        def esc(t: str) -> str:
            return t.replace("\r", "").replace("\t", " ").replace("\n", "\\n")

        lines = ""
        rows, s = build_glue_pak.read_dbc(os.path.join(dbcs, "battlemasterlist.dbc"))
        maps = set()
        for r in rows:  # 0 id, 1..8 maps (-1 none), 9 instance type, 11+8 name ruRU, 28 max group, 29 holiday world
            ids = [m for m in r[1:9] if m != 0xFFFFFFFF]  # state, 30 / 31 levels
            maps.update(ids)
            lines += (f"B\t{r[0]}\t{r[9]}\t{r[28]}\t{r[30]}\t{r[31]}\t{','.join(str(m) for m in ids)}\t"
                      f"{esc(s(r[11 + 8]))}\t{r[29]}\n")
        rows, s = build_glue_pak.read_dbc(os.path.join(dbcs, "map.dbc"))
        for r in rows:  # 0 id, 5+8 name, 22 area, 23+8 / 40+8 MapDescription0 (Alliance) / 1 (Horde)
            if r[0] in maps:
                lines += f"M\t{r[0]}\t{r[22]}\t{esc(s(r[5 + 8]))}\t{esc(s(r[23 + 8]))}\t{esc(s(r[40 + 8]))}\n"
        rows, s = build_glue_pak.read_dbc(os.path.join(dbcs, "chrclasses.dbc"))
        for r in rows:  # 4+8 name, 21+8 female name ruRU
            lines += f"C\t{r[0]}\t{esc(s(r[4 + 8]))}\t{esc(s(r[21 + 8]))}\n"
        rows, s = build_glue_pak.read_dbc(os.path.join(dbcs, "chrraces.dbc"))
        for r in rows:  # 13 faction (0 Alliance, 1 Horde), 14+8 name, 31+8 female name ruRU
            if r[0] <= 11:
                lines += f"R\t{r[0]}\t{esc(s(r[14 + 8]))}\t{esc(s(r[31 + 8]))}\t{r[13]}\n"
        out.append(("bg_data", 3, lines.encode("utf-8")))
    return out


def world_state_ui(root: str) -> list[tuple[str, int, bytes]]:
    """Battleground: raw "ws_ui" - WorldStateUI.dbc (GetWorldStateUIInfo / GetBattlefieldStatInfo), one line per
    record: id map area phase type stateVar icon dynIcon dynFlash rowIcon0 rowIcon1 hasIcon capture ev1 ev2 ev3 text
    tooltip dynTooltip (tab-separated; icons = "ws_icons" cells, -1 none; rowIcon = a score column's icon..faction;
    capture = ExtendedUI CAPTUREPOINT), and "ws_icons" - those icons, WS_ICON texels a cell, 8 a row, 4444."""
    dbc = os.path.join(root, "dbfilesclient", "worldstateui.dbc")
    if not os.path.exists(dbc):
        return []
    import build_glue_pak  # noqa: E402 (same folder: read_dbc)
    rows, s = build_glue_pak.read_dbc(dbc)
    cells: dict[str, int] = {}

    def cell(path: str) -> int:
        if not path:
            return -1
        rel = path.replace("\\", "/").lower()
        if rel.endswith(".blp"):
            rel = rel[:-4]
        rel += ".png"
        if not os.path.exists(os.path.join(root, rel)):
            return -1  # e.g. ColumnIcon-FlagCapture2.blp + faction: no such texture (the client shows none)
        return cells.setdefault(rel, len(cells))

    def esc(t: str) -> str:
        return t.replace("\r", "").replace("\t", " ").replace("\n", "\\n")

    lines = ""
    for r in rows:  # 0 id, 1 map, 2 area, 3 phase, 4 icon, 5+8 text, 22+8 tooltip, 39 var, 40 type, 41 dynamic
        icon, dyn = s(r[4]), s(r[41])  # icon, 42+8 dynamic tooltip, 59 extended UI, 60..62 its variables
        if r[40] == 2:
            ci = cd = cf = -1
            r0, r1 = cell(icon + "0" if icon else ""), cell(icon + "1" if icon else "")
        else:
            ci, cd, cf = cell(icon), cell(dyn), cell(dyn + "Flash" if dyn else "")
            r0 = r1 = -1
        cap = 1 if s(r[59]) == "CAPTUREPOINT" else 0
        lines += (f"{r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\t{r[40]}\t{r[39]}\t{ci}\t{cd}\t{cf}\t{r0}\t{r1}\t{1 if icon else 0}\t"
                  f"{cap}\t{r[60]}\t{r[61]}\t{r[62]}\t{esc(s(r[5 + 8]))}\t{esc(s(r[22 + 8]))}\t{esc(s(r[42 + 8]))}\n")
    out = [("ws_ui", 3, lines.encode("utf-8"))]
    if cells:
        rows_n = (len(cells) + 7) // 8
        atlas = Image.new("RGBA", (8 * WS_ICON, rows_n * WS_ICON), (0, 0, 0, 0))
        for rel, k in cells.items():
            atlas.alpha_composite(load_rgba(root, rel).resize((WS_ICON, WS_ICON), Image.LANCZOS),
                                  ((k % 8) * WS_ICON, (k // 8) * WS_ICON))
        out.append(("ws_icons", KIND_TEXTURE, tc.wtex_blob(tc.encode(atlas, "4444", linear=True))))
    return out
# raid / battleground (end)

# DressUpFrame.xml of the original (src/ui/psp/ui_world_dressup.cpp). The frame -
# UI-Character-General-TopLeft (0,0) / -TopRight (256,0), SkillFrame-BotLeft (0,-256) / -BotRight (256,-256) - with
# the OVERLAY background of the player's race over it: DressUpBackground-<race>1..4 at 22,-76 in 256 x 255,
# 62 x 255, 256 x 128 and 62 x 128 (no TexCoords: the 256 / 64 wide files squeezed in). One composite per race
# (DressUpTexturePath: gnomes and trolls take the dwarves' and the orcs'), cut like pd_frame (352 x 482) at
# 0.6 x 0.55 = 211 x 265, T8 (129 KB each; only the player's one is loaded, while the frame is open).
DU_RACES = ("human", "orc", "dwarf", "nightelf", "scourge", "tauren", "bloodelf", "draenei")


def dressup_frames(root: str) -> list[tuple[str, bytes]]:
    """Dressing room: "du_frame_<race>" for each DU_RACES with its four backgrounds extracted."""
    d = "interface/paperdollinfoframe/"
    need = [d + "ui-character-general-topleft.png", d + "ui-character-general-topright.png",
            d + "skillframe-botleft.png", d + "skillframe-botright.png"]
    if not all(os.path.exists(os.path.join(root, v)) for v in need):
        return []
    base = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
    base.alpha_composite(_part(root, need[0], size=(256, 256)), (0, 0))
    base.alpha_composite(_part(root, need[1], size=(128, 256)), (256, 0))
    base.alpha_composite(_part(root, need[2], size=(256, 256)), (0, 256))
    base.alpha_composite(_part(root, need[3], size=(128, 256)), (256, 256))
    out = []
    for race in DU_RACES:
        bg = [f"interface/dressupframe/dressupbackground-{race}{k}.png" for k in (1, 2, 3, 4)]
        if not all(os.path.exists(os.path.join(root, v)) for v in bg):
            print(f"  skip du_frame_{race} (optional, missing {bg[0][:-5]}1..4)")
            continue
        c = base.copy()
        for rel, size, at in ((bg[0], (256, 255), (22, 76)), (bg[1], (62, 255), (22 + 256, 76)),
                              (bg[2], (256, 128), (22, 76 + 255)), (bg[3], (62, 128), (22 + 256, 76 + 255))):
            c.alpha_composite(_part(root, rel, size=size), at)
        img = c.crop((0, 0, PD_W, PD_H)).resize((round(PD_W * PD_SX), round(PD_H * PD_SY)), Image.LANCZOS)
        out.append((f"du_frame_{race}", tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    return out
# dressing room (end)

# AuctionFrame of the original (the client's AuctionUI addon XML / .lua, src/ui/psp/ui_world_auction.cpp).
# The frame is 832 x 447 units: six textures per tab as AuctionFrameTab_OnClick sets them (TopLeft 256 + Top 320 (the
# 256-wide file stretched, as the XML sizes it) + TopRight 256, the same under it), baked at AUC_S = 0.55 -> 458 x 246
# px, T8 (512 x 256, ~130 KB, the shown tab's only). Baked into each tab are its static widgets at their XML places:
# the InputBoxTemplate borders of BrowseName / BrowseMinLevel / BrowseMaxLevel, the UIDropDownMenuTemplate bodies
# (BrowseDropDown at BrowseLevelText's BOTTOMRIGHT -5,-1 - the ruRU LEVEL_RANGE is 86 units wide in its 10-unit font;
# PriceDropDown / DurationDropDown at UIDropDownMenu_SetWidth 80), the MoneyInputFrames (BrowseBidPrice, BidBidPrice,
# StartPrice, BuyoutPrice: their boxes and coins) and AuctionsItemButton's UI-AuctionFrame-ItemSlot. What comes and
# goes is apart (4444): the sort column tab (WhoFrame-ColumnTabs), the sort arrow, the row frame
# (UI-AuctionItemNameFrame), the row / filter highlights, the filter background and lines, an input border for the
# stack boxes, the scroll bar at 0.55, the progress frame (AuctionProgressFrame). Data: raw "auc_data"
# (tools/psp/cbakers/auc_data.py) - the browse categories (ItemClass.dbc / ItemSubClass.dbc names, ruRU) and
# AuctionHouse.dbc's deposit rates. All optional: without them the frame draws plain panels.
AUC_S, AUC_W, AUC_H = 0.55, 832, 447
AUC_TABS = {  # AuctionFrameTab_OnClick: TopLeft, Top, TopRight, BotLeft, Bot, BotRight
    "auc_browse": ("browse-topleft", "browse-top", "browse-topright", "browse-botleft", "auction-bot", "bid-botright"),
    "auc_bid": ("bid-topleft", "auction-top", "auction-topright", "bid-botleft", "auction-bot", "bid-botright"),
    "auc_auctions": ("auction-topleft", "auction-top", "auction-topright", "auction-botleft", "auction-bot",
                     "auction-botright"),
}
AUC_LEVEL_TEXT_W = 86  # LEVEL_RANGE (ruRU "Level Range") in GameFontHighlightSmall (ui_world_auction.cpp kDropBrowseX)


def _auc_dropdown(root: str, c: Image.Image, x: int, y: int, middle: int) -> None:
    """UIDropDownMenuTemplate at frame TOPLEFT (x, y): Left 25 x 64 at 0,17 (TexCoords 0..0.1953125), Middle
    `middle` wide (0.1953125..0.8046875), Right 25 (0.8046875..1) of CharacterCreate-LabelFrame (128 x 64)."""
    lf = "interface/glues/charactercreate/charactercreate-labelframe.png"
    if not os.path.exists(os.path.join(root, lf)):
        return
    c.alpha_composite(_part(root, lf, crop=(0, 0, 25, 64)), (x, y - 17))
    c.alpha_composite(_part(root, lf, crop=(25, 0, 103, 64), size=(middle, 64)), (x + 25, y - 17))
    c.alpha_composite(_part(root, lf, crop=(103, 0, 128, 64)), (x + 25 + middle, y - 17))


def _auc_editbox(root: str, c: Image.Image, x: int, y: int, w: int) -> None:
    """InputBoxTemplate's border around an EditBox at (x, y) w x 16 (Left at LEFT -5,0, Right at RIGHT, 20 high)."""
    _input_border(root, c, x - 5, y - 2, w - 11, right_at=x + w - 8)


def _auc_money(root: str, c: Image.Image, x: int, y: int) -> None:
    """MoneyInputFrameTemplate at (x, y): gold 58 wide, silver 30 at +84, copper 30 at +130 (their borders: the right
    caps at RIGHT 0 / -10 / -10), the coins (UI-MoneyIcons quarters, 13 x 13) after each box."""
    _input_border(root, c, x - 5, y, 47, right_at=x + 50)
    _input_border(root, c, x + 79, y, 9, right_at=x + 96)
    _input_border(root, c, x + 125, y, 9, right_at=x + 142)
    coins = "interface/moneyframe/ui-moneyicons.png"
    if os.path.exists(os.path.join(root, coins)):
        for k, cx in enumerate((x + 60, x + 106, x + 152)):
            c.alpha_composite(_part(root, coins, crop=(16 * k, 0, 16 * k + 16, 16), size=(13, 13)), (cx, y + 3))


def auction_frames(root: str) -> list[tuple[str, int, bytes]]:
    """Auction: "auc_browse" / "auc_bid" / "auc_auctions" (T8 458 x 246), the small parts (4444), raw "auc_data"."""
    out: list[tuple[str, int, bytes]] = []

    def ex(rel: str) -> bool:
        return os.path.exists(os.path.join(root, rel))

    af = "interface/auctionframe/ui-auctionframe-"
    for name, parts in AUC_TABS.items():
        rels = [af + p + ".png" for p in parts]
        if not all(ex(r) for r in rels):
            print(f"  skip {name} (optional, missing interface/auctionframe)")
            continue
        c = Image.new("RGBA", (832, 512), (0, 0, 0, 0))
        for rel, (x, y, w) in zip(rels, ((0, 0, 256), (256, 0, 320), (576, 0, 256), (0, 256, 256), (256, 256, 320),
                                         (576, 256, 256))):
            c.alpha_composite(_part(root, rel, size=(w, 256)), (x, y))
        if name == "auc_browse":
            _auc_editbox(root, c, 83, 53, 140)    # BrowseName (BrowseNameText 80,-41 + BOTTOMLEFT 3,-2)
            _auc_editbox(root, c, 233, 53, 25)    # BrowseMinLevel (BrowseLevelText BOTTOMLEFT 3,-6)
            _auc_editbox(root, c, 270, 53, 25)    # BrowseMaxLevel (RIGHT 12,0)
            _auc_dropdown(root, c, 230 + AUC_LEVEL_TEXT_W - 5, 48, 115)  # BrowseDropDown (default width)
            _auc_money(root, c, 316, 411)         # BrowseBidPrice (BOTTOM 25,18: 176 x 18 centred at 404)
        elif name == "auc_bid":
            _auc_money(root, c, 276, 411)         # BidBidPrice (BOTTOM -15,18)
        else:
            slot = "interface/auctionframe/ui-auctionframe-itemslot.png"
            if ex(slot):  # AuctionsItemButton's texture (TexCoords .15625 .83203125 .171875 .796875) at -2,2
                c.alpha_composite(_part(root, slot, crop=(40, 11, 213, 51)), (28, 92))
            _auc_money(root, c, 35, 259)          # StartPrice (BOTTOMLEFT 35,170)
            _auc_money(root, c, 35, 304)          # BuyoutPrice (BOTTOMLEFT 35,125)
            _auc_dropdown(root, c, 87, 215, 80)   # PriceDropDown (TOPRIGHT 217,-215, width 80 + 50)
            _auc_dropdown(root, c, 87, 326, 80)   # DurationDropDown (BOTTOMRIGHT 217,89)
        img = c.crop((0, 0, AUC_W, AUC_H)).resize((round(AUC_W * AUC_S), round(AUC_H * AUC_S)), Image.LANCZOS)
        out.append((name, KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))

    def small(name: str, img: Image.Image) -> None:
        out.append((name, KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "4444", linear=True))))

    who = "interface/friendsframe/whoframe-columntabs.png"
    if ex(who):  # AuctionSortButtonTemplate: Left 0..0.078125, Middle ..0.90625, Right ..0.96875 x 0..0.59375 (19 high)
        small("auc_coltab", _part(root, who, crop=(0, 0, 62, 19)))
    arrow = "interface/buttons/ui-sortarrow.png"
    if ex(arrow):  # TexCoords 0..0.5625 (9 x 8) over it flipped (SortButton_UpdateArrow's reversed TexCoords)
        ar = _part(root, arrow, crop=(0, 0, 9, 8), size=(16, 8))
        c = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        c.alpha_composite(ar, (0, 0))
        c.alpha_composite(ar.transpose(Image.FLIP_TOP_BOTTOM), (0, 8))
        small("auc_arrow", c)
    namefr = "interface/auctionframe/ui-auctionitemnameframe.png"
    if ex(namefr):  # the rows' Left / Middle / Right (0..0.078125 / ..0.75 / ..0.828125 of 128 x 32)
        small("auc_namefr", _part(root, namefr, crop=(0, 0, 106, 32)))
    hl = "interface/helpframe/helpframebutton-highlight.png"
    if ex(hl):  # the rows' HighlightTexture (TexCoords 0..1 x 0..0.578125)
        small("auc_rowhl", _part(root, hl, crop=(0, 0, 256, 37), size=(128, 32)))
    tabhl = "interface/paperdollinfoframe/ui-character-tab-highlight.png"
    if ex(tabhl):  # the filter / sort buttons' HighlightTexture
        small("auc_tabhl", _part(root, tabhl, size=(64, 16)))
    fbg = "interface/auctionframe/ui-auctionframe-filterbg.png"
    if ex(fbg):  # AuctionClassButtonTemplate's NormalTexture (TexCoords 0..0.53125 x 0..0.625 = 136 x 20)
        small("auc_filterbg", _part(root, fbg, crop=(0, 0, 136, 20), size=(128, 32)))
    flines = "interface/auctionframe/ui-auctionframe-filterlines.png"
    if ex(flines):  # $parentLines: 0..0.4375 (a line going on) / 0.4375..0.875 (the last), 0..0.625 high
        small("auc_flines", _part(root, flines, crop=(0, 0, 14, 20), size=(16, 32)))
    box = "interface/common/common-input-border.png"
    if ex(box):  # AuctionsStackSizeEntry / AuctionsNumStacksEntry borders (0..0.625 high)
        small("auc_input", _part(root, box, crop=(0, 0, 128, 20), size=(64, 16)))
    scroll = "interface/paperdollinfoframe/ui-character-scrollbar.png"
    if ex(scroll):  # the FauxScrollFrames' bar: the top 31 x 256 and the bottom 31 x 106 at 0.55
        c = Image.new("RGBA", (17, 199), (0, 0, 0, 0))
        c.alpha_composite(_part(root, scroll, crop=(0, 0, 31, 256), size=(17, 141)), (0, 0))
        c.alpha_composite(_part(root, scroll, crop=(33, 0, 64, 106), size=(17, 58)), (0, 141))
        small("auc_scroll", c)
    gear = "interface/dialogframe/ui-dialog-icon-alertother.png"
    if ex(gear):  # StaticPopup's showAlertGear (AUCTION_HOUSE_DISABLED)
        small("auc_alertgear", _part(root, gear, size=(32, 32)))
    post = ["interface/auctionframe/ui-auctionpost-" + p + ".png" for p in ("background", "endcaps", "middle")]
    if all(ex(r) for r in post):  # AuctionProgressFrame 300 x 64: the fill 12,8 inset, the end caps, the middle
        c = Image.new("RGBA", (300, 64), (0, 0, 0, 0))
        c.alpha_composite(_part(root, post[0], crop=(0, 16, 64, 48), size=(276, 48)), (12, 8))
        c.alpha_composite(_part(root, post[1], crop=(32, 0, 64, 64)), (0, 0))
        c.alpha_composite(_part(root, post[1], crop=(0, 0, 32, 64)), (268, 0))
        c.alpha_composite(_part(root, post[2], size=(236, 64)), (32, 0))
        small("auc_post", c.resize((round(300 * AUC_S), round(64 * AUC_S)), Image.LANCZOS))

    import cbakers  # noqa: E402 (same folder: the "auc_data" baker)
    auc = cbakers.build_auc_data(root)
    if auc is not None:
        out.append(("auc_data", 3, auc))
    return out
# auction (end)


# Using game objects (src/platform/psp/psp_world_session_gouse.cpp,
# src/ui/psp/ui_world_gouse.cpp; formats in include/rendering/psp/psp_gameobject_format.hpp). Data from the client's
# DBC (nothing from a server's DB): "go_lock" (tools/psp/cbakers/go_lock.py) = the lock types (LockType.dbc texts,
# ruRU), the locks' cases (Lock.dbc) and the open-lock spells (Spell.dbc, SkillLineAbility.dbc); "go_snd" =
# GameObjectDisplayInfo.Sound[10] of the rows with a sound. Art: ItemTextFrame.xml
# - "itext_frame" (Spellbook-Icon 58 at 10,-8, UI-ItemText-TopLeft / -BotLeft, UI-SpellbookPanel-TopRight /
# -BotRight; the visible 354 x 442 of the 384 x 512 frame) and "itext_mat_<material>" (ItemText-<Material>-TopLeft
# 256 x 256, -TopRight 64 x 256, -BotLeft 256 x 128, -BotRight 64 x 128 = 320 x 384) at 0.6 x 0.55, T8. Optional.
ITEXT_SX, ITEXT_SY, ITEXT_W, ITEXT_H = 0.6, 0.55, 354, 442
ITEXT_MATERIALS = ("stone", "marble", "silver", "bronze", "valentine", "illidan")


def gameobject_use_data(root: str) -> list[tuple[str, int, bytes]]:
    """Game objects: "go_lock", "go_snd" (raw), "itext_frame", "itext_mat_<m>" (textures)."""
    out: list[tuple[str, int, bytes]] = []
    import build_glue_pak  # noqa: E402 (same folder: read_dbc)
    import cbakers  # noqa: E402 (same folder: the "go_lock" baker)
    dbc = os.path.join(root, "dbfilesclient")

    def path(name: str) -> str:
        return os.path.join(dbc, name)

    go_lock = cbakers.build_go_lock(root)
    if go_lock is not None:
        out.append(("go_lock", 3, go_lock))
    if os.path.exists(path("gameobjectdisplayinfo.dbc")):
        rows, _ = build_glue_pak.read_dbc(path("gameobjectdisplayinfo.dbc"))
        recs = [struct.pack("<I10H", r[0], *[min(v, 0xFFFF) for v in r[2:12]]) for r in sorted(rows, key=lambda r: r[0])
                if any(r[2:12])]
        out.append(("go_snd", 3, struct.pack("<4sI", b"WGSN", len(recs)) + b"".join(recs)))

    def ex(rel: str) -> bool:
        return os.path.exists(os.path.join(root, rel))

    it, sb = "interface/itemtextframe/", "interface/spellbook/"
    parts = [it + "ui-itemtext-topleft.png", sb + "ui-spellbookpanel-topright.png", it + "ui-itemtext-botleft.png",
             sb + "ui-spellbookpanel-botright.png"]
    if all(ex(r) for r in parts):
        c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
        if ex(sb + "spellbook-icon.png"):  # BACKGROUND: the portrait under the frame (TOPLEFT 10,-8, 58 x 58)
            c.alpha_composite(_part(root, sb + "spellbook-icon.png", size=(58, 58)), (10, 8))
        for rel, at in zip(parts, ((0, 0), (256, 0), (0, 256), (256, 256))):
            c.alpha_composite(_part(root, rel), at)
        img = c.crop((0, 0, ITEXT_W, ITEXT_H)).resize((round(ITEXT_W * ITEXT_SX), round(ITEXT_H * ITEXT_SY)), Image.LANCZOS)
        out.append(("itext_frame", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    for m in ITEXT_MATERIALS:
        quarters = [it + f"itemtext-{m}-{k}.png" for k in ("topleft", "topright", "botleft", "botright")]
        if not all(ex(r) for r in quarters):
            continue
        c = Image.new("RGBA", (320, 384), (0, 0, 0, 0))
        for rel, size, at in zip(quarters, ((256, 256), (64, 256), (256, 128), (64, 128)),
                                 ((0, 0), (256, 0), (0, 256), (256, 256))):
            c.alpha_composite(_part(root, rel, size=size), at)
        img = c.resize((round(320 * ITEXT_SX), round(384 * ITEXT_SY)), Image.LANCZOS)
        out.append((f"itext_mat_{m}", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    out += barber_data(root)
    return out


# BarberShopUI addon XML: BarberShopFrame (265 x 319: UI-Barbershop TexCoords 0..0.517578 x 0..0.623047 of 512 x 512)
# and BarberShopBannerFrame (381 x 210: UI-Barbershop-Banner 0..0.744141 x 0..0.820313 of 512 x 256) at 0.6 x 0.55, T8;
# the money frame's UI-MoneyFrame-Border 128 x 32. "go_barber": BarberShopStyle.dbc (ruRU names), gtBarberShopCostBase,
# the selectors' labels per race (ChrRaces.dbc FacialHairCustomization[2] 65 / 66, HairCustomization 67 ->
# GlobalStrings ruRU).
BARBER_SX, BARBER_SY = 0.6, 0.55


def barber_data(root: str) -> list[tuple[str, int, bytes]]:
    out: list[tuple[str, int, bytes]] = []
    import build_glue_pak  # noqa: E402
    import re  # noqa: E402
    ru = build_glue_pak.RU
    dbc = os.path.join(root, "dbfilesclient")
    gpath = os.path.join(root, "interface", "framexml", "globalstrings.lua")
    need = [os.path.join(dbc, n) for n in ("barbershopstyle.dbc", "gtbarbershopcostbase.dbc", "chrraces.dbc")]
    if all(os.path.exists(v) for v in need) and os.path.exists(gpath):
        gs = {k: v.replace('\\"', '"') for k, v in
              re.findall(r'^([A-Z0-9_]+) = "(.*)";\s*$', open(gpath, encoding="utf-8", errors="replace").read(), re.M)}
        pool = bytearray(b"\0")
        offs: dict[str, int] = {"": 0}

        def intern(text: str) -> int:
            if text not in offs:
                offs[text] = len(pool)
                pool.extend(text.encode("utf-8") + b"\0")
            return offs[text]

        rows, sstr = build_glue_pak.read_dbc(need[0])
        styles = []
        for r in sorted(rows, key=lambda r: (r[37], r[38], r[1], r[39])):  # Race, Sex, Type, Data
            name = sstr(r[2 + ru]) or next((sstr(r[2 + k]) for k in range(16) if sstr(r[2 + k])), "")
            cost = struct.unpack("<f", struct.pack("<I", r[36]))[0]
            styles.append(struct.pack("<HBBBBHfI", r[0], r[1] & 0xFF, r[37] & 0xFF, r[38] & 0xFF, r[39] & 0xFF, 0, cost,
                                      intern(name)))
        cost_rows, _ = build_glue_pak.read_dbc(need[1])
        costs = [struct.unpack("<f", struct.pack("<I", r[0]))[0] for r in cost_rows]
        race_rows, rstr = build_glue_pak.read_dbc(need[2])
        colors: dict[tuple[int, int], int] = {}
        if os.path.exists(os.path.join(dbc, "charsections.dbc")):
            cs_rows, _ = build_glue_pak.read_dbc(os.path.join(dbc, "charsections.dbc"))
            for r in cs_rows:  # ID, Race, Sex, BaseSection, Texture[3], Flags, VariationIndex, ColorIndex
                if r[3] == 3 and r[8] == 0 and (r[7] & 0x10):  # Flags 0x10: the character creation's (not the NPCs')
                    colors[(r[1], r[2])] = max(colors.get((r[1], r[2]), 0), r[9] + 1)
        races = [struct.pack("<4IBBH", 0, 0, 0, 0, 0, 0, 0)] * 12
        for r in race_rows:
            if r[0] >= 12:
                continue
            hair = rstr(r[67]) or "NORMAL"
            fac = [rstr(r[65]) or "NORMAL", rstr(r[66]) or "NORMAL"]
            races[r[0]] = struct.pack("<4IBBH", intern(gs.get(f"HAIR_{hair}_STYLE", "")),
                                      intern(gs.get(f"HAIR_{hair}_COLOR", "")), intern(gs.get(f"FACIAL_HAIR_{fac[0]}", "")),
                                      intern(gs.get(f"FACIAL_HAIR_{fac[1]}", "")), min(colors.get((r[0], 0), 0), 255),
                                      min(colors.get((r[0], 1), 0), 255), 0)
        while len(pool) % 4:
            pool.append(0)
        blob = (struct.pack("<4sHHHHI", b"WGBS", len(styles), len(costs), len(races), 0, len(pool)) + b"".join(styles) +
                struct.pack(f"<{len(costs)}f", *costs) + b"".join(races) + bytes(pool))
        out.append(("go_barber", 3, blob))
    art = "interface/barbershop/"
    if os.path.exists(os.path.join(root, art + "ui-barbershop.png")):
        img = _part(root, art + "ui-barbershop.png", size=(512, 512)).crop((0, 0, 265, 319))
        img = img.resize((round(265 * BARBER_SX), round(319 * BARBER_SY)), Image.LANCZOS)
        out.append(("barber_frame", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    if os.path.exists(os.path.join(root, art + "ui-barbershop-banner.png")):
        img = _part(root, art + "ui-barbershop-banner.png", size=(512, 256)).crop((0, 0, 381, 210))
        img = img.resize((round(381 * BARBER_SX), round(210 * BARBER_SY)), Image.LANCZOS)
        out.append(("barber_banner", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    border = "interface/moneyframe/ui-moneyframe-border.png"
    if os.path.exists(os.path.join(root, border)):
        img = _part(root, border, size=(128, 32)).resize((round(128 * BARBER_SX), round(32 * BARBER_SY)), Image.LANCZOS)
        out.append(("barber_money", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "4444", linear=True))))
    return out
# game objects (end)

# FriendsFrame's Friends / Ignore, Who and Guild tabs and the guild's side frames of the
# original (src/ui/psp/ui_world_social.cpp, ui_world_guild.cpp). The FriendsFrame per tab as
# FriendsFrame_Update sets its four textures (FriendsFrameScrollIcon 60 at 7,-6 under them), cut like rf_frame (x 0..352,
# y 0..482 at 0.6 x 256 px, drawn 211 x 265): "sf_friends" (UI-FriendsFrame-TopLeft/-TopRight-bnet +
# UI-FriendsFrameMute-BotLeft/-BotRight-bnet: the Friends and the Ignore header tabs - voice chat off), "sf_who"
# (UI-ClassTrainer-TopLeft/-TopRight + WhoFrame-BotLeft/-BotRight), "sf_guild" (UI-ClassTrainer + GuildFrame-BotLeft/
# -BotRight). The FauxScrollFrames' bars (only while a list scrolls): "sf_who_scroll" (WhoListScrollFrame: the
# UI-Character-ScrollBar strip y 91..385 at x 315), "sf_guild_scroll" (GuildListScrollFrame: y 93..337). The guild's
# side frames at 0.6 x 0.55: "sf_detail" / "sf_detail_n" (GuildMemberDetailFrame 212 x 255 with the officer note /
# 195 without: the dialog backdrop, UI-GuildMember-Patch at BOTTOMLEFT 12,3, UI-DialogBox-Corner at TOPRIGHT -6,-7;
# sf_guild has GuildFrameLFGFrame's UI-ClassTrainer-FilterBorder), "sf_info" (GuildInfoFrame 297 x 298), "sf_log"
# (GuildEventLogFrame 395 x 435), "sf_ctrl" (GuildControlPopupFrame 320 x 457: MacroPopup-TopLeft / -TopRight /
# -BotLeft / -BotRight and their stretched middles) - the tooltip-backdrop boxes inside them are drawn at run time.
# Small art: the friends' status icons (StatusIcon-Online / -Away / -DnD / -Offline in one 64 x 16 atlas), the
# offline divider, the guild rows' highlight bar, the column header tab and its highlight, the promote / demote
# arrows (UI-MainMenu-ScrollUp/DownButton Up / Down / Disabled / Highlight, a 64 x 32 atlas), the bank tab
# permission tab, the input box (Common-Input-Border, three slices at run time). All optional.
SF_SX, SF_SY, SF_W, SF_H = 0.6, 0.55, 352, 482
TEXTURES += [
    (name, rel, "image", dict(opts, linear=True, optional=True))
    for name, rel, opts in (
        ("sf_divider", "interface/friendsframe/ui-friendsframe-onlinedivider.png", dict(size=(128, 16), fmt="4444")),
        ("sf_hlbar", "interface/friendsframe/ui-friendsframe-highlightbar.png", dict(size=(128, 16), fmt="4444")),
        ("sf_coltab", "interface/friendsframe/whoframe-columntabs.png", dict(size=(64, 32), fmt="4444")),
        ("sf_colhl", "interface/paperdollinfoframe/ui-character-tab-highlight.png", dict(size=(64, 16), fmt="4444")),
        ("sf_permtab", "interface/guildbankframe/ui-guildframe-permissiontab.png", dict(size=(32, 32), fmt="4444")),
        ("sf_input", "interface/common/common-input-border.png", dict(crop=(0, 0, 128, 20), size=(64, 10),
                                                                       fmt="4444")),
    )
]


def social_frames(root: str) -> list[tuple[str, int, bytes]]:
    """Social: the FriendsFrame per tab, the scroll bars, the guild's side frames, the status / arrow atlases."""
    out: list[tuple[str, int, bytes]] = []

    def ex(rel: str) -> bool:
        return os.path.exists(os.path.join(root, rel))

    def t8(img: Image.Image) -> bytes:
        return tc.wtex_blob(tc.encode(img, "t8", linear=True))

    ff, ct = "interface/friendsframe/", "interface/classtrainerframe/"
    frames = (("sf_friends", (ff + "ui-friendsframe-topleft-bnet.png", ff + "ui-friendsframe-topright-bnet.png",
                              ff + "ui-friendsframemute-botleft-bnet.png", ff + "ui-friendsframemute-botright-bnet.png")),
              ("sf_who", (ct + "ui-classtrainer-topleft.png", ct + "ui-classtrainer-topright.png",
                          ff + "whoframe-botleft.png", ff + "whoframe-botright.png")),
              ("sf_guild", (ct + "ui-classtrainer-topleft.png", ct + "ui-classtrainer-topright.png",
                            ff + "guildframe-botleft.png", ff + "guildframe-botright.png")))
    icon = ff + "friendsframescrollicon.png"
    for name, (tl, tr, bl, br) in frames:
        if not all(ex(r) for r in (tl, tr, bl, br)):
            print(f"  skip {name} (optional, missing {tl} ...)")
            continue
        c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
        if ex(icon):
            c.alpha_composite(_part(root, icon, size=(60, 60)), (7, 6))
        c.alpha_composite(_part(root, tl, size=(256, 256)), (0, 0))
        c.alpha_composite(_part(root, tr, size=(128, 256)), (256, 0))
        c.alpha_composite(_part(root, bl, size=(256, 256)), (0, 256))
        c.alpha_composite(_part(root, br, size=(128, 256)), (256, 256))
        fb = ct + "ui-classtrainer-filterborder.png"
        if name == "sf_guild" and ex(fb):  # GuildFrameLFGFrame (210 x 23 at TOPRIGHT -42,-38): Left / Middle / Right 28 high
            c.alpha_composite(_part(root, fb, crop=(0, 0, 12, 32), size=(12, 28)), (342 - 12 - 186 - 12, 38))
            c.alpha_composite(_part(root, fb, crop=(12, 0, 116, 32), size=(186, 28)), (342 - 12 - 186, 38))
            c.alpha_composite(_part(root, fb, crop=(116, 0, 128, 32), size=(12, 28)), (342 - 12, 38))
        out.append((name, KIND_TEXTURE, t8(c.crop((0, 0, SF_W, SF_H)).resize((round(SF_W * SF_SX), 256),
                                                                              Image.LANCZOS))))
    sb = "interface/paperdollinfoframe/ui-character-scrollbar.png"
    if ex(sb):  # FauxScrollFrame textures: 31 x top at TOPLEFT.TOPRIGHT -2,5, 31 x 106 at BOTTOMLEFT.BOTTOMRIGHT -2,-2
        for name, top_h, y0, y1 in (("sf_who_scroll", 256, 91, 385), ("sf_guild_scroll", 226, 93, 337)):
            strip = Image.new("RGBA", (31, y1 - y0), (0, 0, 0, 0))
            strip.alpha_composite(_part(root, sb, crop=(0, 0, 31, top_h)), (0, 0))
            strip.alpha_composite(_part(root, sb, crop=(33, 0, 64, 106)), (0, y1 - y0 - 106))
            out.append((name, KIND_TEXTURE, t8(strip.resize((round(31 * SF_SX), round((y1 - y0) * SF_SY)),
                                                            Image.LANCZOS))))
    dlg = "interface/dialogframe/"
    corner = dlg + "ui-dialogbox-corner.png"

    def dialog(w: int, h: int) -> Image.Image:
        c = _backdrop(root, w, h, dlg + "ui-dialogbox-background.png", dlg + "ui-dialogbox-border.png", (11, 12, 12, 11))
        if ex(corner):
            c.alpha_composite(_part(root, corner), (w - 6 - 32, 7))
        return c

    if ex(dlg + "ui-dialogbox-border.png"):
        dets = []
        for dname, dh in (("sf_detail", 255), ("sf_detail_n", 195)):  # GUILD_DETAIL_OFFICER_HEIGHT / _NORM_HEIGHT
            det = dialog(212, dh)
            patch = ff + "ui-guildmember-patch.png"
            if ex(patch):  # BOTTOMLEFT 12,3 (256 wide: the frame clips it at its right edge)
                det.alpha_composite(_part(root, patch).crop((0, 0, 212 - 12 - 4, 32)), (12, dh - 3 - 32))
            dets.append((dname, det))
        for name, img in dets + [("sf_info", dialog(297, 298)), ("sf_log", dialog(395, 435))]:
            out.append((name, KIND_TEXTURE, t8(img.resize((round(img.width * SF_SX), round(img.height * SF_SY)),
                                                          Image.LANCZOS))))
    mp = "interface/macroframe/macropopup-"
    if all(ex(mp + k + ".png") for k in ("topleft", "topright", "botleft", "botright")):
        w, h = 320, 457
        c = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        c.alpha_composite(_part(root, mp + "topleft.png"), (0, 0))
        c.alpha_composite(_part(root, mp + "topright.png"), (256, 0))
        mid = h - 256 - 64  # TexCoords 0..1 x 0.3125..1 stretched between the top and the bottom pieces
        c.alpha_composite(_part(root, mp + "topleft.png", crop=(0, 80, 256, 256), size=(256, mid)), (0, 256))
        c.alpha_composite(_part(root, mp + "topright.png", crop=(0, 80, 64, 256), size=(64, mid)), (256, 256))
        c.alpha_composite(_part(root, mp + "botleft.png"), (0, h - 64))
        c.alpha_composite(_part(root, mp + "botright.png"), (256, h - 64))
        out.append(("sf_ctrl", KIND_TEXTURE, t8(c.resize((round(w * SF_SX), round(h * SF_SY)), Image.LANCZOS))))
    icons = [ff + f"statusicon-{k}.png" for k in ("online", "away", "dnd", "offline")]
    if all(ex(r) for r in icons):
        a = Image.new("RGBA", (64, 16), (0, 0, 0, 0))
        for i, r in enumerate(icons):
            a.alpha_composite(_part(root, r, size=(16, 16)), (16 * i, 0))
        out.append(("sf_status", KIND_TEXTURE, tc.wtex_blob(tc.encode(a, "4444", linear=True))))
    mb = "interface/mainmenubar/ui-mainmenu-scroll"
    arrows = [mb + f"{d}button-{k}.png" for d in ("up", "down") for k in ("up", "down", "disabled", "highlight")]
    if all(ex(r) for r in arrows):
        a = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
        for i, r in enumerate(arrows):  # row 0 up, row 1 down; columns up / down / disabled / highlight
            a.alpha_composite(_part(root, r, size=(16, 16)), (16 * (i % 4), 16 * (i // 4)))
        out.append(("sf_updn", KIND_TEXTURE, tc.wtex_blob(tc.encode(a, "4444", linear=True))))
    return out
# social (end)


# The art of GlyphFrame (the client's GlyphUI addon over the talent frame), GuildBankFrame
# (GuildBankUI addon) and ChannelFrame (FriendsFrame's "Channels" tab) of the original (src/ui/psp/ui_world_glyphs.cpp,
# ui_world_guildbank.cpp, ui_world_channels.cpp). All optional; each frame's art is read
# while it shows. GlyphFrame at the talent frame's 0.6 x 0.55: "gly_frame" (UI-GlyphFrame TexCoords 0..0.6875 x
# 0..0.861 = 352 x 441 -> 211 x 243 T8), "gly_glow" (UI-GlyphFrame-Glow, the same cut, T8), "gly_parts" (a 256 x 128 4444
# atlas of the pieces at their drawn sizes: the major / minor setting 108 / 86, ring 82 / 62, the empty background and
# GLYPH_SLOTS[1..6] 70, the shines 16), "gly_locked" (UI-GlyphFrame-Locked .1-.9, 64 x 64), "gly_sparkle"
# (UI-ItemSockets' GlyphSparkleTexture, 16 x 16), "gly_rune_0" (UI-Glyph-Rune1) and "gly_rune_1..20" (UI-Glyph-Rune-N,
# 32 x 32 4444). GuildBankFrame at 0.55 x 0.525: "gb_frame" (UI-GuildBankFrame-Left + -Right at TOPRIGHT 0,-11, 769 x 444
# -> 423 x 233 T8), "gb_emblem" (GuildBankEmblemFrame's -EmblemBorder halves, x 0..769 of the frame, 80 high, T8),
# "gb_cols" (UI-GuildBankFrame-Slots 0..0.78125 x 0..0.607 = a column, 55 x 163), "gb_tab" (UI-GuildBankFrame-Tab),
# "gb_newtab", "gb_namebd" (UI-TabNameBorder). ChannelFrame: "sf_channel" (FriendsFrame with UI-Character-General-Top* and
# UI-ChannelFrame-Bot*, cut as sf_friends: 211 x 256 T8), "ch_rowbg" (UI-AuctionFrame-FilterBg 0..0.53125 x 0..0.625),
# "ch_hl" (UI-Character-Tab-Highlight), "ch_owner" / "ch_mod" (UI-Group-LeaderIcon / -AssistantIcon).
GLY_SX, GLY_SY = 0.6, 0.55
GB_SX, GB_SY = 0.55, 0.525


def glyphs_guildbank_art(root: str) -> list[tuple[str, int, bytes]]:
    """Glyphs / guild bank: GlyphFrame, GuildBankFrame and ChannelFrame art."""
    out: list[tuple[str, int, bytes]] = []

    def ex(rel: str) -> bool:
        return os.path.exists(os.path.join(root, rel))

    def t8(img: Image.Image) -> bytes:
        return tc.wtex_blob(tc.encode(img, "t8", linear=True))

    def c4(img: Image.Image) -> bytes:
        return tc.wtex_blob(tc.encode(img, "4444", linear=True))

    def tcrop(rel: str, l: float, r: float, t: float, b: float) -> Image.Image:  # TexCoords -> the source piece
        im = Image.open(os.path.join(root, rel)).convert("RGBA")
        w, h = im.size
        return im.crop((round(l * w), round(t * h), round(r * w), round(b * h)))

    sb = "interface/spellbook/"
    gf = sb + "ui-glyphframe.png"
    if ex(gf):
        frame = tcrop(gf, 0, 0.6875, 0, 0.861328125)
        out.append(("gly_frame", KIND_TEXTURE,
                    t8(frame.resize((round(352 * GLY_SX), round(441 * GLY_SY)), Image.LANCZOS))))
        atlas = Image.new("RGBA", (256, 128), (0, 0, 0, 0))
        slots = {0: (0.78125, 0.91015625, 0.69921875, 0.828125), 1: (0, 0.12890625, 0.87109375, 1),
                 2: (0.130859375, 0.259765625, 0.87109375, 1), 3: (0.392578125, 0.521484375, 0.87109375, 1),
                 4: (0.5234375, 0.65234375, 0.87109375, 1), 5: (0.26171875, 0.390625, 0.87109375, 1),
                 6: (0.654296875, 0.783203125, 0.87109375, 1)}  # GLYPH_SLOTS (the GlyphUI addon's .lua)
        pieces = [((0.740234375, 0.953125, 0.484375, 0.697265625), (0, 0, 65, 59)),       # major setting 108
                  ((0.765625, 0.927734375, 0.15625, 0.31640625), (65, 0, 52, 47)),         # minor setting 86
                  ((0.767578125, 0.92578125, 0.32421875, 0.482421875), (117, 0, 49, 45)),  # major ring 82
                  ((0.787109375, 0.908203125, 0.033203125, 0.154296875), (166, 0, 37, 34)),  # minor ring 62
                  (slots[0], (203, 0, 42, 39)),                                            # empty background
                  ((0.9609375, 1, 0.9609375, 1), (0, 100, 10, 9)),                         # major shine
                  ((0.9609375, 1, 0.921875, 0.9609375), (10, 100, 10, 9))]                 # minor shine
        pieces += [(slots[i], (42 * (i - 1), 60, 42, 39)) for i in range(1, 7)]
        for (l, r, t, b), (x, y, w, h) in pieces:
            atlas.alpha_composite(tcrop(gf, l, r, t, b).resize((w, h), Image.LANCZOS), (x, y))
        out.append(("gly_parts", KIND_TEXTURE, c4(atlas)))
    if ex(sb + "ui-glyphframe-glow.png"):
        glow = tcrop(sb + "ui-glyphframe-glow.png", 0, 0.6875, 0, 0.861328125)
        out.append(("gly_glow", KIND_TEXTURE,
                    t8(glow.resize((round(352 * GLY_SX), round(441 * GLY_SY)), Image.LANCZOS))))
    if ex(sb + "ui-glyphframe-locked.png"):
        out.append(("gly_locked", KIND_TEXTURE,
                    c4(tcrop(sb + "ui-glyphframe-locked.png", 0.1, 0.9, 0.1, 0.9).resize((64, 64), Image.LANCZOS))))
    sock = "interface/itemsocketingframe/ui-itemsockets.png"
    if ex(sock):
        out.append(("gly_sparkle", KIND_TEXTURE,
                    c4(tcrop(sock, 0.3984375, 0.4453125, 0.40234375, 0.44921875).resize((16, 16), Image.LANCZOS))))
    runes = [(0, sb + "ui-glyph-rune1.png")] + [(i, sb + f"ui-glyph-rune-{i}.png") for i in range(1, 21)]
    for i, rel in runes:
        if ex(rel):
            out.append((f"gly_rune_{i}", KIND_TEXTURE, c4(_part(root, rel, size=(32, 32)))))
    gb = "interface/guildbankframe/"
    if ex(gb + "ui-guildbankframe-left.png") and ex(gb + "ui-guildbankframe-right.png"):
        c = Image.new("RGBA", (1024, 523), (0, 0, 0, 0))
        c.alpha_composite(_part(root, gb + "ui-guildbankframe-left.png"), (0, 0))
        c.alpha_composite(_part(root, gb + "ui-guildbankframe-right.png"), (512, 11))
        out.append(("gb_frame", KIND_TEXTURE,
                    t8(c.crop((0, 0, 769, 444)).resize((round(769 * GB_SX), round(444 * GB_SY)), Image.LANCZOS))))
    eb = gb + "ui-guildbankframe-emblemborder.png"
    if ex(eb):  # GuildBankEmblemFrame (700 x 80 at TOP -70,46): the left half at 0,0, the right at its TOPRIGHT 0,-1
        c = Image.new("RGBA", (1100, 80), (0, 0, 0, 0))
        x0 = 36  # the frame's x 0 in this canvas (the emblem frame starts at x -35.5)
        c.alpha_composite(tcrop(eb, 0, 1, 0, 0.3125).resize((512, 80), Image.LANCZOS), (0, 0))
        c.alpha_composite(tcrop(eb, 0, 1, 0.31640625, 0.625).resize((512, 79), Image.LANCZOS), (512, 1))
        out.append(("gb_emblem", KIND_TEXTURE,
                    t8(c.crop((x0, 0, x0 + 769, 80)).resize((round(769 * GB_SX), round(80 * GB_SY)), Image.LANCZOS))))
    if ex(gb + "ui-guildbankframe-slots.png"):
        col = tcrop(gb + "ui-guildbankframe-slots.png", 0, 0.78125, 0, 0.607421875)
        out.append(("gb_cols", KIND_TEXTURE,
                    c4(col.resize((round(100 * GB_SX), round(311 * GB_SY)), Image.LANCZOS))))
    for name, rel, size in (("gb_tab", "ui-guildbankframe-tab.png", (64, 64)),
                            ("gb_newtab", "ui-guildbankframe-newtab.png", (32, 32)),
                            ("gb_namebd", "ui-tabnameborder.png", (128, 32))):
        if ex(gb + rel):
            out.append((name, KIND_TEXTURE, c4(_part(root, gb + rel, size=size))))
    ff, pd = "interface/friendsframe/", "interface/paperdollinfoframe/"
    quarters = (pd + "ui-character-general-topleft.png", pd + "ui-character-general-topright.png",
                ff + "ui-channelframe-botleft.png", ff + "ui-channelframe-botright.png")
    if all(ex(r) for r in quarters):  # FriendsFrame_Update for the channel tab (as sf_friends: the scroll icon under it)
        c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
        icon = ff + "friendsframescrollicon.png"
        if ex(icon):
            c.alpha_composite(_part(root, icon, size=(60, 60)), (7, 6))
        c.alpha_composite(_part(root, quarters[0], size=(256, 256)), (0, 0))
        c.alpha_composite(_part(root, quarters[1], size=(128, 256)), (256, 0))
        c.alpha_composite(_part(root, quarters[2], size=(256, 256)), (0, 256))
        c.alpha_composite(_part(root, quarters[3], size=(128, 256)), (256, 256))
        out.append(("sf_channel", KIND_TEXTURE,
                    t8(c.crop((0, 0, SF_W, SF_H)).resize((round(SF_W * SF_SX), 256), Image.LANCZOS))))
    small = (("ch_rowbg", "interface/auctionframe/ui-auctionframe-filterbg.png", (0, 0.53125, 0, 0.625),
              (round(155 * SF_SX), round(20 * SF_SY))),
             ("ch_hl", pd + "ui-character-tab-highlight.png", (0, 1, 0, 1), (64, 16)),
             ("ch_owner", "interface/groupframe/ui-group-leadericon.png", (0, 1, 0, 1), (16, 16)),
             ("ch_mod", "interface/groupframe/ui-group-assistanticon.png", (0, 1, 0, 1), (16, 16)))
    for name, rel, (l, r, t, b), size in small:
        if ex(rel):
            out.append((name, KIND_TEXTURE, c4(tcrop(rel, l, r, t, b).resize(size, Image.LANCZOS))))
    return out
# glyphs / guild bank (end)


# TradeFrame.xml of the original (src/ui/psp/ui_world_trade_duel.cpp). 384 x 512 units,
# drawn x 0..360 (the close button ends at 359) y 0..462 (the "Trade" / "Cancel" buttons end at 457), baked at the
# merchant's 0.6 x 0.55 = 216 x 254 px - one 256 x 256 T8 (65 KB, only while a trade is open). In it: the
# UI-TradeFrame quarters (the portraits' holes stay clear: the PSP draws the portraits under it), per side the seven
# TradeItemTemplate slots (UI-EmptySlot 64 at -13,13; UI-QuestItemNameFrame 124 x 64 LEFT of the slot texture's RIGHT
# -20; the ItemButton's NormalTexture UI-Quickslot2 64 at its CENTER 0,-1; the 7th's UI-TradeFrame-EnchantIcon 62 at
# 0,0), TradePlayerInputMoneyFrame's boxes in "compact" mode (gold 56 wide, silver +22, copper +11, 30 each;
# Common-Input-Border) with their UI-MoneyIcons coins. "trade_hl": UI-TradeFrame-Highlight (TexCoords right
# 0.62890625 = 161 of 256) at 64 x 128, 4444 - the green of an accepted side, drawn in three slices.
TRADE_SX, TRADE_SY, TRADE_W, TRADE_H = 0.6, 0.55, 360, 462


def trade_frames(root: str) -> list[tuple[str, bytes]]:
    """Trade: "trade_frame" (T8 216 x 254) and "trade_hl" (4444 64 x 128). Optional (without them the
    PSP draws the generic panel)."""
    t, b, q, mf = "interface/tradeframe/", "interface/buttons/", "interface/questframe/", "interface/moneyframe/"
    need = [t + f"ui-tradeframe-{k}.png" for k in ("topleft", "topright", "botleft", "botright", "enchanticon")]
    need += [b + "ui-emptyslot.png", b + "ui-quickslot2.png", q + "ui-questitemnameframe.png"]
    if not all(os.path.exists(os.path.join(root, r)) for r in need):
        return []
    c = Image.new("RGBA", (384, 512), (0, 0, 0, 0))
    for rel, at, size in ((t + "ui-tradeframe-topleft.png", (0, 0), (256, 256)),
                          (t + "ui-tradeframe-topright.png", (256, 0), (128, 256)),
                          (t + "ui-tradeframe-botleft.png", (0, 256), (256, 256)),
                          (t + "ui-tradeframe-botright.png", (256, 256), (128, 256))):
        c.alpha_composite(_part(root, rel, size=size), at)
    slot = _part(root, b + "ui-emptyslot.png", size=(64, 64))
    quick = _part(root, b + "ui-quickslot2.png", size=(64, 64))
    name = _part(root, q + "ui-questitemnameframe.png", size=(124, 64))
    enchant = _part(root, t + "ui-tradeframe-enchanticon.png", size=(62, 62))
    for x in (26, 195):  # TradePlayerItem1 TOPLEFT 26,-104 / TradeRecipientItem1 195,-104
        for i in range(7):
            y = 104 + min(i, 5) * 44 + (65 if i == 6 else 0)  # 7 apart (37 high), the 7th 28 under the 6th
            c.alpha_composite(slot, (x - 13, y - 13))
            c.alpha_composite(name, (x - 13 + 64 - 20, y - 13))
            if i == 6:
                c.alpha_composite(enchant, (x, y))
            c.alpha_composite(quick, (x - 13, y - 12))
    _input_border(root, c, 21, 73, 45, right_at=74)    # gold: 26,-73, 56 wide (compact)
    _input_border(root, c, 99, 73, 9, right_at=116)    # silver: 22 right of it, 30 wide, its cap at RIGHT -10
    _input_border(root, c, 140, 73, 9, right_at=157)   # copper: 11 right of the silver
    coins = mf + "ui-moneyicons.png"
    if os.path.exists(os.path.join(root, coins)):
        for k, x in enumerate((84, 126, 167)):  # gold LEFT at the box's RIGHT +2, silver / copper at RIGHT -8
            c.alpha_composite(_part(root, coins, crop=(16 * k, 0, 16 * k + 16, 16), size=(13, 13)), (x, 76))
    img = c.crop((0, 0, TRADE_W, TRADE_H)).resize((round(TRADE_W * TRADE_SX), round(TRADE_H * TRADE_SY)),
                                                  Image.LANCZOS)
    out = [("trade_frame", tc.wtex_blob(tc.encode(img, "t8", linear=True)))]
    hl = t + "ui-tradeframe-highlight.png"
    if os.path.exists(os.path.join(root, hl)):
        im = _part(root, hl, size=(256, 256)).crop((0, 0, 161, 256)).resize((64, 128), Image.LANCZOS)
        out.append(("trade_hl", tc.wtex_blob(tc.encode(im, "4444", linear=True))))
    return out
# trade / duel (end)


FONTS = [
    # name, path, pixel size
    ("font_small", "fonts/frizqt__.ttf", 10),
    ("font_normal", "fonts/frizqt__.ttf", 13),
    ("font_title", "fonts/morpheus.ttf", 18),
    ("font_tiny", "fonts/frizqt__.ttf", 8),  # dense blocks (PaperDoll stats)
]


def load_rgba(root: str, rel: str) -> Image.Image:
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        raise FileNotFoundError(f"missing {rel} under {root} - re-run asset_extract with --emit-png")
    return Image.open(path).convert("RGBA")


def resized(img: Image.Image, opts: dict) -> Image.Image:
    if "crop" in opts:
        img = img.crop(opts["crop"])
    if opts.get("tight"):
        # Crop to the visible (alpha) box, centred in a square - the Kenney
        # prompts have different empty margins (face 48/64, L/R 44x40, START 34x36),
        # so the same draw rect gave glyphs of different sizes in a line of text.
        box = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        if box:
            img = img.crop(box)
            side = max(img.width, img.height)
            sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            sq.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
            img = sq
    if "size" in opts:
        img = img.resize(opts["size"], Image.LANCZOS)
    elif "scale" in opts:
        s = opts["scale"]
        img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
    return img


def edge_to_nine(strip: Image.Image, edge_px: int) -> Image.Image:
    """Client backdrop edge file (8 cells: L, R, T, B, TL, TR, BL, BR; top and
    bottom stored rotated) -> a plain 3x3 9-slice picture, centre empty."""
    e = strip.height
    cells = [strip.crop((i * e, 0, (i + 1) * e, e)) for i in range(8)]
    left, right, top, bottom, tl, tr, bl, br = cells
    top = top.transpose(Image.ROTATE_270)
    bottom = bottom.transpose(Image.ROTATE_270)
    nine = Image.new("RGBA", (3 * e, 3 * e), (0, 0, 0, 0))
    nine.paste(tl, (0, 0)); nine.paste(top, (e, 0)); nine.paste(tr, (2 * e, 0))
    nine.paste(left, (0, e)); nine.paste(right, (2 * e, e))
    nine.paste(bl, (0, 2 * e)); nine.paste(bottom, (e, 2 * e)); nine.paste(br, (2 * e, 2 * e))
    return nine.resize((3 * edge_px, 3 * edge_px), Image.LANCZOS)


def build_texture(root: str, kind: str, rel: str, opts: dict) -> bytes:
    img = load_rgba(root, rel)
    if kind == "edge":
        edge = opts["edge"]
        nine = edge_to_nine(img, edge)
        enc = tc.encode(nine, opts.get("fmt", "4444"), linear=True, slice_lrtb=(edge, edge, edge, edge))
    elif kind == "button":
        img = resized(img, opts)
        cap = opts["cap"]
        enc = tc.encode(img, "4444", linear=True, slice_lrtb=(cap, 0, cap, 0))
    else:
        img = resized(img, opts)
        if opts.get("flip"):  # mirrored art (player frame)
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if opts.get("mask") == "circle":  # round portrait (SetPortraitToTexture)
            m = Image.new("L", img.size, 0)
            ImageDraw.Draw(m).ellipse((0, 0, img.width - 1, img.height - 1), fill=255)
            a = np.minimum(np.asarray(img.getchannel("A")), np.asarray(m))
            img.putalpha(Image.fromarray(a.astype(np.uint8), "L"))
        enc = tc.encode(img, opts.get("fmt", "auto"), linear=opts.get("linear", False))
    return tc.wtex_blob(enc)


def build_font(root: str, rel: str, px: int) -> bytes:
    font = ImageFont.truetype(os.path.join(root, rel), px)
    ascent, descent = font.getmetrics()
    atlas_w = 256
    glyphs = []
    x = y = 0
    row_h = 0
    pad = 1
    bitmaps = []

    def ink(f, ch):  # what a TTF draws for ch: its box, advance and pixels
        bb = f.getbbox(ch)
        im = Image.new("L", (max(1, bb[2] - bb[0]), max(1, bb[3] - bb[1])), 0)
        ImageDraw.Draw(im).text((-bb[0], -bb[1]), ch, font=f, fill=255)
        return bb, int(round(f.getlength(ch))), im.tobytes()

    def has(f, ch):
        """f has a real glyph for ch - not the .notdef box, and not a CP1251 letter in a Latin-1 slot (the
        ruRU client's FRIZQT__.TTF and FRIENDS.TTF draw Cyrillic ze for "ç": the old one-byte Russian codepage)."""
        if ch.isspace() or ord(ch) <= 0x7E:
            return True
        drawn = ink(f, ch)
        if drawn == ink(f, "\U0010FFFD"):
            return False
        if 0xA0 <= ord(ch) <= 0xFF:
            alias = bytes([ord(ch)]).decode("cp1251", "replace")
            if alias != ch and drawn == ink(f, alias):
                return False
        return True

    # A letter the font lacks comes from the client's Arial Narrow (fonts/arialn.ttf: Latin-1 and Cyrillic in
    # both the ruRU and the enUS client) at the same size, its baseline on the font's; none has it - left out, the
    # PSP draws "?".
    fallback = None
    fb_path = os.path.join(root, "fonts", "arialn.ttf")
    if rel.lower() != "fonts/arialn.ttf" and os.path.exists(fb_path):
        fallback = ImageFont.truetype(fb_path, px)
    skipped = borrowed = 0
    for ch in sorted(set(CHARSET), key=ord):
        f, dy = font, 0
        if not has(font, ch):
            if fallback is not None and has(fallback, ch):
                f, dy = fallback, ascent - fallback.getmetrics()[0]
                borrowed += 1
            else:
                skipped += 1
                continue
        advance = int(round(f.getlength(ch)))
        bbox = f.getbbox(ch)  # relative to the ascender line
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= 0 or h <= 0 or ch.isspace():
            glyphs.append((ord(ch), 0, 0, 0, 0, 0, 0, advance))
            continue
        if x + w + pad > atlas_w:
            x = 0
            y += row_h + pad
            row_h = 0
        glyph_img = Image.new("L", (w, h), 0)
        ImageDraw.Draw(glyph_img).text((-bbox[0], -bbox[1]), ch, font=f, fill=255)
        bitmaps.append((x, y, glyph_img))
        glyphs.append((ord(ch), x, y, w, h, bbox[0], bbox[1] + dy, advance))
        x += w + pad
        row_h = max(row_h, h)
    atlas_h = y + row_h
    if skipped or borrowed:
        print(f"  font {rel} {px}px: {borrowed} letters from arialn.ttf, {skipped} in neither (the PSP draws '?')")
    atlas = Image.new("RGBA", (atlas_w, max(8, atlas_h)), (255, 255, 255, 0))
    for gx, gy, gimg in bitmaps:
        solid = Image.new("RGBA", gimg.size, (255, 255, 255, 255))
        solid.putalpha(gimg)
        atlas.paste(solid, (gx, gy))
    tex = tc.wtex_blob(tc.encode(atlas, "t4alpha"))

    glyphs.sort(key=lambda g: g[0])
    glyph_bytes = b"".join(
        struct.pack("<IHHBBbbB3x", cp, gx, gy, w, h, max(-128, min(127, bx)), max(-128, min(127, top)),
                    max(0, min(255, adv)))
        for cp, gx, gy, w, h, bx, top, adv in glyphs
    )
    glyph_off = 32
    tex_off = (glyph_off + len(glyph_bytes) + 63) // 64 * 64
    header = b"WFNT" + struct.pack("<HHhhHHIIII", 1, len(glyphs), ascent + descent, ascent, px, 0,
                                   glyph_off, tex_off, len(tex), 0)
    assert len(header) == 32
    body = header + glyph_bytes
    body += b"\0" * (tex_off - len(body))
    return body + tex


def write_pak(path: str, entries: list[tuple[str, int, bytes]]) -> None:
    # The PSP finds a record by its name (the first in the TOC wins), so a second record of the
    # same name - easy after stitching the entry lists of several tasks by hand - would be dead weight hiding a bug.
    # Fail before anything is written (every pak builder of tools/psp goes through here).
    seen: set[str] = set()
    dups = sorted({name for name, _, _ in entries if name in seen or seen.add(name)})
    if dups:
        raise ValueError(f"{path}: duplicate entry names ({len(dups)}): {', '.join(dups[:20])}")
    toc_size = 64 * len(entries)
    offset = (16 + toc_size + 63) // 64 * 64
    toc = b""
    blobs = []
    for name, kind, blob in entries:
        if len(name.encode()) >= 48:
            raise ValueError(f"entry name too long: {name}")
        toc += struct.pack("<48sIIII", name.encode(), offset, len(blob), kind, 0)
        blobs.append((offset, blob))
        offset = (offset + len(blob) + 63) // 64 * 64
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"WPAK" + struct.pack("<III", 1, len(entries), 16))
        f.write(toc)
        for off, blob in blobs:
            f.write(b"\0" * (off - f.tell()))
            f.write(blob)


def dump_pak(path: str) -> int:
    """Lists a WPAK: name, kind, size and, for textures, storage/content size and psm."""
    with open(path, "rb") as f:
        data = f.read()
    magic, version, count, toc = data[:4], *struct.unpack_from("<III", data, 4)
    if magic != b"WPAK":
        print(f"{path}: not a WPAK")
        return 1
    print(f"{path}: {count} entries, version {version}")
    for i in range(count):
        name, off, size, kind, _ = struct.unpack_from("<48sIIII", data, toc + 64 * i)
        name = name.split(b"\0", 1)[0].decode()
        extra = ""
        if kind == KIND_TEXTURE and data[off:off + 4] == b"WTEX":
            psm, swz, flags, mips = data[off + 4:off + 8]
            w, h, uw, uh = struct.unpack_from("<HHHH", data, off + 8)
            extra = f"  {w}x{h} (content {uw}x{uh}) psm={psm} swizzled={swz} flags=0x{flags:02x}"
        print(f"  {name:20s} kind={kind} {size:7d} B{extra}")
    missing = [n for n in ("uf_target", "uf_player", "uf_bar") if n.encode() not in data]
    print("unit frame art: " + ("ok" if not missing else "missing " + ", ".join(missing)))
    # talent art (optional)
    names = [struct.unpack_from("<48s", data, toc + 64 * i)[0].split(b"\0", 1)[0] for i in range(count)]
    print(f"talent art: backgrounds={sum(1 for n in names if n.startswith(b'tb_'))} "
          f"rank_border={'ok' if b'tal_rank' in names else 'missing'}")
    missing_mm = [n for n in ("mm_border", "mm_arrow") if n.encode() not in data]  # map
    print("minimap art: " + ("ok" if not missing_mm else "missing " + ", ".join(missing_mm)))
    missing_wm = [n for n in ("wm_top", "wm_bottom", "wm_sides", "wm_dd", "wm_font", "wm_conts")
                  if n.encode() not in names]  # world map
    print("world map art: " + ("ok" if not missing_wm else "missing " + ", ".join(missing_wm))
          + f", highlights={sum(1 for n in names if n.startswith(b'wmhl_'))}")
    missing_mc = [n for n in ("mm_cluster", "mm_pvp") if n.encode() not in names]  # minimap cluster
    print("minimap cluster: " + ("ok" if not missing_mc else "missing " + ", ".join(missing_mc))
          + (", compass" if b"mm_compass" in names else ""))
    missing_tx = [n for n in ("taxi_top", "taxi_left", "taxi_right", "taxi_bottom", "taxi_ic_cur", "taxi_ic_reach",
                              "taxi_ic_far", "taxi_ic_hl", "taxi_line") if n.encode() not in names]  # taxi
    print("taxi frame art: " + ("ok" if not missing_tx else "missing " + ", ".join(missing_tx)))
    missing_rb = [n for n in ("rf_frame", "rf_group", "rf_btn", "rc_frame", "rc_wait", "rt_icons", "bf_frame",
                              "bf_mm_a", "ws_capbar", "ws_score", "ws_icons", "ws_ui", "bg_data")
                  if n.encode() not in names]  # raid / battleground
    print("raid / battleground art: " + ("ok" if not missing_rb else "missing " + ", ".join(missing_rb)))
    missing_du = [f"du_frame_{r}" for r in DU_RACES if f"du_frame_{r}".encode() not in names]  # dressing room
    print("dressing room art: " + ("ok" if not missing_du else "missing " + ", ".join(missing_du)))
    missing_sf = [n for n in ("sf_friends", "sf_who", "sf_guild", "sf_detail", "sf_info", "sf_log", "sf_ctrl",
                              "sf_status", "sf_updn", "sf_coltab", "sf_hlbar") if n.encode() not in names]  # social
    print("social / guild art: " + ("ok" if not missing_sf else "missing " + ", ".join(missing_sf)))
    missing_gg = [n for n in ("gly_frame", "gly_parts", "gly_glow", "gly_locked", "gly_sparkle", "gly_rune_0",
                              "gly_rune_20", "gb_frame", "gb_emblem", "gb_cols", "gb_tab", "gb_newtab", "gb_namebd",
                              "sf_channel", "ch_rowbg", "ch_hl", "ch_owner", "ch_mod")
                  if n.encode() not in names]  # glyphs / guild bank
    print("glyph / guild bank / channel art: " + ("ok" if not missing_gg else "missing " + ", ".join(missing_gg)))
    return 0


# The class bars and the vehicles (src/ui/psp/ui_world_classbars.cpp). ShapeshiftBarFrame /
# PossessBarFrame: ShapeshiftBar (64 x 128, the left / right parts by texcoords) and ShapeshiftBarMiddle; the multicast
# bar: UI-TotemBar whole (the slots' backgrounds / overlays, the flyout buttons by texcoords); TotemFrame: TotemBorder,
# UI-Minimap-Background; VehicleMenuBar: the exit / pitch buttons (their NormalTexture texcoords), VehicleSeats; the
# skins' composites and the bars' overlays (class_bar_art below); the seat indicators' backgrounds (VehicleUIIndicator).
TEXTURES += [
    ("cb_ssbar", "interface/shapeshiftbar/shapeshiftbar.png", "image", dict(fmt="4444", linear=True, optional=True)),
    ("cb_ssmid", "interface/shapeshiftbar/shapeshiftbarmiddle.png", "image", dict(fmt="4444", linear=True, optional=True)),
    ("cb_totembar", "interface/buttons/ui-totembar.png", "image", dict(fmt="t8", linear=True, optional=True)),
    ("cb_totemborder", "interface/characterframe/totemborder.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("cb_totembg", "interface/minimap/ui-minimap-background.png", "image",
     dict(size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("cb_veh_exit", "interface/vehicles/ui-vehicles-button-exit-up.png", "image",
     dict(crop=(9, 9, 55, 55), size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("cb_veh_pup", "interface/vehicles/ui-vehicles-button-pitch-up.png", "image",
     dict(crop=(14, 15, 49, 50), size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("cb_veh_pdown", "interface/vehicles/ui-vehicles-button-pitchdown-up.png", "image",
     dict(crop=(14, 15, 49, 50), size=(32, 32), fmt="4444", linear=True, optional=True)),
    ("cb_vseats", "interface/vehicles/vehicleseats.png", "image", dict(fmt="4444", linear=True, optional=True)),
]

CB_VEH_SCALE = 0.49  # ui_world_classbars.cpp kVehS


def _lua_table(src: str, start: int):
    """A Lua table constructor at src[start] == '{' -> (dict / list, end). Numbers, strings ("..." / [[...]]),
    nested tables, identifiers (returned as their name)."""
    i = start + 1
    out_list, out_map = [], {}

    def skip(i):
        while i < len(src):
            if src[i].isspace():
                i += 1
            elif src.startswith("--", i):
                j = src.find("\n", i)
                i = len(src) if j < 0 else j + 1
            else:
                break
        return i

    def value(i):
        i = skip(i)
        c = src[i]
        if c == "{":
            return _lua_table(src, i)
        if src.startswith("[[", i):
            j = src.index("]]", i)
            return src[i + 2:j], j + 2
        if c == '"':
            j = src.index('"', i + 1)
            return src[i + 1:j], j + 1
        j = i
        while j < len(src) and (src[j].isalnum() or src[j] in "._-+"):
            j += 1
        tok = src[i:j]
        try:
            return float(tok), j
        except ValueError:
            return tok, j

    while True:
        i = skip(i)
        if src[i] == "}":
            return (out_map if out_map else out_list), i + 1
        key = None
        if src[i] == "[" and not src.startswith("[[", i):
            j = src.index("]", i)
            k = src[i + 1:j].strip()
            key = k.strip('"') if k.startswith('"') else int(float(k))
            i = skip(j + 1)
            assert src[i] == "="
            i += 1
        else:
            j = i
            while j < len(src) and (src[j].isalnum() or src[j] == "_"):
                j += 1
            if j > i and skip(j) < len(src) and src[skip(j)] == "=" and src[skip(j) + 1] != "=":
                key = src[i:j]
                i = skip(j) + 1
        v, i = value(i)
        if key is None:
            out_list.append(v)
        else:
            out_map[key] = v
        i = skip(i)
        if src[i] == ",":
            i += 1


def _veh_skins(root: str) -> dict:
    p = os.path.join(root, "interface/framexml/vehiclemenubar.lua")
    src = open(p, encoding="utf-8", errors="replace").read()
    at = src.index("SkinsData = {") + len("SkinsData = ")
    skins, _ = _lua_table(src, at)
    return skins


def _tex_rect(root: str, rel: str, tc4, w: int, h: int, tile: bool, color) -> Image.Image | None:
    """A texture with UI texcoords (left, right, top, bottom; > 1 repeats, left > right mirrors) sampled into w x h."""
    path = os.path.join(root, rel.replace("\\", "/").lower() + ".png")
    if not os.path.exists(path) or w <= 0 or h <= 0:
        return None
    src = np.asarray(Image.open(path).convert("RGBA")).astype(np.float32)
    sh, sw = src.shape[:2]
    l, r, t, b = tc4
    xs = (np.arange(w) + 0.5) / w
    ys = (np.arange(h) + 0.5) / h
    u = l + (r - l) * xs
    v = t + (b - t) * ys
    u = np.mod(u, 1.0) if tile or u.max() > 1.0001 or u.min() < -0.0001 else np.clip(u, 0, 0.99999)
    v = np.mod(v, 1.0) if v.max() > 1.0001 or v.min() < -0.0001 else np.clip(v, 0, 0.99999)
    px = np.clip((u * sw).astype(int), 0, sw - 1)
    py = np.clip((v * sh).astype(int), 0, sh - 1)
    out = src[py[:, None], px[None, :]].copy()
    if color:
        out[..., 0] *= color[0]
        out[..., 1] *= color[1]
        out[..., 2] *= color[2]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


def class_bar_art(root: str) -> list[tuple[str, int, bytes]]:
    """Class bars: "cb_veh_<n|m><0|1>" - VehicleMenuBar's skin (Natural / Mechanical, without / with the pitch
    parts) as VehicleMenuBar_SetSkin lays it out from SkinsData (read from the client's vehiclemenubar.lua): the numbered
    textures by layer (BACKGROUND, BORDER, ARTWORK, OVERLAY) honouring pitchHidden, the pitch slider's BG / overlay (its
    LOW strata: first) and the health / power bars' backgrounds, on a canvas of the bar's width x 128 units (16 under
    the bar's bottom) at 0.49, T8; "cb_veh_ovl_<n|m>" - the bars' overlay (the fuel tank / the bottle) 4444;
    "cb_vind_<id>" - VehicleUIIndicator's background at 64 x 64, T8. Without the files: nothing (the PSP draws fills)."""
    out = []
    if not os.path.exists(os.path.join(root, "interface/framexml/vehiclemenubar.lua")):
        print("  class bars: no vehiclemenubar.lua - no vehicle bar art (optional)")
        return out
    skins = _veh_skins(root)
    tooltip_bg = (0.09, 0.09, 0.19)  # TOOLTIP_DEFAULT_BACKGROUND_COLOR
    s = CB_VEH_SCALE
    for key, name in (("n", "Natural"), ("m", "Mechanical")):
        sk = skins[name]
        for pitch in (0, 1):
            W = sk["Overall"]["yesPitchWidth" if pitch else "noPitchWidth"]
            cw, ch = round(W * s), round(128 * s)
            canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))

            def place(fd, x_units, y_units, w_units, h_units):
                tex = fd.get("texture")
                if not tex:
                    return
                col = fd.get("vertexColor")
                col = [tooltip_bg[i] if isinstance(c, str) else c for i, c in enumerate(col)] if col else None
                tc4 = fd.get("texCoord", [0, 1, 0, 1])
                w, h = max(1, round(w_units * s)), max(1, round(h_units * s))
                img = _tex_rect(root, tex, tc4, w, h, bool(fd.get("tile")), col)
                if img is None:
                    return
                left = round(x_units * s)
                top = round(ch - (y_units + h_units + 16) * s)
                canvas.alpha_composite(img, (left, top)) if left >= 0 and top >= 0 and left + w <= cw and top + h <= ch \
                    else _paste_clipped(canvas, img, left, top)

            def anchor_x(fd, w_units):
                x = fd.get("xOfs", 0)
                return W + x - w_units if fd.get("point", "BOTTOMLEFT").endswith("RIGHT") else x

            if pitch:  # VehicleMenuBarPitchSlider (strata LOW): its BG and overlay under the art
                sl = sk["PitchSlider"]
                sx = anchor_x(sl, sl["width"])
                for part in ("PitchSliderBG", "PitchSliderOverlayThing"):
                    place(sk[part], sx, sl.get("yOfs", 0), sl["width"], sl["height"])
            numbered = [sk[i] for i in sorted(k for k in sk if isinstance(k, int))]
            for layer in ("BACKGROUND", "BORDER", "ARTWORK", "OVERLAY"):
                for fd in numbered:
                    if fd.get("layer") != layer:
                        continue
                    if int(fd.get("pitchHidden", 0)) & (pitch + 1):
                        continue
                    place(fd, anchor_x(fd, fd["width"]), fd.get("yOfs", 0), fd["width"], fd["height"])
            for bar in ("HealthBar", "PowerBar"):  # the bars' backgrounds (their fill and overlay are drawn on the PSP)
                b = sk[bar]
                bx = anchor_x(b, b["width"])
                bg = sk[bar + "Background"]
                place(bg, bx + bg.get("xOfs", 0), b.get("yOfs", 0) + bg.get("yOfs", 0), bg["width"], bg["height"])
            out.append((f"cb_veh_{key}{pitch}", KIND_TEXTURE, tc.wtex_blob(tc.encode(canvas, "t8", linear=True))))
        ov = sk["HealthBarOverlay"]
        img = _tex_rect(root, ov["texture"], ov["texCoord"], round(ov["width"] * s), round(ov["height"] * s), False, None)
        if img is not None:
            out.append((f"cb_veh_ovl_{key}", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "4444", linear=True))))
    # VehicleSeatIndicator backgrounds (VehicleUIIndicator.dbc: ID, BackgroundTexture).
    try:
        from build_spells_pak import dbc_path, dbc_str, load_dbc  # noqa: E402
        rows, strs = load_dbc(dbc_path(root, "VehicleUIIndicator.dbc"))
    except (OSError, ValueError, FileNotFoundError) as e:
        print(f"  class bars: VehicleUIIndicator.dbc not read ({e}) - no seat indicators")
        rows, strs = [], b""
    for r in rows:
        rel = dbc_str(strs, r[1]).replace("\\", "/").lower()
        rel = rel[:-4] if rel.endswith(".blp") else rel
        path = os.path.join(root, rel + ".png")
        if os.path.exists(path):
            img = Image.open(path).convert("RGBA").resize((64, 64), Image.LANCZOS)
            out.append((f"cb_vind_{r[0]}", KIND_TEXTURE, tc.wtex_blob(tc.encode(img, "t8", linear=True))))
    return out


def _paste_clipped(canvas: Image.Image, img: Image.Image, left: int, top: int) -> None:
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(canvas.width, left + img.width), min(canvas.height, top + img.height)
    if x1 <= x0 or y1 <= y0:
        return
    canvas.alpha_composite(img.crop((x0 - left, y0 - top, x1 - left, y1 - top)), (x0, y0))


def main() -> int:
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracted", required=True, help="asset_extract output root (with PNG sidecars)")
    ap.add_argument("--out", default=os.path.join(repo, "psp", "bin", "data", "ui.pak"))
    ap.add_argument("--preview", help="optional directory for PNG previews of 9-slices")
    ap.add_argument("--dump", action="store_true",
                    help="list the entries of an existing --out pak (name, kind, size, WTEX size/format) and exit")
    # The glue screens' on-demand art (loading screens, realm list...) - tools/psp/build_glue_pak.py.
    ap.add_argument("--glue-out", default=os.path.join(repo, "psp", "bin", "data", "glue.pak"))
    ap.add_argument("--no-glue", action="store_true", help="do not (re)bake glue.pak")
    args = ap.parse_args()
    if args.dump:  # self-check of a baked pak (e.g. the unit frame art)
        return dump_pak(args.out)
    import build_glue_pak  # noqa: E402 (same folder) - the tree's text slot, not a fixed ruRU column
    build_glue_pak.use_locale_of(args.extracted)

    entries = []
    total = 0
    for name, rel, kind, opts in TEXTURES:
        if opts.get("optional") and not os.path.exists(os.path.join(args.extracted, rel)):
            print(f"  skip {name:14s} (optional, missing {rel})")  # optional art
            continue
        blob = build_texture(args.extracted, kind, rel, opts)
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:14s} {len(blob):7d} B  <- {rel}")
        if args.preview and kind == "edge":
            os.makedirs(args.preview, exist_ok=True)
            edge_to_nine(load_rgba(args.extracted, rel), opts["edge"] * 4).save(
                os.path.join(args.preview, f"{name}.png"))
    for name, blob in npc_frame(args.extracted):  # NPC windows
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- interface/questframe/ui-questgreeting-* (composed)")
    for name, blob in (merchant_frames(args.extracted) + questlog_frames(args.extracted)  # merchant, quest log
                       + bag_frames(args.extracted) + paperdoll_frame(args.extracted)  # bags, paperdoll
                       + spellbook_frame(args.extracted)):  # spellbook
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- merchant / quest log frame (composed)")
    for name, blob in reputation_frames(args.extracted) + skill_frames(args.extracted):  # character tabs
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- reputation / skill frame (composed)")
    for name, blob in trainer_frame(args.extracted) + talent_frame(args.extracted):  # trainer, talents
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- trainer / talent frame (composed)")
    for name, blob in tradeskill_frames(args.extracted):  # TradeSkillFrame
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- trade skill frame (composed)")
    for name, blob in mainmenubar_art(args.extracted):  # MainMenuBar (always on screen)
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- interface/mainmenubar/* (composed atlas)")
    tb = talent_backgrounds(args.extracted)  # talent tree backgrounds
    for name, blob in tb:
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
    print(f"  talent backgrounds: {len(tb)}" + ("" if tb else " (optional; extract Interface/TalentFrame)"))
    wm = worldmap_frame(args.extracted)  # WorldMapFrame (frame, font, continents, highlights)
    for name, kind, blob in wm:
        entries.append((name, kind, blob))
        total += len(blob)
        if not name.startswith("wmhl_"):
            print(f"  {'font' if kind == KIND_FONT else 'tex ' if kind == KIND_TEXTURE else 'raw '} {name:14s} "
                  f"{len(blob):7d} B  <- WorldMapFrame (composed)")
    hls = [len(b) for n, _, b in wm if n.startswith("wmhl_")]
    print(f"  world map highlights: {len(hls)}, {sum(hls)} B" + ("" if hls else " (optional; Interface/WorldMap)"))
    for name, kind, blob in minimap_cluster(args.extracted):  # MinimapCluster atlas, compass, pvp
        entries.append((name, kind, blob))
        total += len(blob)
        print(f"  {'tex ' if kind == KIND_TEXTURE else 'raw '} {name:14s} {len(blob):7d} B  <- MinimapCluster (composed)")
    for name, blob in taxi_frame(args.extracted):  # TaxiFrame (the pieces around the map)
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- interface/taxiframe/ui-taxiframe-* (composed)")
    for name, kind, blob in bank_mail_frames(args.extracted):  # BankFrame, MailFrame, their data
        entries.append((name, kind, blob))
        total += len(blob)
        print(f"  {'tex ' if kind == KIND_TEXTURE else 'raw '} {name:24s} {len(blob):7d} B  <- bank / mail (composed)")
    for name, kind, blob in raid_bg_frames(args.extracted):  # raid / battleground frames and data
        entries.append((name, kind, blob))
        total += len(blob)
        print(f"  {'tex ' if kind == KIND_TEXTURE else 'raw '} {name:24s} {len(blob):7d} B  <- raid / battleground")
    for name, blob in inspect_frames(args.extracted):  # InspectFrame
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- InspectFrame (composed)")
    for name, blob in dressup_frames(args.extracted):  # DressUpFrame, one per race
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:24s} {len(blob):7d} B  <- DressUpFrame + DressUpBackground (composed)")
    for name, kind, blob in gameobject_use_data(args.extracted):  # locks, sounds, ItemTextFrame
        entries.append((name, kind, blob))
        total += len(blob)
        print(f"  {'tex ' if kind == KIND_TEXTURE else 'raw '} {name:24s} {len(blob):7d} B  <- game objects (DBC / composed)")
    for name, kind, blob in auction_frames(args.extracted):  # AuctionFrame, its parts and data
        entries.append((name, kind, blob))
        total += len(blob)
        print(f"  {'tex ' if kind == KIND_TEXTURE else 'raw '} {name:24s} {len(blob):7d} B  <- AuctionFrame (composed)")
    for name, kind, blob in social_frames(args.extracted):  # FriendsFrame tabs, the guild's frames
        entries.append((name, kind, blob))
        total += len(blob)
        print(f"  tex  {name:24s} {len(blob):7d} B  <- FriendsFrame / guild frames (composed)")
    for name, blob in trade_frames(args.extracted):  # TradeFrame
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- TradeFrame (composed)")
    for name, blob in pet_frames(args.extracted):  # PetActionBarFrame, PetStableFrame, PetPaperDollFrame
        entries.append((name, KIND_TEXTURE, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- pet bar / stable / pet paper doll (composed)")
    for name, kind, blob in class_bar_art(args.extracted):  # VehicleMenuBar skins, seat indicators
        entries.append((name, kind, blob))
        total += len(blob)
        print(f"  tex  {name:16s} {len(blob):6d} B  <- VehicleMenuBar / VehicleSeatIndicator (composed)")
    for name, kind, blob in glyphs_guildbank_art(args.extracted):  # glyphs, vault, channels
        entries.append((name, kind, blob))
        total += len(blob)
        print(f"  tex  {name:24s} {len(blob):7d} B  <- GlyphFrame / GuildBankFrame / ChannelFrame (composed)")
    for name, rel, px in FONTS:
        blob = build_font(args.extracted, rel, px)
        entries.append((name, KIND_FONT, blob))
        total += len(blob)
        print(f"  font {name:14s} {len(blob):7d} B  <- {rel} {px}px")
    write_pak(args.out, entries)
    print(f"wrote {args.out}: {len(entries)} entries, {total} B payload, {os.path.getsize(args.out)} B file")
    if not args.no_glue:  # glue.pak (loading screens, realm list, character select art)
        import build_glue_pak  # noqa: E402 (same folder, on sys.path above)
        return build_glue_pak.build(args.extracted, args.glue_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
