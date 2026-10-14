"""Menu art for the player's own copy: ICON0.PNG (144 x 80) and PIC1.PNG (480 x 272) made from the
user's extracted client art, and optionally the user's EBOOT.PBP repacked with them.

The published EBOOT carries neutral art. The art made here comes from the user's own client files and
stays on the user's machine, like everything else Kiln writes.

Sources (read from the client's own tables, nothing is named here):
  * background: the loading screen of map id 0 (map table column 57 -> loading-screen table: id, name,
    file name, has-wide-screen flag); its wide variant when the flag is set;
  * logo: the last (in name order) "*logo*.blp" of interface/glues/common/.

PBP layout (public homebrew knowledge): "\\0PBP", a version word, then eight offsets of PARAM.SFO,
ICON0.PNG, ICON1.PMF, PIC0.PNG, PIC1.PNG, SND0.AT3, DATA.PSP, DATA.PSAR; each section runs to the next
offset, the last one to the end of the file.
"""
from __future__ import annotations

import glob
import os
import struct

from . import blp, mapdb

ICON0_SIZE = (144, 80)
PIC1_SIZE = (480, 272)
PBP_SECTIONS = ("PARAM.SFO", "ICON0.PNG", "ICON1.PMF", "PIC0.PNG", "PIC1.PNG", "SND0.AT3", "DATA.PSP", "DATA.PSAR")


def _image(path):
    from PIL import Image
    with open(path, "rb") as f:
        return Image.fromarray(blp.decode(f.read()), "RGBA")


def _cover(img, size):
    from PIL import Image
    w, h = img.size
    s = max(size[0] / w, size[1] / h)
    r = img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    x, y = (r.size[0] - size[0]) // 2, (r.size[1] - size[1]) // 2
    return r.crop((x, y, x + size[0], y + size[1]))


def _contain(img, box):
    from PIL import Image
    w, h = img.size
    s = min(box[0] / w, box[1] / h)
    return img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def find_sources(extracted):
    dbc = os.path.join(extracted, "dbfilesclient")
    bg = None
    maps = mapdb.load_maps(extracted)
    screens = {}
    lpath = os.path.join(dbc, "loadingscreens.dbc")
    if os.path.exists(lpath):
        recs, _f, string = mapdb.read_dbc(lpath)
        for r in recs:
            sid, fname = struct.unpack_from("<I", r, 0)[0], string(struct.unpack_from("<I", r, 8)[0])
            wide = struct.unpack_from("<I", r, 12)[0] if len(r) >= 16 else 0
            screens[sid] = (fname, wide)
    m0 = maps.get(0)
    if m0 and m0.loading in screens:
        fname, wide = screens[m0.loading]
        rel = fname.replace("\\", "/").lower()
        cands = ([rel[:-4] + "wide.blp"] if wide and rel.endswith(".blp") else []) + [rel]
        for c in cands:
            p = os.path.join(extracted, c)
            if os.path.exists(p):
                bg = p
                break
    logos = sorted(glob.glob(os.path.join(extracted, "interface", "glues", "common", "*logo*.blp")))
    return bg, (logos[-1] if logos else None)


def make_art(extracted, out_dir, log=print):
    """-> (icon0_path, pic1_path). Raises RuntimeError when the client art is not there."""
    from PIL import Image, ImageEnhance
    bg_path, logo_path = find_sources(extracted)
    if not bg_path:
        raise RuntimeError("no loading-screen art found (extract dbfilesclient\\ and interface\\glues\\ first)")
    bg = _image(bg_path).convert("RGB")
    os.makedirs(out_dir, exist_ok=True)
    pic1 = _cover(bg, PIC1_SIZE)
    icon = ImageEnhance.Brightness(_cover(bg, ICON0_SIZE)).enhance(0.45).convert("RGBA")
    if logo_path:
        logo = _contain(_image(logo_path), (ICON0_SIZE[0] - 8, ICON0_SIZE[1] - 6))
        icon.alpha_composite(logo, ((ICON0_SIZE[0] - logo.size[0]) // 2, (ICON0_SIZE[1] - logo.size[1]) // 2))
    p0, p1 = os.path.join(out_dir, "ICON0.PNG"), os.path.join(out_dir, "PIC1.PNG")
    icon.save(p0, format="PNG")
    pic1.save(p1, format="PNG")
    log("  xmb art: background %s, logo %s" % (os.path.relpath(bg_path, extracted),
                                                os.path.relpath(logo_path, extracted) if logo_path else "none"))
    return p0, p1


def read_pbp(data):
    if data[:4] != b"\x00PBP":
        raise ValueError("not a PBP file")
    version = struct.unpack_from("<I", data, 4)[0]
    offs = list(struct.unpack_from("<8I", data, 8)) + [len(data)]
    return version, {n: data[offs[i]:offs[i + 1]] for i, n in enumerate(PBP_SECTIONS)}


def write_pbp(version, sections):
    body, offs, pos = [], [], 40
    for n in PBP_SECTIONS:
        b = sections.get(n, b"")
        offs.append(pos)
        body.append(b)
        pos += len(b)
    return b"\x00PBP" + struct.pack("<I", version) + struct.pack("<8I", *offs) + b"".join(body)


def repack_eboot(eboot_in, eboot_out, icon0, pic1):
    with open(eboot_in, "rb") as f:
        version, sec = read_pbp(f.read())
    with open(icon0, "rb") as f:
        sec["ICON0.PNG"] = f.read()
    with open(pic1, "rb") as f:
        sec["PIC1.PNG"] = f.read()
    with open(eboot_out, "wb") as f:
        f.write(write_pbp(version, sec))


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else None

