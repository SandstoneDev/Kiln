"""text.pak entry "glyph_data".

Written from specs/bakers/text_glyph_data.md (v2) only.
"""

import re

from .wdbc import BakeError, Out, load_required

_EFFECT_APPLY_GLYPH = 74
_SP_EFFECT = (71, 72, 73)
_SP_MISC = (110, 111, 112)

# "ui-glyph-rune-" + one or more ASCII decimal digits and nothing else
_RUNE_NAME = re.compile(r"ui-glyph-rune-([0-9]+)")


def rune_number(texture_filename):
    """Rune number of a SpellIcon TextureFilename (0 when the file name is not a rune name)."""
    text = texture_filename.replace("/", "\\").lower()
    base = text.rsplit("\\", 1)[-1]
    m = _RUNE_NAME.fullmatch(base)
    if m is None:
        return 0
    value = int(m.group(1), 10)
    if value > 0xFFFFFFFF:
        raise BakeError("glyph rune number %s does not fit u32" % m.group(1))
    return value


def build_glyph_data(root):
    """Return the bytes of the "glyph_data" entry. Missing input files raise MissingTableError."""
    slots = load_required(root, "GlyphSlot", 3)
    props = load_required(root, "GlyphProperties", 4)
    icons = load_required(root, "SpellIcon", 2)
    spell = load_required(root, "Spell", max(_SP_MISC) + 1)

    # SpellIcon ID -> TextureFilename. With several rows of one ID (not expected in real data)
    # the first row in input order is used.
    icon_names = {}
    for icon_id, fname in icons.rows(0, 1):
        if icon_id not in icon_names:
            icon_names[icon_id] = icons.string(fname)

    out = Out()

    slot_rows = sorted(slots.rows(0, 1, 2), key=lambda r: r[0])
    out.u32(len(slot_rows))
    for rec in slot_rows:
        for v in rec:
            out.u32(v)

    prop_rows = sorted(props.rows(0, 1, 2, 3), key=lambda r: r[0])
    out.u32(len(prop_rows))
    for pid, spell_id, slot_flags, icon_id in prop_rows:
        name = icon_names.get(icon_id)
        rune = 0 if name is None else rune_number(name)
        out.u32(pid).u32(spell_id).u32(slot_flags).u32(rune)

    pairs = []
    for r in spell.rows(0, *(_SP_EFFECT + _SP_MISC)):
        sid, effects, misc = r[0], r[1:4], r[4:7]
        for k in range(3):
            if effects[k] == _EFFECT_APPLY_GLYPH:
                pairs.append((sid, misc[k]))
                break
    pairs.sort()
    out.u32(len(pairs))
    for sid, glyph in pairs:
        out.u32(sid).u32(glyph)

    return out.bytes()
