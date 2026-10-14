"""Client identification.

Kiln works with exactly one client release. It identifies the release by the SHA-256 of the client's
executable (a top-level .exe of the game folder). A hash identifies the VERSION of the files; it does
not and cannot verify that the person running Kiln owns a licence for them - a copy obtained
elsewhere has the same hash. Kiln never claims otherwise.

The table below lists the executables that have been checked against this converter. Add a row only
after a full convert from that executable was tested on hardware.

Private-server distributions often ship the same release with a few bytes of the executable patched (the PE
header's large-address-aware flag and checksum, a handful of code bytes) and the Data archives untouched. A row's
"patch_sites" lists such places (file offset, length) and "masked_sha256" is the hash of the release's executable
with those bytes zeroed - no byte of the client is kept here. An executable of the row's size whose masked hash
matches is the same release (ClientInfo.patched: the places it may differ in).
"""
from __future__ import annotations

import hashlib
import os
import re
import struct
from dataclasses import dataclass, field

# Known client executables, one row per (locale, build). Kiln accepts build 12340 of any locale whose
# executable hash is listed here; an executable that is not listed is refused. "locale" is the locale
# of the client the row was verified with (the executable may be shared by several locales: then one
# row covers them all; whether it is is Unknown until a client of that locale was checked).
KNOWN_CLIENTS = [
    # One executable for every locale of this build: checked with a ruRU and an enUS client (2026-10-07,
    # byte-identical); the language comes from Data/<xxYY>/ (detect_locales).
    {"locale": "ruRU, enUS", "build": 12340, "version": "3.3.5a", "file_version": "3.3.5.12340", "size": 7704216,
     "sha256": "aa63a5750d60ef16746c686b3d5e26876d98953eab08b1c026cd0faf78e88cb8",
     # (file offset, length, name). Seen in a private-server client (2026-10): 15 bytes differ, all inside these
     # places; its Data archives are the stock 3.3.5a ones.
     "masked_sha256": "862b933339088fb4eab1d66fc07dcafe006a5127165bb2f5bfec101651ec9059",
     "patch_sites": [
         (0x126, 1, "pe_large_address_aware"),
         (0x168, 4, "pe_checksum"),
         (0x1F41BF, 1, "code_1"),
         (0x415A25, 1, "code_2"),
         (0x415A3F, 1, "code_3"),
         (0x415A95, 1, "code_4"),
         (0x415B46, 1, "code_5"),
         (0x415B5F, 7, "code_6"),
     ]},
]


def known_by_hash(digest):
    return [row for row in KNOWN_CLIENTS if row["sha256"] == digest]


def known_patched(path: str, size: int):
    """(row, the row's patch site names) when the executable is a known release changed only inside its
    patch_sites, else (None, []). Which of the places differ cannot be told: the release's bytes are not kept."""
    rows = [r for r in KNOWN_CLIENTS if r.get("patch_sites") and r["size"] == size]
    if not rows:
        return None, []
    with open(path, "rb") as f:
        data = f.read()
    exact = hashlib.sha256(data).hexdigest()
    for row in rows:
        buf = bytearray(data)
        sites = [(off, n, name) for off, n, name in row["patch_sites"] if off + n <= len(buf)]
        for off, n, _ in sites:
            buf[off:off + n] = bytes(n)
        if hashlib.sha256(buf).hexdigest() != row["masked_sha256"] or exact == row["sha256"]:
            continue
        return row, [name for _, _, name in sites]
    return None, []
    with open(path, "rb") as f:
        data = f.read()
    for row in rows:
        buf = bytearray(data)
        changed = []
        for off, orig_hex, name in row["patch_sites"]:
            orig = bytes.fromhex(orig_hex)
            if off + len(orig) > len(buf):
                break
            if buf[off:off + len(orig)] != orig:
                buf[off:off + len(orig)] = orig
                changed.append(name)
        else:
            if changed and hashlib.sha256(buf).hexdigest() == row["sha256"]:
                return row, changed
    return None, []


SUPPORTED_TEXT = "3.3.5a (build 12340) PC client"

_FIXED_INFO_SIG = b"\xbd\x04\xef\xfe"   # VS_FIXEDFILEINFO.dwSignature, little-endian (PE version resource)


