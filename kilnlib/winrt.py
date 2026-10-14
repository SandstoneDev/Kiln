"""The converters' runtime on Windows: no error dialogs, which DLL folder, and a start check.

* set_error_mode(): a converter whose DLL is missing must end with an exit code (0xC0000135), never with a
  "System Error ... was not found" dialog per start. The error mode is inherited by every child process.
* pe_imports() reads the import table of an executable or DLL (no third-party module); missing_dlls()
  follows the imports through the DLLs it finds and lists the ones found nowhere.
* resolve_dll_dir(): the folder put on the converters' PATH when --dll-dir is not given - none when the
  DLLs are next to the converters (the bundle), else KILN_DLL_DIR, else an MSYS2 UCRT64 installation
  (a developer setup).
* preflight(): starts every converter once without arguments (it prints its usage and exits); a start
  failure stops Kiln before any extraction with the missing DLLs named.
"""
from __future__ import annotations

import os
import struct
import subprocess

SEM_FAILCRITICALERRORS = 0x0001
SEM_NOGPFAULTERRORBOX = 0x0002
SEM_NOOPENFILEERRORBOX = 0x8000

# Exit codes of a process that could not start or crashed in the loader (NTSTATUS, as Python reports them).
START_FAILURES = {
    0xC0000135: "a DLL was not found (STATUS_DLL_NOT_FOUND)",
    0xC000007B: "a DLL is for another architecture or damaged (STATUS_INVALID_IMAGE_FORMAT)",
    0xC0000139: "a DLL lacks a function the program needs (STATUS_ENTRYPOINT_NOT_FOUND)",
    0xC0000142: "a DLL failed to initialise (STATUS_DLL_INIT_FAILED)",
}
CANNOT_START = 127          # Kiln's own code for "the program could not be started at all" (pipeline.run_cmd)

_DRIVE = os.environ.get("SystemDrive", "C:") + os.sep
# Where an MSYS2 installation keeps its UCRT64 runtime (the installer's default and the Chocolatey one).
MSYS2_CANDIDATES = [os.path.join(_DRIVE, "msys64", "ucrt64", "bin"),
                    os.path.join(_DRIVE, "tools", "msys64", "ucrt64", "bin")]


def set_error_mode():
    """No loader or crash dialogs for Kiln and the programs it starts (Windows only). -> previous mode or None"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        return k32.SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX)
    except (OSError, AttributeError):
        return None


def rc_text(rc):
    rc &= 0xFFFFFFFF
    if rc in START_FAILURES:
        return "0x%08X: %s" % (rc, START_FAILURES[rc])
    if rc >= 0xC0000000:
        return "0x%08X: the program crashed" % rc
    return str(rc)


def is_start_failure(rc):
    return rc == CANNOT_START or (rc & 0xFFFFFFFF) in START_FAILURES


# -- PE import table -----------------------------------------------------------------------------------------------
def pe_imports(path):
    """DLL names a PE file imports (import + delay-import directories), in table order. [] if not a PE."""
    try:
        with open(path, "rb") as f:
            d = f.read()
    except OSError:
        return []
    if d[:2] != b"MZ" or len(d) < 0x40:
        return []
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    if d[pe:pe + 4] != b"PE\0\0":
        return []
    nsec, opt_size = struct.unpack_from("<H", d, pe + 6)[0], struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", d, opt)[0]
    dd = opt + (96 if magic == 0x10B else 112)          # data directories of PE32 / PE32+
    ndirs = struct.unpack_from("<I", d, dd - 4)[0]
    secs = []
    for i in range(nsec):
        s = opt + opt_size + i * 40
        vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", d, s + 8)
        secs.append((va, max(vsize, rawsize), rawptr))

    def off(rva):
        for va, size, raw in secs:
            if va <= rva < va + size:
                return raw + rva - va
        return None

    def cstr(rva):
        o = off(rva)
        if o is None:
            return ""
        end = d.find(b"\0", o)
        return d[o:end].decode("latin-1")

    names = []
    for index, stride, name_at in ((1, 20, 12), (13, 32, 4)):      # import, delay import
        if index >= ndirs:
            continue
        rva = struct.unpack_from("<I", d, dd + index * 8)[0]
        o = off(rva) if rva else None
        while o is not None and o + stride <= len(d):
            entry = d[o:o + stride]
            if not any(entry):
                break
            n = cstr(struct.unpack_from("<I", entry, name_at)[0])
            if n and n.lower() not in (x.lower() for x in names):
                names.append(n)
            o += stride
    return names


def system_dirs():
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or os.path.join(_DRIVE, "Windows")
    return [os.path.join(root, "System32"), root]


def _find(name, dirs):
    if name.lower().startswith(("api-ms-win-", "ext-ms-")):
        return "(api set)"
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def missing_dlls(exe, dirs):
    """The DLLs exe needs (directly or through the DLLs it finds) that are in none of: the exe's folder,
    dirs, the system folders. -> sorted names"""
    search = [os.path.dirname(os.path.abspath(exe))] + [d for d in dirs if d] + system_dirs()
    seen, missing, todo = set(), set(), [exe]
    sysdirs = [os.path.normcase(x) for x in system_dirs()]
    while todo:
        f = todo.pop()
        for n in pe_imports(f):
            k = n.lower()
            if k in seen:
                continue
            seen.add(k)
            p = _find(n, search)
            if p is None:
                missing.add(n)
            elif p != "(api set)" and not os.path.normcase(os.path.dirname(p)) in sysdirs:
                todo.append(p)              # a non-system DLL: its own imports count too
    return sorted(missing, key=str.lower)


def resolve_dll_dir(exes, given=""):
    """--dll-dir, else the first of: the converters' own folder, KILN_DLL_DIR, an MSYS2 UCRT64 bin folder,
    whose DLLs complete every converter. -> (folder or "", how it was chosen)"""
    if given:
        return given, "--dll-dir"
    exes = [e for e in exes if os.path.isfile(e)]
    if not exes or not any(missing_dlls(e, []) for e in exes):
        return "", "the converters' own folder"
    env = os.environ.get("KILN_DLL_DIR", "")
    cands = ([(env, "KILN_DLL_DIR")] if env else []) + [(c, "MSYS2 UCRT64 (%s)" % c) for c in MSYS2_CANDIDATES]
    for d, how in cands:
        if os.path.isdir(d) and not any(missing_dlls(e, [d]) for e in exes):
            return d, how
    return (env, "KILN_DLL_DIR (incomplete)") if env else ("", "none found")


def preflight(exes, env, dll_dirs, timeout=30):
    """Start every converter once without arguments (in an empty temporary folder: a usage call writes
    nothing). -> [(exe, rc text, missing DLLs)] of the ones that cannot start (an empty list = all start)."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="kiln_preflight_") as cwd:
        return _preflight(exes, env, cwd, dll_dirs, timeout)


def _preflight(exes, env, cwd, dll_dirs, timeout):
    bad = []
    for exe in sorted(set(exes)):
        try:
            r = subprocess.run([exe], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=timeout)
            rc = r.returncode
        except subprocess.TimeoutExpired:
            continue                         # it started (and waits for something): not a start failure
        except OSError:
            rc = CANNOT_START
        if is_start_failure(rc) or (rc & 0xFFFFFFFF) >= 0xC0000000:
            bad.append((exe, rc_text(rc), missing_dlls(exe, dll_dirs)))
    return bad
