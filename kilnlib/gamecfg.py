"""wowee.cfg of the game folder: --server writes the realm list address.

The game reads its settings from wowee.cfg next to EBOOT.PBP (plain "key=value" lines, "#" comments). Kiln
only sets "server=" (and "port=" when one is given) and keeps every other line as it is; the account is never
written here - the player types it on the login screen.
"""
from __future__ import annotations

import os
import re

HOST_RX = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def parse_server(value: str) -> tuple[str, int | None]:
    """'host' or 'host:port' -> (host, port or None). Raises ValueError with a user-facing message."""
    v = value.strip()
    host, port = v, None
    if v.count(":") == 1:
        host, p = v.split(":")
        if not p.isdigit() or not 0 < int(p) < 65536:
            raise ValueError("--server %s: the port must be a number from 1 to 65535" % value)
        port = int(p)
    if not HOST_RX.match(host):
        raise ValueError("--server %s: not a host name or IPv4 address" % value)
    return host, port


def set_server(cfg_path: str, host: str, port: int | None) -> str:
    """Writes server= (and port=) into cfg_path, creating the file when it is missing. -> a line for the log."""
    lines, nl = [], "\r\n"
    if os.path.isfile(cfg_path):
        with open(cfg_path, "rb") as f:
            raw = f.read().decode("utf-8", "replace")
        nl = "\r\n" if "\r\n" in raw else "\n"
        lines = raw.replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
    want = {"server": host}
    if port is not None:
        want["port"] = str(port)
    done = set()
    out = []
    for ln in lines:
        key = ln.split("=", 1)[0].strip().lower() if "=" in ln and not ln.lstrip().startswith("#") else None
        if key in want:
            if key not in done:
                out.append("%s=%s" % (key, want[key]))
                done.add(key)
            continue  # a second line of the same key is dropped
        out.append(ln)
    if not lines:
        out.append("# WoWee PSP settings (key=value). The account is typed on the login screen.")
    for key in ("server", "port"):
        if key in want and key not in done:
            out.append("%s=%s" % (key, want[key]))
    os.makedirs(os.path.dirname(os.path.abspath(cfg_path)), exist_ok=True)
    with open(cfg_path, "wb") as f:
        f.write((nl.join(out) + nl).encode("utf-8"))
    return "wowee.cfg: server=%s%s -> %s" % (host, (" port=%d" % port) if port is not None else "", cfg_path)