@dataclass
class ClientInfo:
    game_dir: str
    exe: str            # file name inside game_dir
    sha256: str
    size: int
    entry: dict         # the KNOWN_CLIENTS row that matched
    locale: str         # primary locale: written to data/
    data_dir: str
    locales: list       # the locales to convert: the primary first, then the extra ones
    detected: list = field(default_factory=list)   # every locale present in this client's Data/
    sources: dict = field(default_factory=dict)    # extra locale -> the added client (--add-locale) it comes from
    default: str = ""   # the locale the game starts in (locales.txt "default"); "" = the primary
    present: bool = True    # False: rebuilt from an output's manifest (--add-locale without --game): no archives
    patched: list = field(default_factory=list)    # a patched executable: the row's patch site names (known_patched)

    @property
    def build(self) -> int:
        return int(self.entry["build"])

    @property
    def default_locale(self) -> str:
        return self.default or self.locale

    def source_of(self, loc: str) -> "ClientInfo":
        """The client whose archives a locale is extracted from: an added client or this one."""
        return self.sources.get(loc, self)

    def data_dir_for(self, loc: str) -> str:
        return self.source_of(loc).data_dir

    @property
    def label(self) -> str:
        e = self.entry
        text = "%s (build %d), executable verified with a %s client" % (e["version"], e["build"], e["locale"])
        if self.patched:
            text += "; patched executable (differs only at %d known places: %s) - the same release" % (
                len(self.patched), ", ".join(self.patched))
        return text

    @property
    def secondary(self) -> list:
        return [x for x in self.locales if x != self.locale]


