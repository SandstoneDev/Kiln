"""WDBC table reader and small output helpers.

Written from specs/bakers/CONVENTIONS.md ("Input" and "Output conventions") only.

A table `Name` is looked up as `<root>/dbfilesclient/<name lower case>.dbc` first, then as
`<root>/DBFilesClient/<Name>.dbc`.  The file layout is: magic `WDBC`, then four little-endian
u32 values (record count, field count, record size, string block size), the records, then the
string block.  Every field the bakers read is a 32-bit little-endian value at byte offset
4 * field number inside its record.
"""

import os
import struct

__all__ = [
    "BakeError",
    "MissingTableError",
    "MalformedTableError",
    "MemoryRoot",
    "Table",
    "find_table_file",
    "table_exists",
    "load_table",
    "load_required",
    "load_optional",
    "check_locale_index",
    "Out",
    "f32_bits",
]

_HEADER = struct.Struct("<4sIIII")
_MAGIC = b"WDBC"
_U32 = struct.Struct("<I")
_MASK32 = 0xFFFFFFFF


class BakeError(Exception):
    """An error that stops the bake."""


class MissingTableError(BakeError):
    """A required input table has no file."""


class MalformedTableError(BakeError):
    """A table file exists but cannot be read as the WDBC table the baker needs."""


class MemoryRoot:
    """Test seam (brief-bakers.md 3): an extracted root held in memory.

    `tables` maps a table name (as written in the specs, e.g. "SpellIcon") to the bytes of its
    WDBC file. A name that is not in the mapping behaves like a missing file. Every callable
    accepts a MemoryRoot wherever it accepts a root folder.
    """

    def __init__(self, tables):
        self.tables = dict(tables)


class _MemoryFile:
    def __init__(self, data):
        self.data = data


def find_table_file(root, name):
    """Return the path of table `name` under `root`, or None when there is no file.

    The lower-case tree is tried first, then the mixed-case tree (CONVENTIONS.md "Input").
    """
    if isinstance(root, MemoryRoot):
        data = root.tables.get(name)
        return None if data is None else _MemoryFile(data)
    candidates = (
        os.path.join(root, "dbfilesclient", name.lower() + ".dbc"),
        os.path.join(root, "DBFilesClient", name + ".dbc"),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def table_exists(root, name):
    return find_table_file(root, name) is not None


class Table:
    """One parsed WDBC table. Field values are returned as unsigned 32-bit patterns."""

    def __init__(self, name, data, needed_fields):
        self.name = name
        if len(data) < _HEADER.size:
            raise MalformedTableError("%s: file shorter than the WDBC header" % name)
        magic, count, fields, rec_size, str_size = _HEADER.unpack_from(data, 0)
        if magic != _MAGIC:
            raise MalformedTableError("%s: magic is not WDBC" % name)
        end_records = _HEADER.size + count * rec_size
        end_strings = end_records + str_size
        if end_strings > len(data):
            raise MalformedTableError("%s: file shorter than its header declares" % name)
        if fields < needed_fields or rec_size < 4 * needed_fields:
            raise MalformedTableError(
                "%s: %d fields / %d-byte records, the baker reads %d fields"
                % (name, fields, rec_size, needed_fields))
        self.record_count = count
        self.field_count = fields
        self.record_size = rec_size
        self._data = bytes(data)
        self._rec_start = _HEADER.size
        self._str_start = end_records
        self._str_end = end_strings

    def __len__(self):
        return self.record_count

    def value(self, row, field):
        """Field `field` of record `row` as an unsigned 32-bit value."""
        off = self._rec_start + row * self.record_size + 4 * field
        return _U32.unpack_from(self._data, off)[0]

    def rows(self, *fields):
        """List of tuples (one per record, input order) holding the requested fields."""
        data = self._data
        unpack = _U32.unpack_from
        offs = [4 * f for f in fields]
        out = []
        base = self._rec_start
        for _ in range(self.record_count):
            out.append(tuple(unpack(data, base + o)[0] for o in offs))
            base += self.record_size
        return out

    def string(self, offset):
        """The string at `offset` of the string block (CONVENTIONS.md "String field")."""
        size = self._str_end - self._str_start
        if offset == 0 or offset >= size:
            return ""
        start = self._str_start + offset
        nul = self._data.find(b"\0", start, self._str_end)
        if nul < 0:
            nul = self._str_end  # no terminator: the string runs to the end of the block
        return self._data[start:nul].decode("utf-8", errors="replace")


def load_table(path, name, needed_fields):
    """Parse the table file found by find_table_file()."""
    if isinstance(path, _MemoryFile):
        return Table(name, path.data, needed_fields)
    with open(path, "rb") as fh:
        data = fh.read()
    return Table(name, data, needed_fields)


def load_required(root, name, needed_fields):
    """Load a required table: a missing file raises MissingTableError."""
    path = find_table_file(root, name)
    if path is None:
        raise MissingTableError("required table %s not found under %s" % (name, root))
    return load_table(path, name, needed_fields)


def load_optional(root, name, needed_fields):
    """Load an optional table: None when the file is missing; a malformed file raises."""
    path = find_table_file(root, name)
    if path is None:
        return None
    return load_table(path, name, needed_fields)


def check_locale_index(locale_index):
    if isinstance(locale_index, bool) or not isinstance(locale_index, int) \
            or not 0 <= locale_index <= 15:
        raise ValueError("locale index must be an integer 0..15, got %r" % (locale_index,))
    return locale_index


def f32_bits(value):
    """The IEEE-754 single bit pattern of `value` as an unsigned 32-bit integer."""
    return _U32.unpack(struct.pack("<f", value))[0]


class Out:
    """Little-endian output buffer."""

    def __init__(self):
        self.buf = bytearray()

    def raw(self, data):
        self.buf += data
        return self

    def u8(self, v):
        if not 0 <= v <= 0xFF:
            raise BakeError("value %d does not fit u8" % v)
        self.buf += struct.pack("<B", v)
        return self

    def u16(self, v):
        if not 0 <= v <= 0xFFFF:
            raise BakeError("value %d does not fit u16" % v)
        self.buf += struct.pack("<H", v)
        return self

    def u32(self, v):
        """A 32-bit pattern (unsigned value, or a signed one that is stored as two's complement)."""
        if not -0x80000000 <= v <= _MASK32:
            raise BakeError("value %d does not fit 32 bits" % v)
        self.buf += _U32.pack(v & _MASK32)
        return self

    def cstr(self, text):
        """UTF-8 text followed by one NUL byte."""
        self.buf += text.encode("utf-8") + b"\0"
        return self

    def bytes(self):
        return bytes(self.buf)
