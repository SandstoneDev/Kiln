"""text.pak entry "chat_chan".

Written from specs/bakers/text_chat_chan.md (v2) only.
"""

from .wdbc import Out, check_locale_index, load_required

_CITY_AREA_ID = 3459
_AREA_FLAG_CITY = 0x100


def build_chat_chan(root, locale_index):
    """Return the bytes of the "chat_chan" entry. Missing input files raise MissingTableError."""
    L = check_locale_index(locale_index)
    channels = load_required(root, "ChatChannels", 36)
    areas = load_required(root, "AreaTable", 27)

    out = Out()

    rows = sorted(channels.rows(0, 1, 2, 3 + L, 20 + L), key=lambda r: r[0])
    out.u32(len(rows))
    for cid, flags, faction, name_off, short_off in rows:
        out.u32(cid).u32(flags).u32(faction)
        out.cstr(channels.string(name_off)).cstr(channels.string(short_off))

    area_rows = areas.rows(0, 4, 11 + L)
    city_word = ""
    for aid, _flags, name_off in area_rows:
        if aid == _CITY_AREA_ID:
            city_word = areas.string(name_off)
            break
    out.cstr(city_word)

    cities = sorted(aid for aid, flags, _ in area_rows if flags & _AREA_FLAG_CITY)
    out.u32(len(cities))
    for aid in cities:
        out.u32(aid)

    return out.bytes()
