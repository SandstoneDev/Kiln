"""PSP GE texture encoding shared by the WoWee PSP asset tools.

Formats follow the GE Users Manual / libgu-Reference:
  - GU_PSM_5551=1, 4444=2, 8888=3, T4=4, T8=5; 16-bit texels are
    little-endian with R in the LOW bits; CLUT entries are 0xAABBGGRR.
  - T4: the left pixel lives in the LOW nibble.
  - Swizzle ("fast" mode): blocks of 16 bytes x 8 rows; width in bytes must
    be a multiple of 16 and height a multiple of 8, and the GE buffer width
    must equal the texture width.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np
from PIL import Image

PSM_5551 = 1
PSM_4444 = 2
PSM_8888 = 3
PSM_T4 = 4
PSM_T8 = 5

BPP = {PSM_5551: 16, PSM_4444: 16, PSM_8888: 32, PSM_T4: 4, PSM_T8: 8}

FLAG_ALPHA = 1
FLAG_NINE = 2
FLAG_LINEAR = 4


def pow2_at_least(v: int, minimum: int) -> int:
    p = 1
    while p < max(v, minimum):
        p <<= 1
    return p


def swizzle(data: bytes, width_bytes: int, height: int) -> bytes:
    a = np.frombuffer(data, dtype=np.uint8).reshape(height // 8, 8, width_bytes // 16, 16)
    return a.transpose(0, 2, 1, 3).tobytes()


def alpha_class(rgba: np.ndarray) -> str:
    a = rgba[..., 3]
    if a.min() == 255:
        return "opaque"
    if np.all((a == 0) | (a == 255)):
        return "binary"
    return "graded"


@dataclass
class Encoded:
    psm: int
    w: int
    h: int
    uw: int
    uh: int
    pixels: bytes
    clut: bytes  # empty when not paletted
    flags: int
    slice: tuple[int, int, int, int] = (0, 0, 0, 0)


def _min_width(psm: int) -> int:
    # 16-byte rows for swizzling.
    return {PSM_T4: 32, PSM_T8: 16, PSM_4444: 8, PSM_5551: 8, PSM_8888: 4}[psm]


def _pad(img: Image.Image, psm: int) -> tuple[Image.Image, int, int]:
    w = pow2_at_least(img.width, _min_width(psm))
    h = pow2_at_least(img.height, 8)
    if (w, h) == img.size:
        return img, w, h
    canvas = Image.new(img.mode, (w, h), 0 if img.mode != "RGBA" else (0, 0, 0, 0))
    canvas.paste(img, (0, 0))
    return canvas, w, h


def encode(img: Image.Image, fmt: str = "auto", linear: bool = False,
           slice_lrtb: tuple[int, int, int, int] | None = None) -> Encoded:
    """Encodes an RGBA image. fmt: auto | 4444 | 5551 | 8888 | t8 | t4alpha."""
    img = img.convert("RGBA")
    rgba = np.asarray(img)
    cls = alpha_class(rgba)
    if fmt == "auto":
        big = img.width * img.height > 128 * 128
        fmt = "t8" if (big or cls != "graded") else "4444"

    flags = (FLAG_ALPHA if cls != "opaque" else 0) | (FLAG_LINEAR if linear else 0)
    if slice_lrtb:
        flags |= FLAG_NINE
    uw, uh = img.size

    if fmt == "t4alpha":
        # Alpha-only ramp (fonts): white, 16 alpha levels, tinted by vertex colour.
        psm = PSM_T4
        padded, w, h = _pad(img, psm)
        a = (np.asarray(padded)[..., 3].astype(np.uint16) * 15 + 127) // 255
        a = a.astype(np.uint8)
        packed = (a[:, 0::2] | (a[:, 1::2] << 4)).astype(np.uint8).tobytes()
        clut = b"".join(struct.pack("<I", (i * 17) << 24 | 0x00FFFFFF) for i in range(16))
        pixels = swizzle(packed, w // 2, h)
        return Encoded(psm, w, h, uw, uh, pixels, clut, flags | FLAG_ALPHA, slice_lrtb or (0, 0, 0, 0))

    if fmt == "t8":
        psm = PSM_T8
        padded, w, h = _pad(img, psm)
        q = padded.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
        pal = q.getpalette(rawmode="RGBA") or []
        pal = (pal + [0, 0, 0, 0] * 256)[: 256 * 4]
        idx = np.asarray(q, dtype=np.uint8).tobytes()
        clut = bytes(pal)  # RGBA bytes == 0xAABBGGRR little-endian
        pixels = swizzle(idx, w, h)
        return Encoded(psm, w, h, uw, uh, pixels, clut, flags, slice_lrtb or (0, 0, 0, 0))

    if fmt in ("4444", "5551", "8888"):
        psm = {"4444": PSM_4444, "5551": PSM_5551, "8888": PSM_8888}[fmt]
        padded, w, h = _pad(img, psm)
        p = np.asarray(padded).astype(np.uint32)
        r, g, b, a = p[..., 0], p[..., 1], p[..., 2], p[..., 3]
        if psm == PSM_4444:
            v = (r >> 4) | ((g >> 4) << 4) | ((b >> 4) << 8) | ((a >> 4) << 12)
            raw = v.astype("<u2").tobytes()
            wb = w * 2
        elif psm == PSM_5551:
            v = (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10) | ((a >> 7) << 15)
            raw = v.astype("<u2").tobytes()
            wb = w * 2
        else:
            raw = np.asarray(padded, dtype=np.uint8).tobytes()
            wb = w * 4
        return Encoded(psm, w, h, uw, uh, swizzle(raw, wb, h), b"", flags, slice_lrtb or (0, 0, 0, 0))

    raise ValueError(f"unknown format {fmt}")


def _align(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def wtex_blob(e: Encoded) -> bytes:
    """Serialises to the WTEX layout read by src/platform/psp/psp_texture.cpp."""
    clut_off = 32 if e.clut else 0
    clut_count = len(e.clut) // 4
    pix_off = _align(32 + len(e.clut), 64)
    header = b"WTEX" + struct.pack(
        "<BBBBHHHHHH4BII",
        e.psm, 1, e.flags, 0,
        e.w, e.h, e.uw, e.uh, clut_count, 0,
        *e.slice, clut_off, pix_off,
    )
    assert len(header) == 32
    body = header + e.clut
    body += b"\0" * (pix_off - len(body))
    return body + e.pixels
