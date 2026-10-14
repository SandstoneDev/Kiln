"""ui.pak entry "go_lock" (kind 3, WGLK).

Written from specs/bakers/ui_go_lock.md (v2) and the record layouts of
specs/world/gameobject_rules.md section 2.4 only.
"""

import struct

from .wdbc import BakeError, load_table, find_table_file

_INPUTS = (
    ("LockType", 53),
    ("Lock", 33),
    ("Spell", 113),
    ("SkillLineAbility", 3),
)

_EFFECT_OPEN_LOCK = 33
_SP_EFFECT = (71, 72, 73)
_SP_DIE_SIDES = (74, 75, 76)
_SP_BASE_POINTS = (80, 81, 82)
_SP_MISC = (110, 111, 112)

_PREFERRED_LOCALE = 8

# gameobject_rules.md 2.4
_HEADER = struct.Struct("<4sHHHHHHI")   # GoLockHeader, 20 bytes
_TYPE_REC = struct.Struct("<HHIIII")    # GoLockTypeRec, 20 bytes
_LOCK_REC = struct.Struct("<HBBHH")     # GoLockRec, 8 bytes
_CASE_REC = struct.Struct("<BBHII")     # GoLockCase, 12 bytes
_SPELL_REC = struct.Struct("<IBBHII")   # GoLockSpellRec, 16 bytes (i32 fields stored as patterns)
assert (_HEADER.size, _TYPE_REC.size, _LOCK_REC.size, _CASE_REC.size, _SPELL_REC.size) == \
    (20, 20, 8, 12, 16)


def _check16(value, what):
    if not 0 <= value <= 0xFFFF:
        raise BakeError("go_lock: %s %d does not fit 16 bits" % (what, value))
    return value


def _localized(table, row_values, base):
    """Locale slot 8 when not empty, else the first non-empty slot 0..15, else ""."""
    preferred = table.string(row_values[base + _PREFERRED_LOCALE])
    if preferred:
        return preferred
    for i in range(16):
        text = table.string(row_values[base + i])
        if text:
            return text
    return ""


class _Pool:
    def __init__(self):
        self.buf = bytearray(b"\0")
        self.offsets = {"": 0}

    def add(self, text):
        off = self.offsets.get(text)
        if off is None:
            off = len(self.buf)
            self.buf += text.encode("utf-8") + b"\0"
            self.offsets[text] = off
        return off

    def finish(self):
        while len(self.buf) % 4:
            self.buf.append(0)
        return bytes(self.buf)


def build_go_lock(root):
    """Return the bytes of the "go_lock" entry, or None ("no entry") when an input file is missing."""
    paths = {}
    for name, _needed in _INPUTS:
        path = find_table_file(root, name)
        if path is None:
            return None
        paths[name] = path
    lock_type, lock, spell, sla = (load_table(paths[name], name, needed)
                                   for name, needed in _INPUTS)

    # Lock types, ascending by ID; texts into the pool in output order
    pool = _Pool()
    type_recs = []
    lt_rows = sorted(lock_type.rows(*range(53)), key=lambda r: r[0])
    for r in lt_rows:
        tid = _check16(r[0], "LockType ID")
        name = pool.add(_localized(lock_type, r, 1))
        resource = pool.add(_localized(lock_type, r, 18))
        verb = pool.add(_localized(lock_type, r, 35))
        cursor = pool.add(lock_type.string(r[52]))
        type_recs.append(_TYPE_REC.pack(tid, 0, name, resource, verb, cursor))

    # Locks with at least one case, ascending by Lock ID; their cases back to back
    lock_recs, case_recs = [], []
    for r in sorted(lock.rows(*range(33)), key=lambda r: r[0]):
        lid = _check16(r[0], "Lock ID")
        types, indexes, skills, actions = r[1:9], r[9:17], r[17:25], r[25:33]
        cases = []
        for k in range(8):
            if types[k] != 0:
                if types[k] > 0xFF:
                    raise BakeError("go_lock: Lock %d Type[%d] = %d does not fit 8 bits"
                                    % (lid, k, types[k]))
                cases.append(_CASE_REC.pack(types[k], k, min(skills[k], 0xFFFF),
                                            indexes[k], actions[k]))
        if cases:
            first = _check16(len(case_recs), "first-case index")
            lock_recs.append(_LOCK_REC.pack(lid, len(cases), 0, first, 0))
            case_recs.extend(cases)

    # Skill line per spell: the last SkillLineAbility row in input order wins
    skill_line = {}
    for line, spell_id in sla.rows(1, 2):
        skill_line[spell_id] = line

    # Open-lock spells, ascending by spell ID (stable)
    spell_rows = []
    for r in spell.rows(0, *(_SP_EFFECT + _SP_DIE_SIDES + _SP_BASE_POINTS + _SP_MISC)):
        sid = r[0]
        effects, dies, bases, misc = r[1:4], r[4:7], r[7:10], r[10:13]
        for e in range(3):
            if effects[e] == _EFFECT_OPEN_LOCK:
                spell_rows.append((sid, e, misc[e] & 0xFF, skill_line.get(sid, 0) & 0xFFFF,
                                   bases[e], dies[e]))
                break
    spell_rows.sort(key=lambda t: t[0])
    spell_recs = [_SPELL_REC.pack(*t) for t in spell_rows]

    pool_bytes = pool.finish()
    header = _HEADER.pack(
        b"WGLK", 1,
        _check16(len(type_recs), "lock type count"),
        _check16(len(lock_recs), "lock count"),
        _check16(len(case_recs), "case count"),
        _check16(len(spell_recs), "spell count"),
        0, len(pool_bytes))
    return b"".join([header] + type_recs + lock_recs + case_recs + spell_recs + [pool_bytes])
