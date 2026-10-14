"""--check, --update, --repair and --target: is an output folder (and a copy of it, e.g. on the memory
stick) still what the manifest says, and what has to be re-made or re-copied.

Problems (exit code 1):
  missing / size / hash   an output file of the manifest is not there, or not the recorded size or SHA-256
  failed                  the step did not finish OK the last time
  unknown                 the step was recorded by an older Kiln without its identity (--update re-makes it)
  outdated                the converters / bakers or the input (client executable, locale) differ from the
                          recorded identity (--update re-makes it)
  locales                 data/locales.txt missing, unreadable or not what the manifest records
  fat32                   a file of 4 GB or more (does not fit a FAT32 memory stick)
  verify                  a converter's own verify pass failed (--deep)
Warnings (reported, exit code unaffected):
  extra                   a file under data/ (or xmb/) that no step of the manifest made; never deleted
  copy                    (target) a file is not copied because of an earlier problem
"""
from __future__ import annotations

import json
import os
import shutil

from . import locales as L
from .manifest import sha256_file

KILN_FILES = ("data/kiln.manifest.json", "data/locales.txt")
COPY_RECORD = "data/kiln.copy.json"          # on the target: what Kiln copied there (size, mtime, sha256)
NOT_PROVIDED = ("data/movies/", "data/music/original/")   # the player's own additions: never "extra"
FAT32_LIMIT = 4 << 30
PROBLEMS = ("missing", "size", "hash", "failed", "unknown", "outdated", "locales", "fat32", "verify", "copy-failed")


def norm(rel):
    return rel.replace("\\", "/")


def key(rel):
    return os.path.normcase(norm(rel))


class Report:
    """Collects check lines; prints them (text) or emits them as 'check' events (--json)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.items = []

    def add(self, where, kind, path="", step="", locale="", detail=""):
        item = {"where": where, "kind": kind, "path": path, "step": step, "locale": locale, "detail": detail}
        self.items.append(item)
        self.ctx.event("check", where=where, problem=kind, path=path, step=step, locale=locale, detail=detail)
        if not self.ctx.json:
            self.ctx.say("  %-6s %-9s %-52s %s%s" % (where, kind, path or step, ("[%s] " % step) if path and step
                                                     else "", detail), log=True)
        return item

    def problems(self, where=None):
        return [i for i in self.items if i["kind"] in PROBLEMS and (where is None or i["where"] == where)]

    def count(self):
        by = {}
        for i in self.items:
            by[i["kind"]] = by.get(i["kind"], 0) + 1
        return by


# -- the output folder ---------------------------------------------------------------------------------------------
def recorded_files(man):
    """{key: (rel, size, sha256, step)} of every output of every step that finished OK."""
    out = {}
    for name, st in man.doc.get("steps", {}).items():
        if st.get("status") != "ok":
            continue
        for o in st.get("outputs", []):
            out[key(o["path"])] = (norm(o["path"]), o["size"], o["sha256"], name)
    return out


def step_locale(st):
    return (st.get("identity") or {}).get("input", {}).get("locale", "")


def check_out(ctx, man, rep, full=True):
    """Every recorded output against its size and SHA-256 (full=False: the hash only when the modification
    time changed, as a run does), the steps' status and identity, data/locales.txt, files nobody made."""
    files = recorded_files(man)
    nbytes = 0
    for name, st in sorted(man.doc.get("steps", {}).items()):
        loc = step_locale(st)
        if st.get("status") != "ok":
            rep.add("out", "failed", step=name, locale=loc, detail=st.get("note", ""))
            continue
        ident = st.get("identity")
        if ident is None:
            rep.add("out", "unknown", step=name, detail="recorded by an older Kiln (no converter/input identity)")
        else:
            now = ctx.identity_now(ident)
            diff = identity_diff(ident, now)
            if diff:
                rep.add("out", "outdated", step=name, locale=loc, detail=diff)
        for o in st.get("outputs", []):
            rel = norm(o["path"])
            full_path = os.path.join(ctx.out, rel)
            try:
                s = os.stat(full_path)
            except OSError:
                rep.add("out", "missing", rel, name, loc)
                continue
            nbytes += s.st_size
            if s.st_size != o["size"]:
                rep.add("out", "size", rel, name, loc, "%d bytes, recorded %d" % (s.st_size, o["size"]))
            elif (full or s.st_mtime_ns != o.get("mtime_ns")) and sha256_file(full_path) != o["sha256"]:
                rep.add("out", "hash", rel, name, loc, "SHA-256 differs from the manifest")
            if s.st_size >= FAT32_LIMIT:
                rep.add("out", "fat32", rel, name, loc, "%d bytes: does not fit a FAT32 memory stick" % s.st_size)
    check_locales(ctx, man, rep)
    known = set(files) | {key(k) for k in KILN_FILES}
    for rel in walk(ctx.out, ("data", "xmb")):
        if key(rel) not in known and not rel.startswith(NOT_PROVIDED):
            rep.add("out", "extra", rel, detail="not made by any step of the manifest (kept)")
    return len(files), nbytes


def identity_diff(old, new):
    parts = []
    for k in sorted(set(old) | set(new)):
        if k == "programs":
            for t in sorted(set(old.get(k, {})) | set(new.get(k, {}))):
                if old.get(k, {}).get(t) != new.get(k, {}).get(t):
                    parts.append("%s changed" % t.split(":", 1)[-1])
        elif k == "input":
            for f in sorted(set(old.get(k, {})) | set(new.get(k, {}))):
                if old.get(k, {}).get(f) != new.get(k, {}).get(f):
                    parts.append("input %s %s -> %s" % (f, old.get(k, {}).get(f), new.get(k, {}).get(f)))
        elif old.get(k) != new.get(k):
            parts.append("%s %s -> %s" % (k, old.get(k), new.get(k)))
    return "; ".join(parts)


