"""BLP (version 2) texture decoder and PNG side-file writer.

Several Python bakers read interface art as PNG files that sit next to the extracted BLP files
(``<extracted>/interface/.../name.png`` beside ``name.blp``). This module writes those PNGs on the
user's machine from the user's own extracted files.

Decoding follows the public description of the BLP2 container (header, mip table, 256-entry BGRA
palette) and of the S3TC/DXT block formats. The rounding rules match the port's own texture loader,
so the PNGs come out pixel-identical to the ones its desktop extractor writes:

* RGB565 endpoints expand as ``v * 255 // 31`` (red, blue) and ``v * 255 // 63`` (green);
* the interpolated colours truncate (``(2 * c0 + c1) // 3``);
* DXT1 reads the endpoint order as the punch-through flag (index 3 transparent when c0 <= c1);
* DXT3 alpha is ``a4 * 255 // 15``, DXT5 alpha uses the 8- or 6-step ramp with truncation;
* palette alpha (1, 4 or 8 bits) follows the index bytes, 4-bit alpha low nibble first.
"""
from __future__ import annotations

import os
import struct

import numpy as np

HEADER = struct.Struct("<4sIBBBBII16I16I")   # magic .. mipSizes; the palette follows (256 x u32)
HEADER_SIZE = HEADER.size + 1024
MAX_DIM = 4096


class BlpError(ValueError):
    pass


def _expand565(c):
    c = c.astype(np.int32)
    r = ((c >> 11) & 0x1F) * 255 // 31
    g = ((c >> 5) & 0x3F) * 255 // 63
    b = (c & 0x1F) * 255 // 31
    return np.stack([r, g, b], axis=-1)            # (n, 3)


