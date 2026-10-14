#!/usr/bin/env python3
"""data/strings.pak - the client's own interface strings of ONE locale, for the PSP's lookup
(include/platform/psp/psp_strings.hpp, docs/PSP_PORT_ARCHITECTURE.md). The port's EBOOT carries no
client text: every client string comes from the player's client through this pak.

  python tools/psp/build_strings_pak.py --extracted <tree> --out <data dir>/strings.pak [--locale ruRU] [--verify]
  python tools/psp/build_strings_pak.py --dump <strings.pak> [KEY ...]     (prints keys / texts)

Kiln runs it once per locale: the primary's tree -> data/strings.pak, every other -> data/locale/<xxYY>/strings.pak.

Sources (in the extracted tree, the locale's own files):
  interface/framexml/globalstrings.lua   KEY = "..." -> KEY             (FrameXML's GlobalStrings, ~8100)
  interface/gluexml/gluestrings.lua      KEY = "..." -> GLUE_KEY        (the glue screens' own, ~870; 249 keys
                                                                         exist in both tables, 70 with other text)
  interface/framexml/localization*.lua   KEY = "..." -> KEY             (when a client has top-level strings there)
  dbfilesclient/chrclasses.dbc           DBC_CLASS_<id>, DBC_CLASS_<id>_FEMALE (Name_lang / Name_female_lang)
  dbfilesclient/chrraces.dbc             DBC_RACE_<id>, DBC_RACE_<id>_FEMALE
  dbfilesclient/map.dbc                  DBC_MAP_<id> (MapName_lang)
The DBC text slot is the locale's (the slot with the most text: enUS 0, ruRU 8, ... - a client has one).
Text rules: the Lua escapes (\\n \\" \\\\ \\t \\ddd) are decoded, "|n" becomes "\\n"; a "<html>" value (the glue's
LOGIN_* pages) is reduced to its text: the tags go, sentences with a web address go (the PSP cannot open them).
Everything else (|c..|r colours, %s / %1$s forms, |3-N() / |4..; grammar) is kept as the client has it.

Pak (WPAK, tools/psp/build_ui_pak.py write_pak), one raw entry "strings":
  header  24 B  char magic[4] "WSTR"; u16 version 1; u16 flags 0; char locale[4]; u32 count;
                u32 poolOffset; u32 poolSize                      (offsets from the entry's start)
  index   count x 16 B  { u64 hash; u32 textOffset; u32 textLength } sorted by hash - hash = FNV-1a 64 of the
                key's ASCII bytes (no two keys of a pak share one: the baker checks); textOffset into the pool
  pool    the texts, UTF-8, each NUL-terminated, in key order (one area's keys together; equal texts once)
The PSP reads the header and the index at boot, then only the texts of the keys the EBOOT uses (a few KB to
~100 KB resident; the rest of the pak is never held in RAM).
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KIND_RAW = 3
FNV64_OFFSET = 0xCBF29CE484222325
FNV64_PRIME = 0x100000001B3
# The text slot of a 3.3.5a localized DBC string per locale (enUS koKR frFR deDE zhCN zhTW esES esMX ruRU).
LOCALE_SLOTS = {"enUS": 0, "enGB": 0, "koKR": 1, "frFR": 2, "deDE": 3, "zhCN": 4, "enCN": 4, "zhTW": 5, "enTW": 5,
                "esES": 6, "esMX": 7, "ruRU": 8}


def fnv1a64(key: str) -> int:
    h = FNV64_OFFSET
    for b in key.encode("ascii"):
        h = ((h ^ b) * FNV64_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


_LUA_LINE = re.compile(r'^([A-Z][A-Z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"\s*;?\s*(--.*)?$')


def lua_unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n.isdigit():
                j = i + 1
                while j < len(s) and j < i + 4 and s[j].isdigit():
                    j += 1
                out.append(chr(int(s[i + 1:j])))
                i = j
                continue
            out.append({"n": "\n", "t": "\t", "r": "", "\\": "\\", '"': '"', "'": "'"}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out).replace("|n", "\n")


def lua_strings(path: str) -> dict[str, str]:
    """Top-level KEY = "..." assignments of a client Lua file."""
    out: dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _LUA_LINE.match(line.rstrip("\r\n").lstrip("﻿"))
            if m:
                out[m.group(1)] = lua_unescape(m.group(2))
    return out


_TAG = re.compile(r"<[^>]*>")
_WEB = re.compile(r"(https?://|www\.|\.com\b|\.ru\b|\.net/)", re.I)


def html_text(v: str) -> str:
    """A glue <html> page as plain text: tags out, the sentences carrying a web address out."""
    text = _TAG.sub(" ", v.replace("<br/>", "\n").replace("<BR/>", "\n"))
    text = text.replace("&amp;", "&").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"[ \t]+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    keep = [p for p in parts if p and not _WEB.search(p)]
    return " ".join(keep).strip() if keep else text


def read_dbc(path: str):
    with open(path, "rb") as f:
        d = f.read()
    magic, n, fields, rec_size, str_size = struct.unpack_from("<4s4I", d)
    if magic != b"WDBC":
        raise ValueError(f"{path}: not a WDBC")
    rows = [struct.unpack_from(f"<{fields}I", d, 20 + i * rec_size) for i in range(n)]
    strings = d[20 + n * rec_size:20 + n * rec_size + str_size]

    def string(off: int) -> str:
        if off <= 0 or off >= len(strings):
            return ""
        return strings[off:strings.index(b"\0", off)].decode("utf-8", "replace")
    return rows, string


def best_slot(rows, string, first: int) -> int:
    """The locale slot (0..15) of the column starting at `first` that holds the most text."""
    counts = [sum(1 for r in rows if first + k < len(r) and string(r[first + k])) for k in range(16)]
    return max(range(16), key=lambda k: counts[k])


def dbc_strings(root: str, locale: str | None) -> dict[str, str]:
    dbc = os.path.join(root, "dbfilesclient")
    out: dict[str, str] = {}
    # (file, key prefix, name column, female name column or None)
    for fname, prefix, col, fcol in (("chrclasses.dbc", "DBC_CLASS_", 4, 21), ("chrraces.dbc", "DBC_RACE_", 14, 31),
                                     ("map.dbc", "DBC_MAP_", 5, None)):
        p = os.path.join(dbc, fname)
        if not os.path.exists(p):
            print(f"  {fname}: missing - no {prefix}* keys")
            continue
        rows, string = read_dbc(p)
        slot = LOCALE_SLOTS.get(locale or "", -1)
        if slot < 0 or not any(string(r[col + slot]) for r in rows):
            slot = best_slot(rows, string, col)
        for r in rows:
            name = string(r[col + slot])
            if not name:
                continue
            out[f"{prefix}{r[0]}"] = name
            if fcol is not None:
                out[f"{prefix}{r[0]}_FEMALE"] = string(r[fcol + slot]) or name
        print(f"  {fname}: slot {slot}, {sum(1 for k in out if k.startswith(prefix))} keys")
    return out


def collect(root: str, locale: str | None) -> dict[str, str]:
    ui = os.path.join(root, "interface")
    glob = lua_strings(os.path.join(ui, "framexml", "globalstrings.lua"))
    glue = lua_strings(os.path.join(ui, "gluexml", "gluestrings.lua"))
    if not glob and not glue:
        raise SystemExit(f"{root}: no interface/framexml/globalstrings.lua or gluexml/gluestrings.lua")
    extra: dict[str, str] = {}
    fx = os.path.join(ui, "framexml")
    if os.path.isdir(fx):
        for n in sorted(os.listdir(fx)):
            if n.lower().startswith("localization") and n.lower().endswith(".lua"):
                extra.update(lua_strings(os.path.join(fx, n)))
    out: dict[str, str] = {}
    for k, v in extra.items():
        out[k] = v
    out.update(glob)
    for k, v in glue.items():
        out["GLUE_" + k] = v
    html = 0
    for k, v in out.items():
        if v.lstrip().lower().startswith("<html"):
            out[k] = html_text(v)
            html += 1
    out.update(dbc_strings(root, locale))
    print(f"  globalstrings {len(glob)}, gluestrings {len(glue)}, localization {len(extra)}, html pages {html}")
    return out


def build(strings: dict[str, str], locale: str) -> bytes:
    items = sorted(((fnv1a64(k), k, v) for k, v in strings.items()), key=lambda t: t[0])
    for a, b in zip(items, items[1:]):
        if a[0] == b[0]:
            raise SystemExit(f"FNV-1a 64 collision: {a[1]} / {b[1]} - change the hash")
    # The pool in KEY order (the index is in hash order): one area's keys share a prefix (GLUE_CHAR_*, LFG_*, ERR_*)
    # and sit together, so the PSP reads them in a few runs instead of one seek a text.
    pool = bytearray()
    where: dict[bytes, int] = {}
    at: dict[str, tuple[int, int]] = {}
    for k in sorted(strings):
        b = strings[k].encode("utf-8").replace(b"\0", b"")
        off = where.get(b)
        if off is None:
            off = len(pool)
            where[b] = off
            pool += b + b"\0"
        at[k] = (off, len(b))
    index = bytearray()
    for h, k, v in items:
        index += struct.pack("<QII", h, *at[k])
    loc = (locale or "????").encode("ascii")[:4].ljust(4, b"?")
    pool_off = 24 + len(index)
    head = b"WSTR" + struct.pack("<HH", 1, 0) + loc + struct.pack("<III", len(items), pool_off, len(pool))
    assert len(head) == 24
    return head + bytes(index) + bytes(pool)


def parse(blob: bytes):
    """-> (locale, {hash: text}) - the reader the PSP mirrors (psp_strings.cpp)."""
    if blob[:4] != b"WSTR":
        raise ValueError("not a WSTR blob")
    ver, flags = struct.unpack_from("<HH", blob, 4)
    loc = blob[8:12].decode("ascii", "replace")
    count, pool_off, pool_size = struct.unpack_from("<III", blob, 12)
    if ver != 1 or 24 + 16 * count > pool_off or pool_off + pool_size > len(blob):
        raise ValueError("bad WSTR header")
    out = {}
    last = -1
    for i in range(count):
        h, off, ln = struct.unpack_from("<QII", blob, 24 + 16 * i)
        if h <= last:
            raise ValueError("index not sorted / duplicate hash")
        last = h
        if off + ln >= pool_size or blob[pool_off + off + ln] != 0:
            raise ValueError(f"text {i} outside the pool or not NUL-terminated")
        out[h] = blob[pool_off + off:pool_off + off + ln].decode("utf-8")
    return loc, out


def read_pak_entry(path: str, name: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    magic, version, count, toc = data[:4], *struct.unpack_from("<III", data, 4)
    if magic != b"WPAK":
        raise ValueError(f"{path}: not a WPAK")
    for i in range(count):
        n, off, size, kind, _ = struct.unpack_from("<48sIIII", data, toc + 64 * i)
        if n.split(b"\0", 1)[0].decode() == name:
            return data[off:off + size]
    raise ValueError(f"{path}: no entry {name}")


def detect_locale(root: str) -> str | None:
    """The tree's locale when --locale is not given: the DBC text slot (enUS / ruRU / ...)."""
    p = os.path.join(root, "dbfilesclient", "chrclasses.dbc")
    if not os.path.exists(p):
        return None
    rows, string = read_dbc(p)
    slot = best_slot(rows, string, 4)
    return {0: "enUS", 1: "koKR", 2: "frFR", 3: "deDE", 4: "zhCN", 5: "zhTW", 6: "esES", 7: "esMX", 8: "ruRU"}.get(slot)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--extracted", help="the client tree extracted with this locale's archives")
    ap.add_argument("--out", help="strings.pak to write")
    ap.add_argument("--locale", help="xxYY (default: from the DBC text slot)")
    ap.add_argument("--verify", action="store_true", help="read the written pak back")
    ap.add_argument("--dump", help="print a strings.pak (all keys of the tree given by --extracted, or KEY args)")
    ap.add_argument("keys", nargs="*")
    a = ap.parse_args(argv)
    if a.dump:
        loc, table = parse(read_pak_entry(a.dump, "strings"))
        print(f"{a.dump}: locale {loc}, {len(table)} texts")
        for k in a.keys:
            print(f"{k} = {table.get(fnv1a64(k), '<missing>')!r}")
        return 0
    if not a.extracted or not a.out:
        ap.error("--extracted and --out are needed")
    locale = a.locale or detect_locale(a.extracted) or "????"
    print(f"strings.pak: {a.extracted} ({locale})")
    strings = collect(a.extracted, locale)
    blob = build(strings, locale)
    from build_ui_pak import write_pak  # noqa: E402 (same folder: the WPAK writer every pak goes through)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    write_pak(a.out, [("strings", KIND_RAW, blob)])
    print(f"  {len(strings)} keys, entry {len(blob)} B (index {16 * len(strings)} B), file {os.path.getsize(a.out)} B")
    if a.verify:
        loc, table = parse(read_pak_entry(a.out, "strings"))
        bad = [k for k, v in strings.items() if table.get(fnv1a64(k)) != v.replace("\0", "")]
        if loc != locale[:4] or bad:
            print(f"  VERIFY FAILED: locale {loc}, {len(bad)} texts differ (first {bad[:3]})")
            return 1
        print(f"  verify ok: {len(table)} texts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
