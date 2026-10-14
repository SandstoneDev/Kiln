"""data/kiln.manifest.json: what Kiln wrote, so a re-run can skip finished steps and a verify pass
can find damaged or missing files.

Per step: status, the step's definition hash (a changed command line re-runs the step), timing,
return codes, its identity (sha256 of the converters / bakers that made it and the input: the client
executable's sha256, the locale, the build; see pipeline.Ctx.step_identity) and every output file (path
relative to the output folder, size, sha256, mtime). File hashes are SHA-256 (hashlib/OpenSSL: far
faster than the disks it reads from; no extra dependency). A step recorded by an older Kiln has no
identity: --check reports it as unknown and --update re-makes it.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

FILE_NAME = "kiln.manifest.json"
SCHEMA = 1          # new fields are added compatibly (a reader ignores what it does not know)
HASH = "sha256"


def sha256_file(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class Manifest:
    def __init__(self, out_dir, data_dir):
        self.out_dir = out_dir
        self.path = os.path.join(data_dir, FILE_NAME)
        self.lock = threading.Lock()
        self.doc = {"schema": SCHEMA, "tool": "kiln", "tool_version": "", "client": {}, "steps": {}, "hash": HASH}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    doc = json.load(f)
                if doc.get("schema") == SCHEMA:
                    self.doc = doc
                    self.doc.setdefault("hash", HASH)
            except (OSError, ValueError):
                pass                        # a damaged manifest = no manifest: every step re-runs

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(self.doc, f, indent=1, sort_keys=True)
            os.replace(tmp, self.path)

    def step(self, name):
        return self.doc["steps"].get(name)

    def file_record(self, rel):
        full = os.path.join(self.out_dir, rel)
        st = os.stat(full)
        return {"path": rel.replace(os.sep, "/"), "size": st.st_size, "sha256": sha256_file(full),
                "mtime_ns": st.st_mtime_ns}

    def record(self, name, *, status, key, outputs, rc, seconds, note="", identity=None):
        files = [self.file_record(r) for r in sorted(outputs)]
        with self.lock:
            self.doc["steps"][name] = {
                "status": status, "key": key, "rc": rc, "seconds": round(seconds, 1),
                "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "outputs": files, "note": note,
            }
            if identity is not None:
                self.doc["steps"][name]["identity"] = identity

    def mark(self, name, **fields):
        with self.lock:
            self.doc["steps"].setdefault(name, {}).update(fields)

    def up_to_date(self, name, key, identity=None):
        """True when the step finished OK with the same definition (and, when both are known, the same
        identity) and every recorded output is present with the recorded size and hash (the hash is only
        recomputed when the size or the modification time changed)."""
        st = self.step(name)
        if not st or st.get("status") != "ok" or st.get("key") != key or not st.get("outputs"):
            return False
        if identity is not None and "identity" in st and st["identity"] != identity:
            return False
        for o in st["outputs"]:
            full = os.path.join(self.out_dir, o["path"])
            try:
                s = os.stat(full)
            except OSError:
                return False
            if s.st_size != o["size"]:
                return False
            if s.st_mtime_ns != o.get("mtime_ns") and sha256_file(full) != o["sha256"]:
                return False
        return True

    def check_files(self, names=None):
        """Full integrity pass. -> list of (step, path, problem)."""
        bad = []
        for name, st in sorted(self.doc["steps"].items()):
            if names is not None and name not in names:
                continue
            for o in st.get("outputs", []):
                full = os.path.join(self.out_dir, o["path"])
                if not os.path.exists(full):
                    bad.append((name, o["path"], "missing"))
                elif os.path.getsize(full) != o["size"]:
                    bad.append((name, o["path"], "size changed"))
                elif sha256_file(full) != o["sha256"]:
                    bad.append((name, o["path"], "hash changed"))
        return bad
