"""data/locales.txt: which languages the data folder holds, for the EBOOT.

    # Kiln locales v1
    primary enUS
    extra ruRU
    default ruRU

UTF-8, LF line ends. Exactly one "primary" (the locale of data/), zero or more "extra" (each has its
localized files in data/locale/<xxYY>/), exactly one "default" (the locale the game starts in when
wowee.cfg has no "locale=" key; the primary or one of the extras). Lines starting with "#", empty lines
and lines with an unknown first word are ignored by readers, so a later version can add keys.

Kiln writes the file on every run (also when there is no extra locale). The player's choice made in
the game is stored by the EBOOT as "locale=xxYY" in wowee.cfg and wins over "default".
"""
from __future__ import annotations

import os
import re

FILE_NAME = "locales.txt"
HEADER = "# Kiln locales v1"
_LOCALE = re.compile(r"[a-z]{2}[A-Z]{2}")


class LocalesError(ValueError):
    pass


def is_locale(s: str) -> bool:
    return bool(_LOCALE.fullmatch(s or ""))


def render(primary: str, extras, default: str | None = None) -> str:
    default = default or primary
    extras = [x for x in extras if x != primary]
    for loc in [primary, default] + extras:
        if not is_locale(loc):
            raise LocalesError("not a locale name (xxYY): %r" % loc)
    if len(set(extras)) != len(extras):
        raise LocalesError("an extra locale is listed twice: %s" % ", ".join(extras))
    if default != primary and default not in extras:
        raise LocalesError("default locale %s is neither the primary (%s) nor an extra (%s)"
                           % (default, primary, ", ".join(extras) or "none"))
    lines = [HEADER, "primary " + primary] + ["extra " + x for x in extras] + ["default " + default]
    return "\n".join(lines) + "\n"


def write(data_dir: str, primary: str, extras, default: str | None = None) -> str:
    """Write data/locales.txt (atomically) and create data/locale/<xxYY>/ of every extra. -> path."""
    text = render(primary, extras, default)
    os.makedirs(data_dir, exist_ok=True)
    for x in extras:
        if x != primary:
            os.makedirs(os.path.join(data_dir, "locale", x), exist_ok=True)
    path = os.path.join(data_dir, FILE_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def parse(text: str) -> dict:
    """-> {"primary": str, "extra": [str], "default": str}. Raises LocalesError when a required key is
    missing or repeated; ignores comments, empty lines and unknown keys (as the EBOOT must)."""
    primary, default, extras = [], [], []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        key, val = parts[0], (parts[1] if len(parts) > 1 else "")
        if key in ("primary", "extra", "default") and not is_locale(val):
            raise LocalesError("%s: not a locale name: %r" % (key, val))
        if key == "primary":
            primary.append(val)
        elif key == "extra":
            extras.append(val)
        elif key == "default":
            default.append(val)
    if len(primary) != 1 or len(default) != 1:
        raise LocalesError("needs exactly one primary and one default line (found %d and %d)"
                           % (len(primary), len(default)))
    if default[0] != primary[0] and default[0] not in extras:
        raise LocalesError("default %s is neither the primary nor an extra" % default[0])
    return {"primary": primary[0], "extra": extras, "default": default[0]}


def read(data_dir: str) -> dict:
    with open(os.path.join(data_dir, FILE_NAME), encoding="utf-8") as f:
        return parse(f.read())
