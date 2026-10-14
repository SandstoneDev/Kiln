"""text.pak entry "pet_text" (WPET).

Written from specs/bakers/text_pet_text.md (v2) only.
"""

import sys

from .wdbc import BakeError, Out, check_locale_index, f32_bits, find_table_file, load_table

# Happiness fallback (3, "Happiness")
_FALLBACK_THRESHOLDS = (0, 333000, 666000)
_FALLBACK_DAMAGE = tuple(f32_bits(x) for x in (0.75, 1.0, 1.25))


def _default_warn(message):
    print(message, file=sys.stderr)


def _load_or_warn(root, name, needed_fields, warn):
    """Optional table: missing or malformed -> warning line + None (a table with no rows)."""
    path = find_table_file(root, name)
    if path is None:
        warn("warning: pet_text: table %s not found, treated as empty" % name)
        return None
    try:
        return load_table(path, name, needed_fields)
    except (BakeError, OSError) as exc:
        warn("warning: pet_text: table %s malformed (%s), treated as empty" % (name, exc))
        return None


def _normalise_path(text):
    return text.replace("/", "\\").lower()


def build_pet_text(root, locale_index, warn=None):
    """Return the bytes of the "pet_text" entry (always produced).

    `warn` receives one line per missing / malformed table (default: printed to stderr).
    """
    L = check_locale_index(locale_index)
    warn = warn or _default_warn

    icons = _load_or_warn(root, "SpellIcon", 2, warn)
    families = _load_or_warn(root, "CreatureFamily", 28, warn)
    foods = _load_or_warn(root, "ItemPetFood", 17, warn)
    personality = _load_or_warn(root, "PetPersonality", 24, warn)
    stable = _load_or_warn(root, "StableSlotPrices", 2, warn)

    # Icon lookup: normalised TextureFilename -> ID; the last row in input order wins.
    icon_ids = {}
    if icons is not None:
        for icon_id, fname in icons.rows(0, 1):
            icon_ids[_normalise_path(icons.string(fname))] = icon_id

    out = Out().raw(b"WPET").u32(1)

    # Families
    fam_rows = []
    if families is not None:
        fam_rows = sorted(families.rows(0, 7, 8, 10 + L, 27), key=lambda r: r[0])
    out.u32(len(fam_rows))
    for fid, food_mask, talent, name_off, icon_off in fam_rows:
        icon_file = _normalise_path(families.string(icon_off))
        out.u32(fid).u32(talent).u32(food_mask).u32(icon_ids.get(icon_file, 0))
        out.cstr(families.string(name_off))

    # Foods
    food_rows = []
    if foods is not None:
        food_rows = sorted(foods.rows(0, 1 + L), key=lambda r: r[0])
    out.u32(len(food_rows))
    for food_id, name_off in food_rows:
        out.u32(food_id).cstr(foods.string(name_off))

    # Happiness: the first PetPersonality row whose ID is 1
    happiness = None
    if personality is not None:
        for r in personality.rows(0, 18, 19, 20, 21, 22, 23):
            if r[0] == 1:
                happiness = r[1:]
                break
    if happiness is None:
        happiness = _FALLBACK_THRESHOLDS + _FALLBACK_DAMAGE
    for v in happiness:
        out.u32(v)

    # Stable slots
    slot_rows = []
    if stable is not None:
        slot_rows = sorted(stable.rows(0, 1), key=lambda r: r[0])
    out.u32(len(slot_rows))
    for slot_id, cost in slot_rows:
        out.u32(slot_id).u32(cost)

    return out.bytes()
