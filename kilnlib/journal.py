"""One append-only journal; each record keeps its run, operation and stream context."""
from __future__ import annotations

import codecs
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
import threading
import uuid

_locks = {}
_locks_guard = threading.Lock()
MAX_RECORD = 64 * 1024


class Journal:
    def __init__(self, path, enabled=True):
        self.path = path
        self.run_id = uuid.uuid4().hex[:12]
        self.enabled = enabled
        with _locks_guard:
            self.lock = _locks.setdefault(os.path.normcase(os.path.abspath(path)), threading.Lock())

    def write(self, message, step="run", stream="kiln"):
        if not self.enabled:
            return
        # Escape context separators; child text itself remains readable and unchanged.
        step = str(step).replace("\n", "\\n").replace("\r", "\\r").replace("]", "\\]")
        lines = str(message).splitlines() or [""]
        with self.lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8", errors="replace", newline="\n") as f:
                for line in lines:
                    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    f.write("%s [run=%s step=%s stream=%s] %s\n" % (stamp, self.run_id, step, stream, line))

    @contextmanager
    def stream(self, step, stream="builtin"):
        journal = self

        class Writer:
            def write(self, value):
                journal.write(value.rstrip("\r\n"), step, stream)

        yield Writer()


def progress_snapshot(line):
    """Accept bounded finite snapshots; arbitrary converter text stays journal text."""
    if not line.startswith("KILN_PROGRESS ") or len(line) > MAX_RECORD:
        return None
    try:
        doc = json.loads(line[len("KILN_PROGRESS "):])
        if not isinstance(doc, dict) or not isinstance(doc.get("phase"), str):
            return None
        for name in ("completed", "total", "elapsed_s"):
            value = doc.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                return None
        if any(type(doc[name]) is not int for name in ("completed", "total")):
            return None
        if "failed" in doc and (type(doc["failed"]) is not int or not 0 <= doc["failed"] <= doc["completed"]):
            return None
        if doc["completed"] > doc["total"] or not isinstance(doc.get("active"), list):
            return None
        for item in doc["active"]:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("model", "stage")):
                return None
            for name in ("model_id", "work_done", "work_total", "stage_elapsed_s"):
                value = item.get(name)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    return None
            if any(type(item[name]) is not int for name in ("model_id", "work_done", "work_total")):
                return None
            if item["work_total"] and item["work_done"] > item["work_total"]:
                return None
        # Only protocol fields are forwarded; a child cannot forge event/step envelope fields.
        names = ("phase", "completed", "total", "elapsed_s", "active") + (("failed",) if "failed" in doc else ())
        return {name: doc[name] for name in names}
    except (ValueError, TypeError, OverflowError, RecursionError):
        return None


def drain_stream(pipe, consume, raw=None):
    """Drain binary pipes independently, including partial UTF-8 and unbounded lines.

    A long non-newline stream is split into bounded journal records. Reader failures
    must be handled by the caller while continuing to drain, so children cannot
    deadlock with an abandoned stdout/stderr pipe.
    """
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pending = ""
    while True:
        block = pipe.read1(MAX_RECORD)
        if not block:
            pending += decoder.decode(b"", final=True)
            if pending:
                consume(pending.rstrip("\r"))
            return
        if raw is not None:
            raw.write(block)
        pending += decoder.decode(block)
        while "\n" in pending or len(pending) >= MAX_RECORD:
            newline = pending.find("\n")
            count = newline if 0 <= newline < MAX_RECORD else MAX_RECORD
            consume(pending[:count].rstrip("\r"))
            pending = pending[count + (1 if count == newline else 0):]