def check_locales(ctx, man, rep):
    want = man.doc.get("locales", {})
    try:
        doc = L.read(ctx.data)
    except (OSError, L.LocalesError) as e:
        rep.add("out", "locales", "data/" + L.FILE_NAME, detail="missing or unreadable (%s)" % e)
        return
    if want.get("primary") and (doc["primary"], doc["extra"], doc["default"]) != (
            want.get("primary"), want.get("extra", []), want.get("default", want.get("primary"))):
        rep.add("out", "locales", "data/" + L.FILE_NAME, detail="primary %s extra %s default %s; manifest: %s %s %s" % (
            doc["primary"], doc["extra"], doc["default"], want.get("primary"), want.get("extra"), want.get("default")))
    for x in doc["extra"]:
        if not os.path.isdir(os.path.join(ctx.data, "locale", x)):
            rep.add("out", "locales", "data/locale/%s/" % x, detail="listed as extra, folder missing")


def walk(root, tops):
    for top in tops:
        base = os.path.join(root, top)
        for dirpath, _dirs, fns in os.walk(base):
            for fn in fns:
                yield norm(os.path.relpath(os.path.join(dirpath, fn), root))


# -- the copy (memory stick) ---------------------------------------------------------------------------------------
def target_files(ctx, man):
    """What belongs on the target: every recorded output under data/ + data/locales.txt. -> {key: (rel, size, sha)}"""
    out = {k: v[:3] for k, v in recorded_files(man).items() if v[0].startswith("data/")}
    lt = os.path.join(ctx.data, L.FILE_NAME)
    if os.path.exists(lt):
        out[key("data/" + L.FILE_NAME)] = ("data/" + L.FILE_NAME, os.path.getsize(lt), sha256_file(lt))
    return out


def target_root(target):
    """The game folder on the stick (ms0:/PSP/GAME/<APP>/): data/ goes inside it."""
    t = os.path.abspath(target)
    return os.path.dirname(t) if os.path.basename(t.rstrip("\\/")).lower() == "data" else t


def load_copy_record(root):
    try:
        with open(os.path.join(root, COPY_RECORD), encoding="utf-8") as f:
            return json.load(f).get("files", {})
    except (OSError, ValueError):
        return {}


def save_copy_record(root, files):
    path = os.path.join(root, COPY_RECORD)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"tool": "kiln", "files": files}, f, indent=0, sort_keys=True)
    os.replace(tmp, path)


def same_on_target(root, rel, size, sha, record, full):
    """Is the target's copy the file of the manifest? Size first; then (unless full) the copy record's size,
    modification time and hash, else the SHA-256 of the target file. -> (same, problem kind or "")"""
    p = os.path.join(root, rel)
    try:
        s = os.stat(p)
    except OSError:
        return False, "missing"
    if s.st_size != size:
        return False, "size"
    r = record.get(rel)
    if not full and r and r.get("sha256") == sha and r.get("size") == size and r.get("mtime_ns") == s.st_mtime_ns:
        return True, ""
    return (True, "") if sha256_file(p) == sha else (False, "hash")


def check_target(ctx, man, target, rep, full=True):
    root = target_root(target)
    want = target_files(ctx, man)
    record = load_copy_record(root)
    for k, (rel, size, sha) in sorted(want.items()):
        same, kind = same_on_target(root, rel, size, sha, record, full)
        if not same:
            rep.add("target", kind, rel, detail=os.path.join(root, rel))
    known = set(want) | {key(COPY_RECORD)}
    for rel in walk(root, ("data",)):
        if key(rel) not in known and not rel.startswith(NOT_PROVIDED):
            rep.add("target", "extra", rel, detail="not in the manifest (kept)")
    return root


def sync_target(ctx, man, target, rep, full=False, only=None):
    """Copy what is missing or different on the target (never deletes). only: keys to look at (None = all).
    -> (copied, kept, failed, bytes copied)"""
    root = target_root(target)
    want = target_files(ctx, man)
    record = load_copy_record(root)
    copied = kept = failed = nbytes = 0
    for k, (rel, size, sha) in sorted(want.items()):
        if only is not None and k not in only:
            continue
        if size >= FAT32_LIMIT:
            rep.add("target", "fat32", rel, detail="%d bytes: not copied (FAT32 holds files under 4 GB)" % size)
            failed += 1
            continue
        same, _kind = same_on_target(root, rel, size, sha, record, full)
        dst = os.path.join(root, rel)
        if same:
            kept += 1
            st = os.stat(dst)
            record[rel] = {"size": size, "sha256": sha, "mtime_ns": st.st_mtime_ns}
            continue
        src = os.path.join(ctx.out, rel)
        tmp = dst + ".kiln.tmp"
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, tmp)
            if os.path.getsize(tmp) != size:
                raise OSError("short copy")
            os.replace(tmp, dst)
            st = os.stat(dst)
            record[rel] = {"size": size, "sha256": sha, "mtime_ns": st.st_mtime_ns}
            copied += 1
            nbytes += size
            ctx.event("copy", path=rel, state="copied", bytes=size)
            if not ctx.json:
                ctx.say("  copied %-52s %.1f MB" % (rel, size / 1048576.0))
        except OSError as e:
            failed += 1
            rep.add("target", "copy-failed", rel, detail=str(e))
            try:
                os.remove(tmp)
            except OSError:
                pass
        if copied and copied % 25 == 0:
            save_copy_record(root, record)       # a long copy that is cut off keeps what it did
    save_copy_record(root, record)
    return copied, kept, failed, nbytes
