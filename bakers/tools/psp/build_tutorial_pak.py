#!/usr/bin/env python3
"""Builds psp/bin/data/tutorial.pak - the original client's
tutorial windows (TutorialFrame) for the PSP - from the player's own extracted
3.3.5a ruRU client (never from this repo).

  1. Extract (read-only, the client may run):
       psp_mpq_extract --data "<client>/Data" --out <extracted> --locale ruRU \\
           --prefix "Interface/FrameXML/" --prefix "Interface/TutorialFrame/"
     (PNG sidecars from upstream asset_extract --emit-png are used when present,
      else the .blp is decoded with Pillow.)
  2. python tools/psp/build_tutorial_pak.py --extracted <extracted> [--out psp/bin/data/tutorial.pak]
  3. python tools/psp/build_tutorial_pak.py --verify     # re-reads the pak, exit code = errors
     python tools/psp/build_tutorial_pak.py --dump       # every id: layout, title, sentences ([K] = keys/mouse)

Why a Python tool (not a C++ converter): the inputs are two Lua files and PNG
art - the same job as build_spells_pak.py (GlobalStrings) and build_ui_pak.py
(WTEX art through psp_texconv); no DBC, M2 or MPQ code is needed.

Layout (include/platform/psp/psp_tutorial_format.hpp): "thdr" header + records
+ elements, "tstr" strings, "tui" labels, frame art "f_*", images "timg_<n>".
TutorialFrame.lua is parsed with a small Lua table reader (TUTORIAL_DATA,
DISPLAY_DATA); TUTORIAL<id> texts are split into sentences and the ones about
keys / the mouse (KEY_WORDS) are marked - the PSP shows a line with its own
button glyphs in their place (src/ui/psp/ui_world_tutorial.cpp).

  --selftest   bakes a synthetic tree (made up here, no client data) and checks it;
               --selftest --out <pak> keeps that pak for the C++ host test
               (tools/psp/host_tests/tutorial_host_test.sh).
Needs Pillow + numpy.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
import tempfile

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psp_texconv as tc  # noqa: E402
from build_ui_pak import write_pak  # noqa: E402

KIND_TEXTURE = 1
KIND_RAW = 3
DEFAULT_SCALE = 0.72  # 364-unit frame -> 262 px on the 480x272 screen

ALIGNS = ["TOPLEFT", "TOP", "TOPRIGHT", "LEFT", "CENTER", "RIGHT", "BOTTOMLEFT", "BOTTOM", "BOTTOMRIGHT"]
KEY_COMMANDS = {"TURNLEFT": 0, "MOVEBACKWARD": 1, "TURNRIGHT": 2, "MOVEFORWARD": 3, "JUMP": 4}
MOUSE_IMAGES = {"LeftClick": 0, "RightClick": 1, "BothClick": 2, "Wheel": 3}
ARROWS = {"ArrowUp": 0, "ArrowDown": 1, "ArrowLeft": 2, "ArrowRight": 3}
CURVED_ARROWS = ("ArrowCurveUpRight", "ArrowCurveUpLeft", "ArrowCurveDownRight", "ArrowCurveDownLeft",
                 "ArrowCurveRightDown", "ArrowCurveRightUp", "ArrowCurveLeftDown", "ArrowCurveLeftUp")
ELEM_IMAGE, ELEM_MOUSE, ELEM_KEY, ELEM_ARROW = 1, 2, 3, 4
MARK_SENTENCE, MARK_KEYS, MARK_PARAGRAPH = b"\x01", b"\x02", b"\x03"

# Sentences about the keyboard or the mouse (ruRU texts, plus English leftovers):
# the PSP replaces them with its own controls line. Edit here when --dump shows
# a sentence that should (or should not) be swapped.
KEY_WORDS = re.compile(
    r"(мыш|клавиш|клавиатур|щелк|щёлк|щелч|щёлч|кликн|колесик|колёсик|пробел|"
    r"\bShift\b|\bCtrl\b|\bAlt\b|\bTab\b|\bEsc\b|\bEnter\b|\bWASD\b|"
    r"[«\"]\s*[A-Z0-9]\s*[»\"]|\bclick|\bmouse|\bkey\b|\bkeys\b)",
    re.IGNORECASE)

# Labels baked into "tui" (GlobalStrings keys; the PSP has Russian fallbacks).
UI_KEYS = ["PREV", "NEXT", "CLOSE", "OKAY", "SHOW_TUTORIALS", "RESET_TUTORIALS", "TUTORIAL_TITLE"]

# UI-TUTORIAL-FRAME (512x512) pieces by the texcoords of TutorialFrame.xml /
# TutorialFrame.lua: name -> (left, right, top, bottom, flip, extra scale).
FRAME_PIECES = {
    "f_top": (0.0019531, 0.7109375, 0.0019531, 0.1562500, None, 1.0),
    "f_bottom": (0.0019531, 0.6953125, 0.1621094, 0.2187500, None, 1.0),
    "f_left": (0.3066406, 0.3261719, 0.6562500, 0.6757813, None, 1.0),
    "f_right": (0.3496094, 0.3613281, 0.6562500, 0.6757813, None, 1.0),
    "f_key": (0.1542969, 0.3007813, 0.8046875, 0.9433594, None, 1.0),
    "f_alert": (0.7167969, 0.9414063, 0.0019531, 0.1386719, None, 1.0),
    "f_alert_hl": (0.7558594, 0.8535156, 0.2246094, 0.3203125, None, 1.0),
    # the layouts draw the arrows at scale 0.5: baked at that size
    "f_arrow_u": (0.1542969, 0.2851563, 0.6269531, 0.7988281, None, 0.5),
    "f_arrow_d": (0.1542969, 0.2851563, 0.6269531, 0.7988281, "v", 0.5),
    "f_arrow_r": (0.3066406, 0.4785156, 0.2246094, 0.3554688, None, 0.5),
    "f_arrow_l": (0.3066406, 0.4785156, 0.2246094, 0.3554688, "h", 0.5),
}
FRAME_FILE = "interface/tutorialframe/ui-tutorial-frame"


# --------------------------------------------------------------------------
# Lua: a table-constructor reader (enough for TUTORIAL_DATA / DISPLAY_DATA)
# --------------------------------------------------------------------------
_TOKEN = re.compile(r'\s*(?:(?P<str>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|'
                    r'(?P<num>-?(?:\d+\.?\d*|\.\d+))|(?P<name>[A-Za-z_]\w*)|(?P<sym>[{}\[\]=,;]))')


def lua_unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n == "n":
                out.append("\n")
            elif n == "t":
                out.append("\t")
            elif n.isdigit():
                m = re.match(r"\d{1,3}", s[i + 1:])
                out.append(chr(int(m.group(0))))
                i += len(m.group(0)) + 1
                continue
            else:
                out.append(n)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def strip_comments(src: str) -> str:
    src = re.sub(r"--\[(=*)\[.*?\]\1\]", "", src, flags=re.S)
    return re.sub(r"--[^\n]*", "", src)


def lua_tokens(src: str, pos: int):
    while True:
        m = _TOKEN.match(src, pos)
        if not m or m.end() == pos:
            return
        pos = m.end()
        kind = m.lastgroup
        yield kind, m.group(kind), pos


class LuaTableReader:
    def __init__(self, src: str, pos: int):
        self.toks = list(lua_tokens(src, pos))
        self.i = 0

    def peek(self, k=0):
        return self.toks[self.i + k] if self.i + k < len(self.toks) else (None, None, 0)

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def value(self):
        kind, text, _ = self.take()
        if kind == "sym" and text == "{":
            return self.table()
        if kind == "str":
            return lua_unescape(text[1:-1])
        if kind == "num":
            return float(text) if "." in text else int(text)
        if kind == "name":
            return {"true": True, "false": False, "nil": None}.get(text, text)
        raise ValueError(f"lua: unexpected {text!r}")

    def table(self):
        out, n = {}, 1
        while True:
            kind, text, _ = self.peek()
            if kind is None:
                raise ValueError("lua: unterminated table")
            if kind == "sym" and text in ",;":
                self.take()
                continue
            if kind == "sym" and text == "}":
                self.take()
                return out
            if kind == "sym" and text == "[":
                self.take()
                key = self.value()
                self.take()  # ]
                self.take()  # =
                out[key] = self.value()
            elif kind == "name" and self.peek(1)[1] == "=":
                self.take()
                self.take()
                out[text] = self.value()
            else:
                out[n] = self.value()
                n += 1


def lua_table(src: str, name: str) -> dict:
    m = re.search(r"(?:^|\n)\s*(?:local\s+)?" + re.escape(name) + r"\s*=\s*\{", src)
    if not m:
        raise ValueError(f"{name} not found")
    reader = LuaTableReader(src, m.end() - 1)
    reader.take()  # {
    return reader.table()


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
def find_file(root: str, rel: str) -> str | None:
    for cand in (rel, rel.lower()):
        p = os.path.join(root, cand)
        if os.path.exists(p):
            return p
    return None


def load_image(root: str, client_path: str) -> Image.Image | None:
    """Interface\\X\\Y (no extension) -> RGBA from the PNG sidecar or the BLP."""
    rel = client_path.replace("\\", "/").lower()
    for ext in (".png", ".blp"):
        p = find_file(root, rel + ext)
        if p:
            try:
                return Image.open(p).convert("RGBA")
            except Exception as e:  # noqa: BLE001 - report and go on
                print(f"  warning: {p}: {e}")
    return None


def global_strings(root: str) -> dict[str, str]:
    p = find_file(root, "interface/framexml/globalstrings.lua")
    if not p:
        raise FileNotFoundError("interface/framexml/globalstrings.lua missing - extract Interface/FrameXML/")
    src = open(p, encoding="utf-8", errors="replace").read()
    out = {}
    for k, v in re.findall(r'^\s*([A-Z_][A-Z0-9_]*)\s*=\s*"((?:\\.|[^"\\])*)"\s*;?\s*$', src, re.M):
        out[k] = lua_unescape(v)
    return out


def client_markup_to_plain(s: str) -> str:
    """Escape sequences of the client UI text: |n newline, colours, textures, ||."""
    s = s.replace("||", "\x00")
    s = re.sub(r"\|c[0-9a-fA-F]{8}", "", s)
    s = s.replace("|r", "").replace("|n", "\n")
    s = re.sub(r"\|T[^|]*\|t", "", s)
    s = re.sub(r"\|H[^|]*\|h", "", s).replace("|h", "")
    s = s.replace("\x00", "|")
    # Marks outside the PSP fonts' charset (build_ui_pak.py CHARSET) -> nearest ASCII.
    for a, b in TYPO.items():
        s = s.replace(a, b)
    return s


TYPO = {"–": "-", "‒": "-", "‐": "-", "‘": "'", "’": "'", "“": '"',
        "”": '"', "„": '"', "→": ">", " ": " ", "•": "-"}

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def mark_text(text: str) -> tuple[bytes, int, list[tuple[bool, str]]]:
    """-> (marked bytes, key sentences, [(is_key, sentence)] for the dump)."""
    out, keys, listing = b"", 0, []
    paragraphs = [p.strip() for p in client_markup_to_plain(text).replace("\r", "").split("\n")]
    first = True
    for para in paragraphs:
        if not para:
            continue
        if not first:
            out += MARK_PARAGRAPH
        first = False
        for sent in _SENTENCE_END.split(para):
            sent = sent.strip()
            if not sent:
                continue
            is_key = bool(KEY_WORDS.search(sent))
            keys += is_key
            out += (MARK_KEYS if is_key else MARK_SENTENCE) + sent.encode("utf-8")
            listing.append((is_key, sent))
    return out, keys, listing


# --------------------------------------------------------------------------
# Bake
# --------------------------------------------------------------------------
def scaled(img: Image.Image, s: float) -> Image.Image:
    w, h = max(1, round(img.width * s)), max(1, round(img.height * s))
    return img.resize((w, h), Image.LANCZOS)


def texture_blob(img: Image.Image, fmt: str = "auto") -> bytes:
    return tc.wtex_blob(tc.encode(img, fmt, linear=True))


def frame_pieces(root: str, s: float, errors: list[str]) -> list[tuple[str, int, bytes]]:
    frame = load_image(root, FRAME_FILE)
    if frame is None:
        errors.append(f"missing {FRAME_FILE}(.png|.blp)")
        return []
    fw, fh = frame.size
    out = []
    for name, (l, r, t, b, flip, extra) in FRAME_PIECES.items():
        box = (round(l * fw), round(t * fh), round(r * fw), round(b * fh))
        piece = frame.crop(box)
        if flip == "v":
            piece = piece.transpose(Image.FLIP_TOP_BOTTOM)
        elif flip == "h":
            piece = piece.transpose(Image.FLIP_LEFT_RIGHT)
        # pieces are measured in 512-texel units of the original art
        piece = scaled(piece, s * extra * 512.0 / fw)
        out.append((name, KIND_TEXTURE, texture_blob(piece, "4444" if piece.width * piece.height <= 128 * 64 else "auto")))
    return out


def s16(v) -> int:
    return max(-32768, min(32767, int(round(float(v or 0)))))


def align_code(a) -> int:
    return ALIGNS.index(a) if a in ALIGNS else ALIGNS.index("TOPLEFT")


def build(root: str, scale: float) -> tuple[list[tuple[str, int, bytes]], dict, list[str], list[dict]]:
    errors: list[str] = []
    gs = global_strings(root)
    tf_path = find_file(root, "interface/framexml/tutorialframe.lua")
    if not tf_path:
        raise FileNotFoundError("interface/framexml/tutorialframe.lua missing - extract Interface/FrameXML/")
    tf_src = strip_comments(open(tf_path, encoding="utf-8", errors="replace").read())
    tdata = lua_table(tf_src, "TUTORIAL_DATA")
    ddata = lua_table(tf_src, "DISPLAY_DATA")

    ids = sorted({int(m.group(1)) for k in gs for m in [re.fullmatch(r"TUTORIAL(\d+)", k)] if m})
    layouts = sorted(set(ddata.keys()) | {"Base"}, key=str)
    strings = bytearray()
    image_index: dict[str, int] = {}
    image_blobs: list[tuple[str, int, bytes]] = []
    recs, elems, report = [], [], []
    stats = dict(ids=0, key_sentences=0, images=0, images_missing=0, curved_arrows=0, callouts=0, keys=0, mice=0)

    def intern(s: bytes) -> int:
        off = len(strings)
        strings.extend(s + b"\0")
        return off

    def image_of(path: str) -> int | None:
        key = path.replace("\\", "/").lower()
        if key in image_index:
            return image_index[key]
        img = load_image(root, path)
        if img is None:
            stats["images_missing"] += 1
            errors.append(f"missing image {path}")
            image_index[key] = None
            return None
        n = len(image_blobs)
        image_blobs.append((f"timg_{n}", KIND_TEXTURE, texture_blob(scaled(img, scale))))
        image_index[key] = n
        return n

    for tid in ids:
        lname = tdata.get(tid)
        layout = ddata.get(lname) if isinstance(lname, str) else None
        if not isinstance(layout, dict):
            lname, layout = "Base", ddata.get("Base", {})
        anchor = layout.get("anchorData", {}) or {}
        tb = layout.get("textBox", {}) or {}
        title = gs.get(f"TUTORIAL_TITLE{tid}", "")
        marked, keys, listing = mark_text(gs.get(f"TUTORIAL{tid}", ""))
        first = len(elems)
        for i in (1, 2, 3):
            d = layout.get(f"imageData{i}")
            if isinstance(d, dict) and d.get("file"):
                # The mouse-cursor pictures (UI-TutorialFrame-*Cursor) show PC input
                # (the PSP shows its own buttons only) - left out.
                if "cursor" in d["file"].lower():
                    stats["cursor_images_skipped"] = stats.get("cursor_images_skipped", 0) + 1
                    continue
                n = image_of(d["file"])
                if n is not None:
                    elems.append((ELEM_IMAGE, n, align_code(d.get("align")), 100, s16(d.get("xOff")), s16(d.get("yOff"))))
        m = layout.get("mouseData")
        if isinstance(m, dict):
            elems.append((ELEM_MOUSE, MOUSE_IMAGES.get(m.get("image"), 1), align_code(m.get("align")),
                          round(float(m.get("scale", 1.0)) * 100), s16(m.get("xOff")), s16(m.get("yOff"))))
            stats["mice"] += 1
        for i in (1, 2, 3, 4):
            k = layout.get(f"keyData{i}")
            if isinstance(k, dict):
                elems.append((ELEM_KEY, KEY_COMMANDS.get(k.get("command"), 0xFF), align_code(k.get("align")), 100,
                              s16(k.get("xOff")), s16(k.get("yOff"))))
                stats["keys"] += 1
        for aname, idx in ARROWS.items():
            a = layout.get(aname)
            if isinstance(a, dict):
                elems.append((ELEM_ARROW, idx, align_code(a.get("align")), round(float(a.get("scale", 1.0)) * 100),
                              s16(a.get("xOff")), s16(a.get("yOff"))))
        stats["curved_arrows"] += sum(1 for c in CURVED_ARROWS if isinstance(layout.get(c), dict))
        stats["callouts"] += 1 if isinstance(layout.get("callOut"), dict) else 0
        recs.append(struct.pack(
            "<HBBhhhhhhIIHHBBH", tid, max(1, min(255, int(layout.get("tileHeight", 7)))),
            align_code(anchor.get("align", "LEFT")), s16(anchor.get("xOff")), s16(anchor.get("yOff")),
            s16(tb.get("topLeft_xOff", 33)), s16(tb.get("topLeft_yOff", -75)),
            s16(tb.get("bottomRight_xOff", -29)), s16(tb.get("bottomRight_yOff", 35)),
            intern(title.encode("utf-8")) if title else 0xFFFFFFFF,
            intern(marked) if marked else 0xFFFFFFFF, first, len(elems) - first,
            layouts.index(lname) if lname in layouts else 0, 1 if keys else 0, 0))
        stats["ids"] += 1
        stats["key_sentences"] += keys
        report.append(dict(id=tid, layout=lname, title=title, sentences=listing, elems=elems[first:]))

    header = b"WTUT" + struct.pack("<7I", 1, len(recs), max(ids) if ids else 0, round(scale * 100), len(elems),
                                   len(image_blobs), 0)
    thdr = header + b"".join(recs) + b"".join(struct.pack("<BBBBhh", *e) for e in elems)
    ui = b"".join(k.encode() + b"\0" + gs[k].encode("utf-8") + b"\0" for k in UI_KEYS if k in gs)
    entries = [("thdr", KIND_RAW, thdr), ("tstr", KIND_RAW, bytes(strings)), ("tui", KIND_RAW, ui)]
    entries += frame_pieces(root, scale, errors)
    entries += image_blobs
    stats["images"] = len(image_blobs)
    if not ids:
        errors.append("no TUTORIAL<id> strings in GlobalStrings.lua")
    return entries, stats, errors, report


# --------------------------------------------------------------------------
# Read back (--verify / --dump)
# --------------------------------------------------------------------------
def read_pak(path: str) -> dict[str, tuple[int, bytes]]:
    data = open(path, "rb").read()
    if data[:4] != b"WPAK":
        raise ValueError(f"{path}: not a WPAK")
    count, toc = struct.unpack_from("<II", data, 8)
    out = {}
    for i in range(count):
        name, off, size, kind, _ = struct.unpack_from("<48sIIII", data, toc + 64 * i)
        out[name.split(b"\0", 1)[0].decode()] = (kind, data[off:off + size])
    return out


def parse(pak: dict) -> tuple[tuple, list, list, bytes, dict]:
    thdr = pak["thdr"][1]
    magic = thdr[:4]
    ver, count, max_id, scale_pct, nelem, nimg, _ = struct.unpack_from("<7I", thdr, 4)
    if magic != b"WTUT" or ver != 1:
        raise ValueError("bad thdr")
    recs = [struct.unpack_from("<HBBhhhhhhIIHHBBH", thdr, 32 + 32 * i) for i in range(count)]
    base = 32 + 32 * count
    elems = [struct.unpack_from("<BBBBhh", thdr, base + 8 * i) for i in range(nelem)]
    tui = pak.get("tui", (0, b""))[1].split(b"\0")
    ui = {tui[i].decode(): tui[i + 1].decode("utf-8") for i in range(0, len(tui) - 1, 2) if tui[i]}
    return (count, max_id, scale_pct, nelem, nimg), recs, elems, pak["tstr"][1], ui


def cstr(blob: bytes, off: int) -> bytes:
    if off == 0xFFFFFFFF:
        return b""
    return blob[off:blob.index(b"\0", off)]


def verify(path: str) -> int:
    errors = 0
    pak = read_pak(path)
    (count, max_id, scale_pct, nelem, nimg), recs, elems, tstr, ui = parse(pak)
    prev = -1
    keyed = 0
    for r in recs:
        tid, tiles, anchor, first, n, flags = r[0], r[1], r[2], r[11], r[12], r[14]
        if tid <= prev:
            print(f"  error: id {tid} out of order")
            errors += 1
        prev = tid
        if anchor >= len(ALIGNS) or first + n > nelem or tiles == 0:
            print(f"  error: id {tid} layout fields")
            errors += 1
        for off in (r[9], r[10]):
            if off != 0xFFFFFFFF and (off >= len(tstr) or b"\0" not in tstr[off:]):
                print(f"  error: id {tid} string offset {off}")
                errors += 1
        if r[10] != 0xFFFFFFFF:
            text = cstr(tstr, r[10])
            if text[:1] not in (MARK_SENTENCE, MARK_KEYS):
                print(f"  error: id {tid} text not marked")
                errors += 1
            try:
                text.decode("utf-8")
            except UnicodeDecodeError:
                print(f"  error: id {tid} text not UTF-8")
                errors += 1
        keyed += flags & 1
    for e in elems:
        if e[0] == ELEM_IMAGE and f"timg_{e[1]}" not in pak:
            print(f"  error: image {e[1]} missing")
            errors += 1
    for name, (kind, blob) in pak.items():
        if kind == KIND_TEXTURE and blob[:4] != b"WTEX":
            print(f"  error: {name} not a WTEX")
            errors += 1
    frame = [n for n in FRAME_PIECES if n in pak]
    print(f"tutorial_pak ids={count} max_id={max_id} scale={scale_pct / 100:.2f} elems={nelem} images={nimg} "
          f"with_keys={keyed} ui={len(ui)} frame_art={len(frame)}/{len(FRAME_PIECES)} errors={errors}")
    if len(frame) != len(FRAME_PIECES):
        print("  warning: frame art incomplete (the PSP draws a plain panel)")
    return errors


def dump(path: str) -> int:
    pak = read_pak(path)
    (count, max_id, scale_pct, nelem, nimg), recs, elems, tstr, ui = parse(pak)
    names = {1: "img", 2: "mouse", 3: "key", 4: "arrow"}
    print(f"{path}: {count} tutorials, max id {max_id}, scale {scale_pct}%, {nimg} images")
    for k, v in ui.items():
        print(f"  ui {k} = {v}")
    for r in recs:
        tid, tiles = r[0], r[1]
        title = cstr(tstr, r[9]).decode("utf-8")
        print(f"[{tid}] tiles={tiles} anchor={ALIGNS[r[2]]} ({r[3]},{r[4]}) text=({r[5]},{r[6]})-({r[7]},{r[8]}) "
              f"layout#{r[13]} \"{title}\"")
        for e in elems[r[11]:r[11] + r[12]]:
            print(f"    {names.get(e[0], '?')} #{e[1]} {ALIGNS[e[2]]} ({e[4]},{e[5]}) scale={e[3]}%")
        text = cstr(tstr, r[10])
        cur = None
        for chunk in re.split(b"([\x01\x02\x03])", text):
            if chunk in (MARK_SENTENCE, MARK_KEYS, MARK_PARAGRAPH):
                cur = chunk
                if chunk == MARK_PARAGRAPH:
                    print("    --")
                continue
            if chunk and cur:
                print(f"    {'[K]' if cur == MARK_KEYS else '   '} {chunk.decode('utf-8')}")
    return 0


# --------------------------------------------------------------------------
# Self-test on synthetic data (written here; no client files involved)
# --------------------------------------------------------------------------
SYNTH_GLOBALSTRINGS = '''-- synthetic GlobalStrings for the tutorial.pak self-test
PREV = "Назад";
NEXT = "Далее";
CLOSE = "Закрыть";
RESET_TUTORIALS = "Сбросить обучение";
TUTORIAL_TITLE1 = "Тестовый заголовок один";
TUTORIAL1 = "Первое предложение без управления. Щелкните правой кнопкой мыши по тестовому NPC.\\nВторой абзац |cffffd200с цветом|r.";
TUTORIAL_TITLE2 = "Движение (тест)";
TUTORIAL2 = "Используйте клавиши W, A, S и D. Это просто текст! Стрелки тоже работают на клавиатуре.";
TUTORIAL_TITLE42 = "Добро пожаловать (тест)";
TUTORIAL42 = "Синтетический текст приветствия.";
TUTORIAL_TITLE55 = "Новый уровень (тест)";
TUTORIAL55 = "Тест \\"кавычек\\" и |nпереноса строки.";
'''

SYNTH_TUTORIALFRAME = '''-- synthetic TutorialFrame.lua for the tutorial.pak self-test
local TUTORIAL_DATA = {
	[1] = "Giver",
	[2] = "Walk",
	[55] = "Level",
};
local DISPLAY_DATA = {
	["Base"] = {
		tileHeight = 7,
		anchorData = {align = "LEFT", xOff = 15, yOff = 30},
		textBox = {topLeft_xOff = 33, topLeft_yOff = -75, bottomRight_xOff = -29, bottomRight_yOff = 35},
	},
	["Giver"] = {
		tileHeight = 21,
		anchorData = {align = "RIGHT", xOff = -25, yOff = -100},
		textBox = {topLeft_xOff = 33, topLeft_yOff = -180, bottomRight_xOff = -29, bottomRight_yOff = 35},
		imageData1 = {file ="Interface\\\\TutorialFrame\\\\Test-Cursor", align = "TOP", xOff = 0, yOff = -110},
		imageData2 = {file ="Interface\\\\TutorialFrame\\\\Test-Giver", align = "TOP", xOff = -40, yOff = -50},
		mouseData = {image = "RightClick", align = "TOP", xOff = 80, yOff = -60},
	},
	["Walk"] = {
		tileHeight = 18,
		anchorData = {align = "LEFT", xOff = 15, yOff = 30},
		textBox = {topLeft_xOff = 33, topLeft_yOff = -200, bottomRight_xOff = -29, bottomRight_yOff = 35},
		keyData1 = {command = "TURNLEFT", layer = "OVERLAY", align = "TOPLEFT", xOff = 105, yOff = -100},
		keyData4 = {command = "MOVEFORWARD", align = "TOPLEFT", xOff = 160, yOff = -60},
		ArrowUp = {layer = "BORDER", align = "TOPLEFT", xOff = 182, yOff = -40, scale = 0.5},
		ArrowLeft = {align = "TOPLEFT", xOff = 83, yOff = -120, scale = 0.5},
--		ArrowCurveUpLeft = {layer = "OVERLAY", align = "TOPRIGHT", xOff = -215, yOff = -55, scale = 0.5},
	},
	["Level"] = {
		tileHeight = 22,
		anchorData = {align = "LEFT", xOff = 15, yOff = 30},
		callOut	= {parent = "PlayerFrame", align = "TOPLEFT", xOff = 34, yOff = -6, width = 78, height = 78},
		textBox = {topLeft_xOff = 33, topLeft_yOff = -215, bottomRight_xOff = -29, bottomRight_yOff = 35},
		imageData1 = {file ="Interface\\\\TutorialFrame\\\\Test-Cursor", align = "TOP", xOff = 10, yOff = -30},
	},
};
'''


def selftest(out: str | None) -> int:
    with tempfile.TemporaryDirectory() as root:
        fx = os.path.join(root, "interface", "framexml")
        tfdir = os.path.join(root, "interface", "tutorialframe")
        os.makedirs(fx)
        os.makedirs(tfdir)
        open(os.path.join(fx, "globalstrings.lua"), "w", encoding="utf-8").write(SYNTH_GLOBALSTRINGS)
        open(os.path.join(fx, "tutorialframe.lua"), "w", encoding="utf-8").write(SYNTH_TUTORIALFRAME)
        frame = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        d = ImageDraw.Draw(frame)
        for i, (l, r, t, b, _, _) in enumerate(FRAME_PIECES.values()):
            d.rectangle((l * 512, t * 512, r * 512 - 1, b * 512 - 1), fill=(40 * i % 255, 120, 200, 255))
        frame.save(os.path.join(tfdir, "ui-tutorial-frame.png"))
        Image.new("RGBA", (128, 128), (200, 180, 40, 255)).save(os.path.join(tfdir, "test-cursor.png"))
        Image.new("RGBA", (256, 128), (90, 60, 30, 200)).save(os.path.join(tfdir, "test-giver.png"))
        entries, stats, errors, report = build(root, DEFAULT_SCALE)
        path = out or os.path.join(root, "tutorial.pak")
        write_pak(path, entries)
        fails = []
        if errors:
            fails.append(f"build errors {errors}")
        # Test-Cursor (Giver, Level) is a mouse-cursor picture - skipped, Test-Giver the one image.
        if (stats["ids"] != 4 or stats["images"] != 1 or stats["key_sentences"] != 3
                or stats.get("cursor_images_skipped") != 2):
            fails.append(f"stats {stats}")
        by_id = {r["id"]: r for r in report}
        if by_id[1]["layout"] != "Giver" or by_id[42]["layout"] != "Base" or by_id[2]["layout"] != "Walk":
            fails.append("layouts")
        if [k for k, _ in by_id[1]["sentences"]] != [False, True, False]:
            fails.append(f"marks id 1 {by_id[1]['sentences']}")
        if "с цветом" not in by_id[1]["sentences"][2][1] or "|c" in by_id[1]["sentences"][2][1]:
            fails.append("colour codes")
        if '"кавычек"' not in by_id[55]["sentences"][0][1]:
            fails.append("escapes")
        if stats["curved_arrows"] != 0 or stats["callouts"] != 1 or stats["keys"] != 2 or stats["mice"] != 1:
            fails.append(f"elements {stats}")
        if verify(path) != 0:
            fails.append("verify")
        dump(path)
        print("selftest " + ("FAILED: " + "; ".join(fails) if fails else "ok") + f" ({path if out else 'temp'})")
        return 1 if fails else 0


def main() -> int:
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracted", help="extracted client root (interface/framexml, interface/tutorialframe)")
    ap.add_argument("--out", default=None, help="pak path (default psp/bin/data/tutorial.pak)")
    ap.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="art scale, PSP px per original unit")
    ap.add_argument("--verify", action="store_true", help="re-read --out and check it (exit code = errors)")
    ap.add_argument("--dump", action="store_true", help="print every tutorial of --out")
    ap.add_argument("--selftest", action="store_true", help="bake + verify synthetic data (no client files)")
    args = ap.parse_args()
    if args.selftest:
        return selftest(args.out)
    out = args.out or os.path.join(repo, "psp", "bin", "data", "tutorial.pak")
    if args.verify or args.dump:
        rc = verify(out) if args.verify else 0
        if args.dump:
            dump(out)
        return min(rc, 100)
    if not args.extracted:
        ap.error("--extracted is required")
    entries, stats, errors, _ = build(args.extracted, args.scale)
    write_pak(out, entries)
    for e in errors:
        print(f"  warning: {e}")
    print("tutorial_pak " + " ".join(f"{k}={v}" for k, v in stats.items()) + f" warnings={len(errors)}")
    print(f"wrote {out}: {len(entries)} entries, {os.path.getsize(out)} B")
    return min(verify(out), 100)


if __name__ == "__main__":
    sys.exit(main())
