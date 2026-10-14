"""text.pak entries "class_bars" (WCBR) and "class_vehicles" (WCBV).

Written from specs/bakers/text_class_bars.md (v2) only.
"""

from .wdbc import Out, load_required

# Spell fields (text_class_bars.md 2, "Input fields used")
_SP_ID = 0
_SP_ATTR = 4
_SP_ATTR_EX2 = 6
_SP_ATTR_EX7 = 11
_SP_EFFECT = (71, 72, 73)
_SP_AURA = (95, 96, 97)
_SP_MISC_B = (113, 114, 115)
_SP_STANCE_ORDER = 215
_SP_TOTEM_CAT = (222, 223)

# Rule constants (3.1 .. 3.3)
_AURA_SHAPESHIFT = 36
_AURA_CONTROL_BASE = (2, 6, 128)
_AURA_VEHICLE = 236
_EFFECT_SUMMON = 28
_SUMMON_CONTROL_POSSESSED = 3

_ATTR2_EXCLUDE = 0x2
_ATTR2_STANCE_BAR = 0x10
_ATTR7_TOTEM = 0x20
_ATTR7_FLAG8 = 0x1000
_ATTR0_FLAG10 = 0x80000000

# TotemCategory value -> slot bits (3.2)
_TOTEM_BITS = {4: 0x1, 2: 0x2, 5: 0x4, 3: 0x8, 21: 0xF}


def _by_id(rows):
    """Stable ascending sort by the first column (the row ID)."""
    return sorted(rows, key=lambda r: r[0])


def build_class_bars(root):
    """Return the bytes of the "class_bars" entry. Missing input files raise MissingTableError."""
    spell_fields = ((_SP_ID, _SP_ATTR, _SP_ATTR_EX2, _SP_ATTR_EX7) + _SP_EFFECT + _SP_AURA
                    + _SP_MISC_B + (_SP_STANCE_ORDER,) + _SP_TOTEM_CAT)
    spell = load_required(root, "Spell", max(spell_fields) + 1)
    summon = load_required(root, "SummonProperties", 2)
    forms = load_required(root, "SpellShapeshiftForm", 35)
    overrides = load_required(root, "OverrideSpellData", 11)
    categories = load_required(root, "TotemCategory", 20)

    possessed = {sid for sid, control in summon.rows(0, 1)
                 if control == _SUMMON_CONTROL_POSSESSED}

    t1, t2, t3 = [], [], []
    for r in spell.rows(*spell_fields):
        (sid, attr, attr2, attr7, e0, e1, e2, a0, a1, a2, mb0, mb1, mb2,
         order, tc0, tc1) = r
        effects = (e0, e1, e2)
        auras = (a0, a1, a2)
        misc_b = (mb0, mb1, mb2)

        # 3.1 shapeshift bar
        if not attr2 & _ATTR2_EXCLUDE and (
                _AURA_SHAPESHIFT in auras or attr2 & _ATTR2_STANCE_BAR):
            t1.append((sid, order))

        # 3.2 totem slots
        if attr7 & _ATTR7_TOTEM:
            mask = _TOTEM_BITS.get(tc0, 0) | _TOTEM_BITS.get(tc1, 0)
            if mask:
                t2.append((sid, mask))

        # 3.3 control spells
        flags = 0
        if any(a in _AURA_CONTROL_BASE for a in auras):
            flags |= 0x1
        if _AURA_VEHICLE in auras:
            flags |= 0x4
        if any(effects[i] == _EFFECT_SUMMON and misc_b[i] in possessed for i in range(3)):
            flags |= 0x2
        if flags:
            if attr7 & _ATTR7_FLAG8:
                flags |= 0x8
            if attr & _ATTR0_FLAG10:
                flags |= 0x10
            t3.append((sid, flags))

    # 3.5 T4..T6
    t4 = [r for r in _by_id(forms.rows(0, *range(27, 35))) if any(r[1:])]
    t5 = _by_id(overrides.rows(0, *range(1, 11)))
    t6 = _by_id(categories.rows(0, 18, 19))

    out = Out().raw(b"WCBR").u32(1)
    for table in (t1, t2, t3, t4, t5, t6):
        out.u32(len(table))
        for rec in table:
            for v in rec:
                out.u32(v)
    return out.bytes()


# Vehicle record columns in output order (2.2)
_VEHICLE_COLUMNS = (0, 1, 2, 3, 4, 5) + tuple(range(6, 14)) + (36, 37)
# Seat record columns in output order (2.2)
_SEAT_COLUMNS = (0, 1, 45, 2, 3, 4, 5, 15, 16, 41, 44, 50, 51, 52, 29, 35)
# Indicator-seat record columns (2.2); the row's own ID is not stored
_IND_COLUMNS = (1, 2, 3, 4)


def build_class_vehicles(root):
    """Return the bytes of the "class_vehicles" entry. Missing input files raise MissingTableError."""
    vehicle = load_required(root, "Vehicle", max(_VEHICLE_COLUMNS) + 1)
    seat = load_required(root, "VehicleSeat", max(_SEAT_COLUMNS) + 1)
    ind = load_required(root, "VehicleUIIndSeat", max(_IND_COLUMNS) + 1)

    vehicles = _by_id(vehicle.rows(*_VEHICLE_COLUMNS))
    seats = _by_id(seat.rows(*_SEAT_COLUMNS))
    inds = sorted(ind.rows(*_IND_COLUMNS), key=lambda r: (r[0], r[1]))  # stable

    out = Out().raw(b"WCBV").u32(1)
    for table in (vehicles, seats, inds):
        out.u32(len(table))
        for rec in table:
            for v in rec:
                out.u32(v)
    return out.bytes()