class ClientError(RuntimeError):
    pass


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def pe_file_version(path: str) -> str | None:
    """The FILEVERSION of a Windows executable's version resource, e.g. '3.3.5.12340', or None."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    i = data.rfind(_FIXED_INFO_SIG)
    if i < 0 or i + 16 > len(data):
        return None
    _sig, _struc, ms, ls = struct.unpack_from("<IIII", data, i)
    return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def _find_data_dir(game_dir: str) -> str | None:
    for name in os.listdir(game_dir):
        p = os.path.join(game_dir, name)
        if name.lower() == "data" and os.path.isdir(p):
            return p
    return None


def detect_locales(data_dir: str) -> list[str]:
    """Locale folders of Data/ that hold their locale archive (e.g. Data/xxYY/locale-xxYY.MPQ)."""
    out = []
    for name in sorted(os.listdir(data_dir)):
        sub = os.path.join(data_dir, name)
        if not (os.path.isdir(sub) and re.fullmatch(r"[a-z]{2}[A-Z]{2}", name)):
            continue
        files = {f.lower() for f in os.listdir(sub)}
        if ("locale-%s.mpq" % name.lower()) in files:
            out.append(name)
    return out


PRIMARY_PREFERENCE = ("enUS", "enGB")


def primary_locale(locs):
    """The locale written to data/ when --locale is not given: the only one, else English, else the
    first in name order. The others go to data/locale/<xxYY>/."""
    if len(locs) == 1:
        return locs[0]
    for p in PRIMARY_PREFERENCE:
        if p in locs:
            return p
    return sorted(locs)[0]


def candidates(game_dir: str):
    """(name, size) of every top-level executable of the game folder, largest first."""
    out = []
    for name in os.listdir(game_dir):
        p = os.path.join(game_dir, name)
        if name.lower().endswith(".exe") and os.path.isfile(p):
            out.append((name, os.path.getsize(p)))
    return sorted(out, key=lambda t: -t[1])


def identify(game_dir: str, locale: str | None = None) -> ClientInfo:
    """Find the client executable by hash. Raises ClientError with a user-facing message."""
    if not os.path.isdir(game_dir):
        raise ClientError("game folder not found: %s" % game_dir)
    data_dir = _find_data_dir(game_dir)
    if data_dir is None:
        raise ClientError("no Data folder in %s - point --game at the folder that holds the client's "
                          "executable and its Data folder (the MPQ archives)." % game_dir)
    exes = candidates(game_dir)
    if not exes:
        raise ClientError("no executable (.exe) in %s - point --game at the client's own folder." % game_dir)
    sizes = {row["size"] for row in KNOWN_CLIENTS}
    seen = []
    # Executables whose size matches a known row first (cheap), then the rest.
    for name, size in sorted(exes, key=lambda t: (t[1] not in sizes, -t[1])):
        digest = sha256_file(os.path.join(game_dir, name))
        rows = known_by_hash(digest)
        patched = []
        if not rows:
            row, patched = known_patched(os.path.join(game_dir, name), size)
            rows = [row] if row else []
        if rows:
            locs = detect_locales(data_dir)
            if not locs:
                raise ClientError("no locale archives found in %s (expected Data/<locale>/locale-<locale>.MPQ)"
                                  % data_dir)
            if locale:
                if locale not in locs:
                    raise ClientError("locale %s requested, but Data/ has %s" % (locale, ", ".join(locs)))
                loc = locale
            else:
                loc = primary_locale(locs)
            return ClientInfo(game_dir, name, digest, size, rows[0], loc, data_dir, [loc] + [x for x in locs if x != loc],
                              detected=list(locs), patched=patched)
        seen.append((name, size, digest, pe_file_version(os.path.join(game_dir, name))))
    lines = ["unsupported or unknown client version - Kiln only converts the %s." % SUPPORTED_TEXT,
             "Executables checked in %s:" % game_dir]
    for name, size, digest, fv in seen:
        lines.append("  %-24s %10d bytes  sha256 %s  file version %s" % (name, size, digest, fv or "?"))
    lines.append("None matches a known client executable. Kiln does not convert other versions: the "
                 "converters were written and tested against that one release only.")
    raise ClientError("\n".join(lines))


def _same_folder(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def identify_added(game_dir: str, main: ClientInfo) -> ClientInfo:
    """--add-locale: another client folder of the SAME build as the main client, identified by the same hash
    check (an unknown executable is refused). Its locales become extra locales (merge_added)."""
    if _same_folder(game_dir, main.game_dir):
        raise ClientError("--add-locale %s is the main client folder (--game); point it at another client" % game_dir)
    try:
        info = identify(game_dir)
    except ClientError as e:
        raise ClientError("--add-locale %s: %s" % (game_dir, e)) from None
    if info.build != main.build:
        raise ClientError("--add-locale %s: build %d, but the main client is build %d - an added client must be the "
                          "same build" % (game_dir, info.build, main.build))
    return info


def merge_added(main: ClientInfo, added: list) -> list:
    """Adds every locale of the added clients that the main client does not convert itself to main.locales,
    extracted from that client's own archives (main.sources). The main client wins when it has the locale
    too; the first added client wins over a later one. -> user-facing notes."""
    notes = []
    for a in added:
        took = []
        for loc in a.detected:
            if loc == main.locale:
                notes.append("%s: %s is the primary locale of the main client - not added" % (a.game_dir, loc))
            elif loc in main.detected:
                notes.append("%s: %s is in the main client too - using the main client's" % (a.game_dir, loc))
                if loc not in main.locales:          # left out by --locales, asked for again by --add-locale
                    main.locales.append(loc)
            elif loc in main.sources:
                notes.append("%s: %s is already added from %s - using that one" % (a.game_dir, loc,
                                                                                 main.sources[loc].game_dir))
            else:
                main.sources[loc] = a
                main.locales.append(loc)
                took.append(loc)
        if took:
            notes.append("%s: extra locale(s) %s, extracted from that client's own archives"
                         % (a.game_dir, ", ".join(took)))
        else:
            notes.append("%s: adds no locale" % a.game_dir)
    return notes


def from_manifest(doc, data_dir=""):
    """The main client of an earlier output, rebuilt from its manifest (--add-locale / --remove-locale without
    --game: the main client folder may be gone). Its own archives are not used (present=False); the extra
    locales recorded there keep their recorded source. Raises ClientError."""
    rec_client, rec_loc = doc.get("client", {}), doc.get("locales", {})
    primary = rec_loc.get("primary")
    if not primary or not rec_client.get("sha256"):
        raise ClientError("the output's manifest does not name its client and primary locale (made by an older "
                          "Kiln?) - run with --game <the main client> instead")
    rows = known_by_hash(rec_client["sha256"])
    if not rows:
        raise ClientError("the output was made from an executable Kiln does not know (sha256 %s)"
                          % rec_client["sha256"])
    sources = rec_loc.get("sources", {})

    def stub(loc):
        src = sources.get(loc, {})
        d = src.get("client", "")
        return ClientInfo(d, "", src.get("sha256", rec_client["sha256"]), 0, rows[0], loc,
                          os.path.join(d, "Data") if d else "", [loc], detected=[loc], present=False)

    cl = stub(primary)
    cl.size = rec_client.get("size", 0)
    for x in rec_loc.get("extra", []):
        if x != primary and x not in cl.locales:
            cl.locales.append(x)
            cl.sources[x] = stub(x)
    cl.default = rec_loc.get("default", "") if rec_loc.get("default") in cl.locales else ""
    return cl
