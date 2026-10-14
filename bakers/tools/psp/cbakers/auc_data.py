"""ui.pak entry "auc_data" (kind 3, UTF-8 text).

Written from specs/bakers/ui_auc_data.md (v2) only.
"""

from .wdbc import load_optional

_LOCALE = 8  # fixed locale index, no detection, no fallback

# Browse categories: item class IDs in the window's fixed order (3, step 2)
CLASS_ORDER = (2, 4, 1, 0, 16, 7, 6, 11, 9, 3, 15, 12)

_SUBCLASS_HIDDEN = 0x2      # DisplayFlags bit: subclass not listed
_SUBCLASS_SLOT_FILTER = 0x200  # Flags bit: slot-filter flag "1"


def build_auc_data(root):
    """Return the bytes of the "auc_data" entry, or None ("no entry") when no line is produced."""
    houses = load_optional(root, "AuctionHouse", 4)
    classes = load_optional(root, "ItemClass", 3 + _LOCALE + 1)
    subclasses = load_optional(root, "ItemSubClass", 27 + _LOCALE + 1)

    lines = []

    if houses is not None:
        for hid, deposit, consignment in houses.rows(0, 2, 3):
            lines.append("H\t%d\t%d\t%d" % (hid, deposit, consignment))

    if classes is not None and subclasses is not None:
        # Class ID -> name (first row of that ID in input order; IDs are unique in real data)
        class_names = {}
        for cid, name_off in classes.rows(0, 3 + _LOCALE):
            if cid not in class_names:
                class_names[cid] = classes.string(name_off)
        sub_rows = subclasses.rows(0, 1, 4, 5, 10 + _LOCALE, 27 + _LOCALE)
        for cid in CLASS_ORDER:
            lines.append("C\t%d\t%s" % (cid, class_names.get(cid, "")))
            for class_id, sub_id, flags, display_flags, disp_off, verb_off in sub_rows:
                if class_id != cid or display_flags & _SUBCLASS_HIDDEN:
                    continue
                name = subclasses.string(verb_off) or subclasses.string(disp_off)
                slot_filter = 1 if flags & _SUBCLASS_SLOT_FILTER else 0
                lines.append("S\t%d\t%d\t%s" % (sub_id, slot_filter, name))

    if not lines:
        return None
    return "".join(line + "\n" for line in lines).encode("utf-8")