def _color_blocks(cb, punch_through):
    """cb: (n, 8) uint8 colour halves -> (n, 16, 3) RGB and (n, 16) bool 'transparent'."""
    c0 = cb[:, 0].astype(np.uint16) | (cb[:, 1].astype(np.uint16) << 8)
    c1 = cb[:, 2].astype(np.uint16) | (cb[:, 3].astype(np.uint16) << 8)
    p0, p1 = _expand565(c0), _expand565(c1)
    four = (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3
    if punch_through:
        three = c0 <= c1
        half = (p0 + p1) // 2
        p2 = np.where(three[:, None], half, four[0])
        p3 = np.where(three[:, None], 0, four[1])
    else:
        three = np.zeros(len(cb), dtype=bool)
        p2, p3 = four
    pal = np.stack([p0, p1, p2, p3], axis=1)        # (n, 4, 3)
    bits = (cb[:, 4].astype(np.uint32) | (cb[:, 5].astype(np.uint32) << 8) |
            (cb[:, 6].astype(np.uint32) << 16) | (cb[:, 7].astype(np.uint32) << 24))
    shifts = (np.arange(16, dtype=np.uint32) * 2)
    idx = ((bits[:, None] >> shifts[None, :]) & 3).astype(np.intp)   # (n, 16)
    rgb = np.take_along_axis(pal, idx[:, :, None].repeat(3, axis=2), axis=1)
    transparent = three[:, None] & (idx == 3)
    return rgb, transparent


def _dxt5_alpha(ab):
    """ab: (n, 8) uint8 alpha halves -> (n, 16) alpha."""
    a0 = ab[:, 0].astype(np.int32)
    a1 = ab[:, 1].astype(np.int32)
    ramp = np.zeros((len(ab), 8), dtype=np.int32)
    ramp[:, 0], ramp[:, 1] = a0, a1
    seven = a0 > a1
    for i in range(6):
        ramp[:, 2 + i] = np.where(seven, ((6 - i) * a0 + (1 + i) * a1) // 7, 0)
    for i in range(4):
        ramp[:, 2 + i] = np.where(seven, ramp[:, 2 + i], ((4 - i) * a0 + (1 + i) * a1) // 5)
    ramp[:, 6] = np.where(seven, ramp[:, 6], 0)
    ramp[:, 7] = np.where(seven, ramp[:, 7], 255)
    bits = np.zeros(len(ab), dtype=np.uint64)
    for i in range(6):
        bits |= ab[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    shifts = (np.arange(16, dtype=np.uint64) * np.uint64(3))
    idx = ((bits[:, None] >> shifts[None, :]) & np.uint64(7)).astype(np.intp)
    return np.take_along_axis(ramp, idx, axis=1)


def _dxt3_alpha(ab):
    lo = ab & 0x0F
    hi = ab >> 4
    a4 = np.stack([lo, hi], axis=2).reshape(len(ab), 16).astype(np.int32)
    return a4 * 255 // 15


def _decode_dxt(src, width, height, kind):
    bw, bh = (width + 3) // 4, (height + 3) // 4
    bsize = 8 if kind == 1 else 16
    need = bw * bh
    have = min(need, len(src) // bsize)
    out = np.zeros((need, 16, 4), dtype=np.uint8)   # blocks past the end of the data stay zero
    if have:
        blocks = np.frombuffer(src, dtype=np.uint8, count=have * bsize).reshape(have, bsize)
        if kind == 1:
            rgb, transparent = _color_blocks(blocks, True)
            alpha = np.where(transparent, 0, 255)
        else:
            rgb, _ = _color_blocks(blocks[:, 8:16], False)
            alpha = _dxt3_alpha(blocks[:, 0:8]) if kind == 3 else _dxt5_alpha(blocks[:, 0:8])
        out[:have, :, 0:3] = rgb
        out[:have, :, 3] = alpha
    img = out.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.ascontiguousarray(img[:height, :width])


def _decode_palette(src, width, height, palette, alpha_depth):
    n = width * height
    buf = np.frombuffer(src, dtype=np.uint8)

    def take(count):
        a = np.zeros(count, dtype=np.uint8)
        k = min(count, len(buf))
        a[:k] = buf[:k]
        return a

    idx = take(n).astype(np.intp)
    pal = np.asarray(palette, dtype=np.uint32)
    col = pal[idx]
    out = np.empty((n, 4), dtype=np.uint8)
    out[:, 0] = (col >> 16) & 0xFF
    out[:, 1] = (col >> 8) & 0xFF
    out[:, 2] = col & 0xFF
    rest = buf[n:] if len(buf) > n else np.zeros(0, dtype=np.uint8)
    i = np.arange(n)

    def alpha_bytes(count):
        a = np.zeros(count, dtype=np.uint8)
        k = min(count, len(rest))
        a[:k] = rest[:k]
        return a

    if alpha_depth == 8:
        out[:, 3] = alpha_bytes(n)
    elif alpha_depth == 4:
        ab = alpha_bytes((n + 1) // 2)[i // 2]
        out[:, 3] = np.where(i % 2 == 0, (ab & 0x0F) * 17, (ab >> 4) * 17)
    elif alpha_depth == 1:
        ab = alpha_bytes((n + 7) // 8)[i // 8]
        out[:, 3] = np.where((ab >> (i % 8)) & 1, 255, 0)
    else:
        out[:, 3] = 255
    return out.reshape(height, width, 4)


def decode(data: bytes) -> np.ndarray:
    """BLP2 bytes -> (height, width, 4) uint8 RGBA of the top mip level. Raises BlpError."""
    if len(data) < HEADER_SIZE:
        raise BlpError("too small for a BLP2 header")
    h = HEADER.unpack_from(data, 0)
    magic, _version, comp, alpha_depth, alpha_enc, _mips, width, height = h[:8]
    offsets, sizes = h[8:24], h[24:40]
    if magic != b"BLP2":
        raise BlpError("not a BLP2 file (%r)" % magic)
    if not (0 < width <= MAX_DIM and 0 < height <= MAX_DIM):
        raise BlpError("dimensions out of range: %dx%d" % (width, height))
    off, size = offsets[0], sizes[0]
    if off + size > len(data):
        raise BlpError("top mip level runs past the end of the file")
    src = data[off:off + size]
    if comp == 1:
        palette = struct.unpack_from("<256I", data, HEADER.size)
        return _decode_palette(src, width, height, palette, alpha_depth)
    if comp == 2:
        if alpha_depth == 0 or alpha_enc == 0:
            kind = 1
        elif alpha_enc == 1:
            kind = 3
        elif alpha_enc == 7:
            kind = 5
        else:
            kind = 1
        return _decode_dxt(src, width, height, kind)
    # 3 = A8R8G8B8 stored as BGRA; anything else is read the same way.
    n = width * height
    if size < n * 4:
        raise BlpError("BGRA level smaller than width x height x 4")
    bgra = np.frombuffer(src, dtype=np.uint8, count=n * 4).reshape(height, width, 4)
    return np.ascontiguousarray(bgra[:, :, [2, 1, 0, 3]])


def write_png(blp_path: str, png_path: str) -> None:
    from PIL import Image  # imported here so the rest of Kiln works without Pillow

    with open(blp_path, "rb") as f:
        rgba = decode(f.read())
    tmp = png_path + ".tmp"
    Image.fromarray(rgba, "RGBA").save(tmp, format="PNG")
    os.replace(tmp, png_path)


def png_sidecars(root: str, subdirs, force=False, jobs=1, log=print):
    """Write <name>.png next to every <name>.blp under root/<subdir> (recursive).

    An empty (0-byte) .blp is skipped with a note, not counted as failed: some private-server patches ship one
    next to the real file (e.g. INV_Shoulder_94.tga.blp beside inv_shoulder_94.blp); nothing uses it.

    Returns (written, kept, failed, empty)."""
    todo, kept, empty = [], 0, 0
    for sub in subdirs:
        base = os.path.join(root, sub)
        for dp, _dns, fns in os.walk(base):
            for fn in fns:
                if not fn.lower().endswith(".blp"):
                    continue
                blp = os.path.join(dp, fn)
                png = blp[:-4] + ".png"
                if os.path.getsize(blp) == 0:
                    empty += 1
                    log("  png skipped: %s (an empty file in the client - not used)" % os.path.relpath(blp, root))
                    continue
                if not force and os.path.exists(png) and os.path.getmtime(png) >= os.path.getmtime(blp):
                    kept += 1
                    continue
                todo.append((blp, png))
    failed = 0
    if jobs > 1 and len(todo) > 64:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            for (blp, _png), err in zip(todo, ex.map(_convert_one, todo, chunksize=32)):
                if err:
                    failed += 1
                    log("  png failed: %s (%s)" % (os.path.relpath(blp, root), err))
    else:
        for item in todo:
            err = _convert_one(item)
            if err:
                failed += 1
                log("  png failed: %s (%s)" % (os.path.relpath(item[0], root), err))
    return len(todo) - failed, kept, failed, empty


def _convert_one(item):
    blp, png = item
    try:
        write_png(blp, png)
        return None
    except (BlpError, OSError, ValueError) as e:
        return str(e)
