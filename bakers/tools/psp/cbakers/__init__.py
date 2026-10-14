"""Clean-room bakers for seven text.pak / ui.pak data entries (specs/bakers, v2).

One callable per entry; each takes the extracted root folder (and a locale index where the
spec says so) and returns the entry's bytes, or None for "no entry".

    build_class_bars(root)            -> bytes ("WCBR")
    build_class_vehicles(root)        -> bytes ("WCBV")
    build_pet_text(root, locale)      -> bytes ("WPET"), always produced
    build_chat_chan(root, locale)     -> bytes
    build_glyph_data(root)            -> bytes
    build_auc_data(root)              -> bytes (UTF-8 text) or None
    build_go_lock(root)               -> bytes ("WGLK") or None

Errors that stop the bake are raised as cbakers.wdbc.BakeError (MissingTableError for a
missing required table, MalformedTableError for an unreadable table file).

Test seam: every callable also accepts a cbakers.MemoryRoot({table name: WDBC bytes}) in
place of a root folder.
"""

from .wdbc import BakeError, MalformedTableError, MemoryRoot, MissingTableError
from .class_bars import build_class_bars, build_class_vehicles
from .pet_text import build_pet_text
from .chat_chan import build_chat_chan
from .glyph_data import build_glyph_data
from .auc_data import build_auc_data
from .go_lock import build_go_lock

# entry name -> (callable, takes a locale index)
ENTRIES = {
    "class_bars": (build_class_bars, False),
    "class_vehicles": (build_class_vehicles, False),
    "pet_text": (build_pet_text, True),
    "chat_chan": (build_chat_chan, True),
    "glyph_data": (build_glyph_data, False),
    "auc_data": (build_auc_data, False),
    "go_lock": (build_go_lock, False),
}

__all__ = [
    "BakeError", "MalformedTableError", "MemoryRoot", "MissingTableError", "ENTRIES",
    "build_class_bars", "build_class_vehicles", "build_pet_text", "build_chat_chan",
    "build_glyph_data", "build_auc_data", "build_go_lock",
]
