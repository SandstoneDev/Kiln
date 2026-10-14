"""Plan and run the steps: extraction, PNG side files, probes, bakers, verification, manifest.

Progress goes to the console as text, or with --json as one JSON object per line (for a GUI):
    {"event": "client" | "plan" | "phase" | "step" | "running" | "log" | "done" | "error", ...}
"""
from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time

from . import KILN_VERSION, mapdb, steps as S, winrt, xmb
from .manifest import sha256_file

EXE = ".exe" if os.name == "nt" else ""
PATH_PARAMS = ("X", "XG", "D", "W", "L")


class Ctx:
    """Everything a run needs to know about folders, programs and output."""

    def __init__(self, a, client, kiln_root):
        self.a = a
        self.client = client
        self.kiln_root = kiln_root
        self.out = os.path.abspath(a.out)
        self.data = os.path.join(self.out, "data")
        self.logs = os.path.join(self.out, "logs")
        self.work = os.path.abspath(a.work) if a.work else os.path.join(self.out, "work")
        self.x = os.path.join(self.work, "extracted")
        self.xg = os.path.join(self.work, "extracted_scenes")
        self.lists = os.path.join(self.work, "lists")
        self.tools = os.path.abspath(a.tools) if a.tools else os.path.join(kiln_root, "bin")
        self.bakers = os.path.abspath(a.bakers) if a.bakers else os.path.join(kiln_root, "bakers")
        self.python = a.python or sys.executable
        self.dll_dir = a.dll_dir or os.environ.get("KILN_DLL_DIR") or ""
        self.mirror = os.path.abspath(a.mirror) if getattr(a, "mirror", None) else ""
        self.jobs = max(1, a.jobs)
        self.json = bool(getattr(a, "json", False))
        self.print_lock = threading.Lock()
        self.map0 = None
        self.hash_cache = {}
        self.running = {}            # step name -> start time
        self.running_locale = {}     # step name -> its locale (for the 'running' events)

    # -- locales --------------------------------------------------------------------------------
    def locale_folders(self, loc):
        """Folders of a secondary locale (the primary one uses the defaults)."""
        w = os.path.join(self.work, "locale", loc)
        return {"X": os.path.join(w, "extracted"), "XG": os.path.join(w, "extracted_scenes"),
                "D": os.path.join(self.data, "locale", loc), "W": w, "L": os.path.join(w, "lists")}

    def folder(self, step, key):
        defaults = {"X": self.x, "XG": self.xg, "D": self.data, "W": self.work, "L": self.lists}
        return step.params.get(key, defaults[key])

    def step_locale(self, step):
        return step.params.get("loc", self.client.locale if self.client else "")

    # -- programs -------------------------------------------------------------------------------
    def program(self, token):
        kind, _, name = token.partition(":")
        if kind == "@exe":
            return [os.path.join(self.tools, name + EXE)]
        if kind == "@py":
            return [self.python, os.path.join(self.bakers, "tools", "psp", name)]
        raise ValueError(token)

    def program_path(self, token):
        return self.program(token)[-1]

    def env(self, argv=None):
        """The converters get --tools and --dll-dir on PATH; the Python bakers do not: a toolchain folder
        on PATH changes what Pillow loads (its text layout engine) and with it the baked fonts."""
        e = dict(os.environ)
        if not (argv and argv[0] == self.python):
            extra = [p for p in (self.tools, self.dll_dir) if p]
            e["PATH"] = os.pathsep.join(extra + [e.get("PATH", "")])
        e["PYTHONIOENCODING"] = "utf-8"
        return e

    # -- output ---------------------------------------------------------------------------------
    def _logline(self, msg):
        if getattr(self.a, "dry_run", False):
            return                       # a dry run writes nothing
        try:
            os.makedirs(self.logs, exist_ok=True)
            with open(os.path.join(self.logs, "kiln.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + msg + "\n")
        except OSError:
            pass

    def event(self, kind, **fields):
        if not self.json:
            return
        doc = {"event": kind, "t": round(time.time(), 1)}
        doc.update(fields)
        with self.print_lock:
            sys.stdout.write(json.dumps(doc, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def say(self, msg, log=True):
        if self.json:
            self.event("log", text=msg)
        else:
            with self.print_lock:
                print(msg, flush=True)
        if log:
            self._logline(msg)

    # -- templates ------------------------------------------------------------------------------
    def expand(self, cmd, step):
        vals = {"X": self.x, "XG": self.xg, "D": self.data, "W": self.work, "L": self.lists,
                "jobs": str(self.jobs), "map0": self.map0 or "?", "loc": self.step_locale(step)}
        vals.update({k: v for k, v in step.params.items() if isinstance(v, str)})
        argv = []
        for i, t in enumerate(cmd):
            if i == 0 and t.startswith("@"):
                argv += self.program(t)
            elif t == "{tiles}":
                tiles = step.params.get("tiles")
                if tiles == "global" or not tiles:
                    argv += ["--around", "32", "32", "32"]
                else:
                    for x, y in tiles:
                        argv += ["--tile", "%d_%d" % (x, y)]
            elif t == "{?mirror}":
                if self.mirror:
                    argv += ["--mirror", self.mirror]
            else:
                v = t.format(**vals)
                if t.startswith(("{X}", "{XG}", "{D}", "{W}", "{L}")):
                    v = os.path.normpath(v)
                argv.append(v)
        return argv

    def outputs_glob(self, step):
        vals = {k: v for k, v in step.params.items() if isinstance(v, str) and k not in PATH_PARAMS}
        found = set()
        for p in step.outputs:
            for f in glob.glob(os.path.join(self.out, p.format(**vals)), recursive=True):
                if os.path.isfile(f) and not f.endswith(".tmp"):
                    found.add(os.path.relpath(f, self.out))
        return sorted(found)

    def step_key(self, step):
        """Definition hash: templates, parameters and the programs' own hashes. A new converter or
        baker, or a changed command line, re-runs the step."""
        progs = {}
        for t in step.programs():
            p = self.program_path(t)
            if p not in self.hash_cache:
                self.hash_cache[p] = sha256_file(p) if os.path.exists(p) else "missing"
            progs[t] = self.hash_cache[p]
        params = {k: (v if isinstance(v, str) else len(v)) for k, v in step.params.items() if k not in PATH_PARAMS}
        doc = {"run": step.run, "verify": step.verify, "probes": [p.cmd for p in step.probes], "params": params,
               "tiles": hashlib.sha256(repr(step.params.get("tiles")).encode()).hexdigest(),
               "programs": progs, "build": self.client.build if self.client else 0,
               "map0": self.map0 if "{map0}" in json.dumps(step.run) else None,
               "mirror": "{?mirror}" in json.dumps(step.run) and bool(self.mirror)}
        return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()

    # -- identity: what made a step's outputs (recorded in the manifest; --check / --update compare it) ----------
    def file_hash(self, path):
        if path not in self.hash_cache:
            self.hash_cache[path] = sha256_file(path) if os.path.exists(path) else "missing"
        return self.hash_cache[path]

    def tree_hash(self, root, patterns):
        """One sha256 over the relative paths and contents of the files under root matching the patterns."""
        key = ("tree", root, tuple(patterns))
        if key not in self.hash_cache:
            h = hashlib.sha256()
            files = sorted({f for p in patterns for f in glob.glob(os.path.join(root, p), recursive=True)
                            if os.path.isfile(f) and "__pycache__" not in f})
            for f in files:
                h.update(os.path.relpath(f, root).replace(os.sep, "/").encode() + b"\0")
                h.update(self.file_hash(f).encode())
            self.hash_cache[key] = h.hexdigest() if files else "missing"
        return self.hash_cache[key]

    def python_versions(self):
        """Python, Pillow and NumPy of the bakers' Python (Pillow's text layout engine changes the fonts)."""
        if "python" not in self.hash_cache:
            code = ("import sys, PIL, numpy; print('python %d.%d.%d Pillow %s numpy %s' % "
                    "(sys.version_info[:3] + (PIL.__version__, numpy.__version__)))")
            try:
                r = subprocess.run([self.python, "-c", code], capture_output=True, text=True, timeout=120,
                                   env=self.env([self.python]))
                self.hash_cache["python"] = r.stdout.strip() or "unknown (rc %d)" % r.returncode
            except (OSError, subprocess.SubprocessError) as e:
                self.hash_cache["python"] = "unknown (%s)" % e
        return self.hash_cache["python"]

    def identity(self, tokens, locale, py=False, png=False, builtin=""):
        """The converters' and bakers' identity and the input identity of a step (see step_identity)."""
        src = self.client.source_of(locale) if hasattr(self.client, "source_of") else self.client
        doc = {"programs": {t: self.file_hash(self.program_path(t)) for t in sorted(tokens)},
               "input": {"client_sha256": getattr(src, "sha256", ""), "locale": locale,
                         "build": getattr(self.client, "build", 0)}}
        if py:
            doc["bakers"] = self.tree_hash(os.path.join(self.bakers, "tools", "psp"), ["*.py", "art/**/*"])
            doc["python"] = self.python_versions()
        if png:
            doc["png"] = "kiln %s blp %s" % (KILN_VERSION, self.file_hash(os.path.join(os.path.dirname(
                os.path.abspath(__file__)), "blp.py"))[:16])
        if builtin:
            doc["kiln"] = KILN_VERSION
            if builtin == "profile":
                doc["profiles"] = self.tree_hash(os.path.join(self.bakers, "Data", "expansions"), ["*/*.json"])
        return doc

    def step_identity(self, step):
        """Recorded per step in the manifest: sha256 of every converter executable and baker script it runs
        (Python bakers: also their helper modules and art, and the Python/Pillow/NumPy versions; steps done by
        Kiln: Kiln's version), and the input - the executable hash of the client the step's locale is read
        from, the locale and the build."""
        progs = step.programs()
        return self.identity(progs, self.step_locale(step), py=any(t.startswith("@py:") for t in progs),
                             png=step.png, builtin=step.builtin)

    def identity_now(self, recorded):
        """The current identity in the shape of a recorded one (without the step table: a map step whose map
        table is no longer extracted can still be checked)."""
        return self.identity(recorded.get("programs", {}), recorded.get("input", {}).get("locale", ""),
                             py="bakers" in recorded, png="png" in recorded,
                             builtin=("profile" if "profiles" in recorded else "x") if "kiln" in recorded else "")


# ------------------------------------------------------------------------------------------------
def resolve_map0(ctx):
    """Directory of map id 0 from the extracted map table (used by the sound step). The map table's directory
    names are the same in every locale: without the primary's tree (a language added later) an extra locale's
    tree serves."""
    if ctx.map0 is None:
        trees = [ctx.x] + [ctx.locale_folders(x)["X"] for x in (ctx.client.secondary if ctx.client else [])]
        for tree in trees:
            if os.path.exists(os.path.join(tree, "dbfilesclient", "map.dbc")):
                recs, _f, string = mapdb.read_dbc(os.path.join(tree, "dbfilesclient", "map.dbc"))
                for r in recs:
                    if struct.unpack_from("<I", r, 0)[0] == 0:
                        ctx.map0 = string(struct.unpack_from("<I", r, 4)[0])
                break
    return ctx.map0


WDT_LIST = "wdt_maps.txt"   # in the primary's list folder: written once every map's WDT is extracted


def map_wdt_names(mapdbc):
    """The WDT of every map of the client's map table, in the archives' own (backslash) form."""
    recs, _f, string = mapdb.read_dbc(mapdbc)
    dirs = sorted({string(struct.unpack_from("<I", r, 4)[0]) for r in recs} - {""})
    return ["World\\Maps\\%s\\%s.wdt" % (d, d) for d in dirs]


def maps_ready(ctx):
    """The per-map steps can be made: the map table and every map's WDT are in the primary's tree."""
    return (os.path.exists(os.path.join(ctx.x, "dbfilesclient", "map.dbc"))
            and os.path.exists(os.path.join(ctx.lists, WDT_LIST)))


def extract_map_wdts(ctx):
    """Extract every map's WDT (the map steps' tiles and global WMOs are read from them; no other step's
    client paths are needed for that). -> extractor rc"""
    mapdbc = os.path.join(ctx.x, "dbfilesclient", "map.dbc")
    if not os.path.exists(mapdbc):
        return 1
    os.makedirs(ctx.lists, exist_ok=True)
    final = os.path.join(ctx.lists, WDT_LIST)
    tmp = final + ".tmp"
    names = map_wdt_names(mapdbc)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(n + "\n" for n in names))
    loc = ctx.client.locale
    rc = extract(ctx, [], names_file=tmp, target=ctx.x, locale=loc, log_name="extract_%s.log" % loc)
    if rc == 0:
        os.replace(tmp, final)
    ctx.say("maps: the WDT of %d maps of the client's map table -> extractor (rc %d)" % (len(names), rc))
    return rc


def build_steps(ctx, map_groups, locales=None):
    """Static steps + per-map steps (when the client's map table and the maps' WDTs are already extracted)
    + the copies of the localized steps for every secondary locale."""
    all_steps = S.static_steps(ctx.bakers)
    maps_note = None
    if os.path.exists(os.path.join(ctx.x, "dbfilesclient", "map.dbc")):
        resolve_map0(ctx)
    if map_groups and maps_ready(ctx):
        chosen, allmaps = mapdb.select_maps(ctx.x, map_groups, python=ctx.python,
                                            dungeon_lister=os.path.join(ctx.bakers, "tools", "psp",
                                                                        "dungeon_list.py"),
                                            log=lambda m: ctx.say(m))
        for g, m in chosen:
            all_steps += S.map_steps(g, m)
    elif map_groups:
        maps_note = ("per-map steps (terrain_/doodads_/minimap_<map>.pak for %s) are added after the client "
                     "tables and the maps' WDT files are extracted - they are read from the client's own map table"
                     % ", ".join(map_groups))
    copies = []
    for loc in (locales if locales is not None else ctx.client.secondary):
        for st in all_steps:
            if st.locale in ("full", "variant"):
                copies.append(S.locale_copy(st, loc, ctx.locale_folders(loc)))
    return all_steps + copies, maps_note


def _matches(st, w):
    if w == "all" or w == st.name or w == st.base_name or w == st.group:
        return True
    if w == "default":
        return st.default
    if w in ("maps", "world") and st.group.startswith("maps:"):
        return True
    if w in S.MAP_GROUPS and st.group == "maps:" + w:
        return True
    if w == "locales":
        return bool(st.base)
    if w.startswith("@"):                  # @xxYY: every step of one secondary locale
        return st.name.endswith(w)
    if any(ch in w for ch in "*?["):
        return fnmatch.fnmatch(st.name, w)
    return False


def select(all_steps, spec, skip):
    """--steps: names, groups ('tables', 'interface', 'models', 'audio', 'world', 'maps', a map group,
    'locales', '@xxYY'), 'default' or 'all'. A step name also selects its locale copies."""
    want = [w.strip() for w in (spec or "default").split(",") if w.strip()]
    return [st for st in all_steps
            if any(_matches(st, w) for w in want) and st.name not in skip and st.base_name not in skip]


def map_groups_wanted(spec):
    want = [w.strip() for w in (spec or "default").split(",") if w.strip()]
    groups = set()
    for w in want:
        if w in ("default", "maps", "world"):
            groups |= set(S.DEFAULT_MAP_GROUPS)
        elif w == "all":
            groups |= set(S.MAP_GROUPS)
        elif w in S.MAP_GROUPS:
            groups.add(w)
        elif w.startswith("maps:") and w[5:] in S.MAP_GROUPS:
            groups.add(w[5:])
        elif w.startswith(("terrain_", "doodads_", "minimap_")) or any(ch in w for ch in "*?["):
            groups |= set(S.MAP_GROUPS)       # a single map by name: resolve every group
    return sorted(groups)


# ------------------------------------------------------------------------------------------------
def run_cmd(ctx, argv, log_path, cwd, stdout_path=None):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    os.makedirs(cwd, exist_ok=True)
    with open(log_path, "a", encoding="utf-8", errors="replace") as log:
        log.write("\n$ %s\n" % subprocess.list2cmdline(argv))
        log.flush()
        t0 = time.time()
        try:
            if stdout_path:
                with open(stdout_path, "w", encoding="utf-8", errors="replace") as so:
                    r = subprocess.run(argv, cwd=cwd, env=ctx.env(argv), stdout=so, stderr=log,
                                       stdin=subprocess.DEVNULL)
            else:
                r = subprocess.run(argv, cwd=cwd, env=ctx.env(argv), stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL)
            rc = r.returncode
        except OSError as e:
            log.write("cannot start: %s\n" % e)
            rc = 127
        log.write("# rc=%d  %.1f s\n" % (rc, time.time() - t0))
    return rc


EXTRACT_RECORD = "extracted_prefixes.json"   # in a tree's list folder: the prefixes extracted completely


def archive_signature(data_dir, loc):
    """Name, size and modification time of every archive the extractor reads for a locale."""
    sig = []
    for d in (data_dir, os.path.join(data_dir, loc)):
        try:
            for e in sorted(os.scandir(d), key=lambda e: e.name.lower()):
                if e.is_file() and e.name.lower().endswith(".mpq"):
                    st = e.stat()
                    sig.append([os.path.relpath(e.path, data_dir).replace(os.sep, "/"), st.st_size, st.st_mtime_ns])
        except OSError:
            pass
    return sig


class ExtractRecord:
    """Which client path prefixes a work tree already holds completely (the extractor returned 0 for them
    from the same archives). A re-run or --update then skips the extractor for them: it would read every
    archive's file list only to keep every file (minutes on a busy disk). --force ignores the record;
    deleting the work folder resets it."""

    def __init__(self, list_dir, target, data_dir, loc):
        self.path = os.path.join(list_dir, EXTRACT_RECORD)
        self.target = os.path.normcase(os.path.abspath(target))
        self.sig = archive_signature(data_dir, loc)
        self.done = set()
        try:
            with open(self.path, encoding="utf-8") as f:
                doc = json.load(f)
            if doc.get("target") == self.target and doc.get("archives") == self.sig:
                self.done = set(doc.get("prefixes", []))
        except (OSError, ValueError):
            pass

    def covers(self, prefix):
        """Extracted before (or a wider prefix was), and its first folder is still there."""
        if not any(prefix.startswith(p) for p in self.done):
            return False
        top = prefix.split("\\", 1)[0]
        return os.path.isdir(os.path.join(self.target, top))

    def add(self, prefixes):
        self.done |= set(prefixes)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"target": self.target, "archives": self.sig, "prefixes": sorted(self.done)}, f, indent=1)
        os.replace(tmp, self.path)


def extract(ctx, prefixes, names_file=None, target=None, locale=None, dry=False, log_name="extract.log"):
    """Run the extractor over the user's archives (read-only) into a work tree. An extra locale that comes
    from an added client (--add-locale) is extracted from that client's own archives."""
    locale = locale or ctx.client.locale
    argv = ctx.program("@exe:psp_mpq_extract") + ["--data", ctx.client.data_dir_for(locale), "--out",
                                                  target or ctx.x, "--locale", locale]
    for p in sorted(set(prefixes)):
        argv += ["--prefix", p]
    if names_file:
        argv += ["--names", names_file]
    if dry:
        argv.append("--dry-run")
    return run_cmd(ctx, argv, os.path.join(ctx.logs, log_name), ctx.work)


def run_probe(ctx, step, probe):
    lists = ctx.folder(step, "L")
    list_file = probe.list_file.format(L=lists, W=ctx.folder(step, "W"))
    os.makedirs(lists, exist_ok=True)
    log = os.path.join(ctx.logs, step.name + ".log")
    make_dirs(ctx, step)
    rc = 0
    raw = list_file + ".raw" if probe.stdout else None
    if probe.builtin == "wdt":
        mapdbc = os.path.join(ctx.folder(step, "X"), "dbfilesclient", "map.dbc")
        with open(list_file, "w", encoding="utf-8") as f:
            for n in (map_wdt_names(mapdbc) if os.path.exists(mapdbc) else []):
                f.write(n + "\n")
    else:
        rc = run_cmd(ctx, ctx.expand(probe.cmd, step), log, ctx.folder(step, "W"), stdout_path=raw)
    if probe.stdout:
        lines = []
        if os.path.exists(raw):
            for ln in open(raw, encoding="utf-8", errors="replace"):
                ln = ln.strip()
                if probe.keep:
                    if ln.startswith(probe.keep):
                        lines.append(ln[len(probe.keep):].strip())
                elif ln and not ln.startswith("-") and ("/" in ln or "\\" in ln) and " " not in ln:
                    lines.append(ln)
        with open(list_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
    if not os.path.exists(list_file):
        return rc, 0
    # The extractor looks names up by archive hash: the archives' own form uses backslashes.
    with open(list_file, encoding="utf-8", errors="replace") as f:
        names = [ln.strip().replace("/", "\\") for ln in f if ln.strip()]
    with open(list_file, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(n + "\n" for n in names))
    n = len(names)
    if n:
        target = ctx.folder(step, "XG" if probe.target == "XG" else "X")
        os.makedirs(target, exist_ok=True)
        erc = extract(ctx, [], names_file=list_file, target=target, locale=ctx.step_locale(step))
        ctx.say("    %s: %d paths not named by the archives' listfiles -> extractor (rc %d, logs/extract.log)"
                % (step.name, n, erc))
    return rc, n


def make_dirs(ctx, step):
    vals = {"X": ctx.folder(step, "X"), "XG": ctx.folder(step, "XG"), "D": ctx.folder(step, "D"),
            "W": ctx.folder(step, "W"), "L": ctx.folder(step, "L")}
    for d in step.mkdirs:
        os.makedirs(os.path.normpath(d.format(**vals)), exist_ok=True)


def builtin_profile(ctx, step, log_path):
    """Copy the port's protocol profile whose expansion.json names the identified build."""
    src_root = os.path.join(ctx.bakers, "Data", "expansions")
    with open(log_path, "a", encoding="utf-8") as log:
        if not os.path.isdir(src_root):
            log.write("no profiles in %s\n" % src_root)
            return 1
        for d in sorted(os.listdir(src_root)):
            meta = os.path.join(src_root, d, "expansion.json")
            try:
                with open(meta, encoding="utf-8") as f:
                    build = json.load(f).get("build")
            except (OSError, ValueError):
                continue
            if build == ctx.client.build:
                dst = os.path.join(ctx.data, "expansions", d)
                os.makedirs(dst, exist_ok=True)
                for fn in sorted(os.listdir(os.path.join(src_root, d))):
                    if fn.endswith(".json"):
                        shutil.copy2(os.path.join(src_root, d, fn), os.path.join(dst, fn))
                log.write("profile %s (build %s) -> %s\n" % (d, build, dst))
                return 0
        log.write("no profile for build %s\n" % ctx.client.build)
        return 1


def builtin_xmb(ctx, step, log_path):
    out_dir = os.path.join(ctx.out, "xmb")
    with open(log_path, "a", encoding="utf-8") as log:
        try:
            icon0, pic1 = xmb.make_art(ctx.folder(step, "X"), out_dir, log=lambda m: log.write(m + "\n"))
            log.write("ICON0 %s, PIC1 %s\n" % (xmb.png_size(icon0), xmb.png_size(pic1)))
            eboot = getattr(ctx.a, "eboot", None)
            if eboot:
                xmb.repack_eboot(eboot, os.path.join(out_dir, "EBOOT.PBP"), icon0, pic1)
                log.write("EBOOT.PBP repacked with this art -> %s\n" % os.path.join(out_dir, "EBOOT.PBP"))
            return 0
        except (RuntimeError, OSError, ValueError) as e:
            log.write("xmb art failed: %s\n" % e)
            return 1


BUILTINS = {"profile": builtin_profile, "xmb_art": builtin_xmb}


# -- checks of a finished step's output that the baker's own verify does not make -------------------------------
def pak_entry(path, name):
    """The bytes of one entry of a WPAK file (include/platform/psp/psp_pak.hpp), or None."""
    with open(path, "rb") as f:
        head = f.read(16)
        if head[:4] != b"WPAK":
            return None
        count, toc = struct.unpack_from("<II", head, 8)
        f.seek(toc)
        raw = f.read(count * 64)
        for i in range(count):
            n, off, size, _kind, _r = struct.unpack_from("<48sIIII", raw, i * 64)
            if n.split(b"\0", 1)[0].decode("latin-1") == name:
                f.seek(off)
                return f.read(size)
    return None


def dbc_text_slot(tree):
    """The text slot of the client's localized table strings (0 enUS ... 8 ruRU): the slot of Map.dbc's map
    names that holds text. None when the table is not there."""
    p = os.path.join(tree, "dbfilesclient", "map.dbc")
    if not os.path.exists(p):
        return None
    recs, _f, string = mapdb.read_dbc(p)
    filled = [sum(1 for r in recs if string(struct.unpack_from("<I", r, (5 + k) * 4)[0])) for k in range(16)]
    return max(range(16), key=lambda k: filled[k]) if any(filled) else None


def check_glue_zones(ctx, step, outs):
    """glue.pak's "zones" (the character list's location line): no name at all means the baker read the area
    names from another locale's text slot (it reads a fixed one) - the pak is usable, the names are missing."""
    for rel in outs:
        if not rel.replace(os.sep, "/").endswith("glue.pak"):
            continue
        z = pak_entry(os.path.join(ctx.out, rel), "zones")
        if z is not None and len(z) >= 4 and struct.unpack_from("<I", z, 0)[0] == 0:
            slot = dbc_text_slot(ctx.folder(step, "X"))
            return ("glue.pak has no zone names: the baker takes the area names from a fixed locale's text slot, "
                    "this client keeps them in slot %s - a baker problem of the port (the character list shows "
                    "no location)" % ("?" if slot is None else slot))
    return ""


POST_CHECKS = {"glue": check_glue_zones}


def post_check(ctx, step, outs):
    fn = POST_CHECKS.get(step.base_name)
    if not fn:
        return ""
    try:
        return fn(ctx, step, outs)
    except (OSError, ValueError, struct.error):
        return ""


def run_step(ctx, man, step, idx, total, must_run=False):
    key = ctx.step_key(step)
    ident = ctx.step_identity(step)
    log = os.path.join(ctx.logs, step.name + ".log")
    if not ctx.a.force and not must_run and man.up_to_date(step.name, key, ident):
        ctx.say("[%3d/%d] %-34s up to date (skipped)" % (idx, total, step.name))
        ctx.event("step", name=step.name, state="skipped", index=idx, total=total, locale=ctx.step_locale(step))
        return "skipped"
    with open(log, "w", encoding="utf-8") as f:
        f.write("kiln %s step %s: %s\n" % (KILN_VERSION, step.name, step.title))
    vals = {k: v for k, v in step.params.items() if isinstance(v, str) and k not in PATH_PARAMS}
    for pat in step.clean:
        for f in glob.glob(os.path.join(ctx.out, pat.format(**vals))):
            os.remove(f)
    ctx.say("[%3d/%d] %-34s running" % (idx, total, step.name))
    ctx.event("step", name=step.name, state="start", index=idx, total=total, title=step.title,
              log=os.path.relpath(log, ctx.out), locale=ctx.step_locale(step))
    t0 = time.time()
    ctx.running[step.name] = t0
    ctx.running_locale[step.name] = ctx.step_locale(step)
    rc = 0
    quality_rc = 0  # a warn_rc code a run command returned
    cwd = ctx.folder(step, "W")
    make_dirs(ctx, step)
    try:
        if step.builtin:
            rc = BUILTINS[step.builtin](ctx, step, log)
        else:
            for cmd in step.run:
                rc = run_cmd(ctx, ctx.expand(cmd, step), log, cwd)
                if rc and rc in step.warn_rc:
                    quality_rc = rc  # written and complete; quality warnings in the step log
                    rc = 0
                    continue
                if rc:
                    break
        vrc = 0
        if rc == 0:
            for cmd in step.verify:
                vrc = run_cmd(ctx, ctx.expand(cmd, step), log, cwd)
                if vrc:
                    break
    finally:
        ctx.running.pop(step.name, None)
        ctx.running_locale.pop(step.name, None)
    outs = ctx.outputs_glob(step)
    status = "ok" if rc == 0 and vrc == 0 and outs else "failed"
    note = "" if status == "ok" else ("rc=%d verify=%d outputs=%d" % (rc, vrc, len(outs)))
    secs = time.time() - t0
    man.record(step.name, status=status, key=key, outputs=outs if status == "ok" else [], rc=rc or vrc,
               seconds=secs, note=note, identity=ident)
    warn = post_check(ctx, step, outs) if status == "ok" else ""
    if status == "ok" and quality_rc:
        q = "quality warnings (rc %d): see logs/%s.log - the output is complete" % (quality_rc, step.name)
        warn = "%s; %s" % (warn, q) if warn else q
    if warn:
        man.mark(step.name, warnings=[warn])
        ctx.say("warning: %s [%s]: %s" % (step.name, ctx.step_locale(step), warn))
        ctx.event("warning", step=step.name, locale=ctx.step_locale(step), message=warn)
    man.save()
    size = sum(os.path.getsize(os.path.join(ctx.out, o)) for o in outs)
    ctx.say("[%3d/%d] %-34s %s  %.1f s  %d file(s) %.1f MB%s" % (
        idx, total, step.name, status.upper(), secs, len(outs), size / 1048576.0,
        "" if status == "ok" else "  (see logs/%s.log)" % step.name))
    ctx.event("step", name=step.name, state=status, index=idx, total=total, seconds=round(secs, 1),
              files=len(outs), bytes=size, note=note, locale=ctx.step_locale(step))
    return status


class Heartbeat(threading.Thread):
    """While steps run: a 'running' event every 10 s (--json) or a text line every 60 s."""

    def __init__(self, ctx):
        super().__init__(daemon=True)
        self.ctx = ctx
        self.stop = threading.Event()

    def run(self):
        period = 10 if self.ctx.json else 60
        while not self.stop.wait(period):
            now = time.time()
            running = {n: round(now - t0) for n, t0 in list(self.ctx.running.items())}
            if not running:
                continue
            if self.ctx.json:
                locs = {n: self.ctx.running_locale.get(n, "") for n in running}
                self.ctx.event("running", steps=running, locales=locs)
            else:
                self.ctx.say("        ... running: %s" % ", ".join("%s %d s" % kv for kv in sorted(running.items())),
                             log=False)


def verify_existing(ctx, man, chosen):
    by_name = {s.name: s for s in chosen}
    bad = man.check_files(set(by_name))
    for name, path, problem in bad:
        ctx.say("  %-24s %-50s %s" % (name, path, problem))
        ctx.event("verify", step=name, path=path, problem=problem, locale=ctx.step_locale(by_name[name]))
    vfail = 0
    for st in chosen:
        if not st.verify:
            continue
        rec = man.step(st.name)
        if not rec or rec.get("status") != "ok":
            continue
        log = os.path.join(ctx.logs, st.name + ".verify.log")
        for cmd in st.verify:
            if run_cmd(ctx, ctx.expand(cmd, st), log, ctx.folder(st, "W")):
                vfail += 1
                ctx.say("  %-24s verify command failed (logs/%s.verify.log)" % (st.name, st.name))
                ctx.event("verify", step=st.name, problem="verify command failed", locale=ctx.step_locale(st))
                break
    return len(bad), vfail


class FailGate:
    """A failed step must not start the rest blindly:
    * a program that could not start (missing DLL, wrong architecture: winrt.START_FAILURES, or Kiln's 127)
      stops the queue - every later step would fail the same way;
    * the other locales' copies of a failed step are skipped (the same command on another tree);
    * a program that failed in 3 steps is not started again in this run.
    Skipped steps are reported ('blocked') and count as failed."""

    PROGRAM_LIMIT = 3

    def __init__(self):
        self.lock = threading.Lock()
        self.stop = ""
        self.failed_base = {}
        self.program_fails = {}

    def blocked(self, step):
        with self.lock:
            if self.stop:
                return "queue stopped: " + self.stop
            if step.base_name in self.failed_base:
                return "%s failed" % self.failed_base[step.base_name]
            for t in step.programs():
                if self.program_fails.get(t, 0) >= self.PROGRAM_LIMIT:
                    return "%s failed in %d steps" % (t.split(":", 1)[-1], self.program_fails[t])
        return ""

    def failed(self, step, rc):
        with self.lock:
            if winrt.is_start_failure(rc):
                self.stop = "%s could not start a program (%s)" % (step.name, winrt.rc_text(rc))
            self.failed_base.setdefault(step.base_name, step.name)
            for t in step.programs():
                self.program_fails[t] = self.program_fails.get(t, 0) + 1


def run_gated(ctx, man, gate, step, idx, total, must_run=False):
    """run_step unless the gate blocks the step; tells the gate about a failure."""
    why = gate.blocked(step)
    if why:
        ctx.say("[%3d/%d] %-34s BLOCKED  %s" % (idx, total, step.name, why))
        ctx.event("step", name=step.name, state="blocked", index=idx, total=total, note=why,
                  locale=ctx.step_locale(step))
        return "blocked"
    status = run_step(ctx, man, step, idx, total, must_run)
    if status == "failed":
        gate.failed(step, (man.step(step.name) or {}).get("rc", 1))
        if gate.stop:
            ctx.say("        %s - the remaining steps are not started" % gate.stop)
    return status
