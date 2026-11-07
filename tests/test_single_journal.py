"""Real child streams must share one journal without contaminating GUI JSON."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
import re
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

import kiln
from kilnlib.journal import progress_snapshot
from kilnlib.manifest import Manifest
from kilnlib.pipeline import Ctx, run_cmd, run_step, run_probe, verify_existing
from kilnlib.steps import Step, Probe


def context(tmp_path):
    a = SimpleNamespace(out=str(tmp_path / "out"), work=None, tools=None, bakers=None,
                        python=sys.executable, dll_dir=None, jobs=2, json=True, force=False)
    cl = SimpleNamespace(locale="enUS", build=12340, sha256="synthetic", data_dir_for=lambda _loc: str(tmp_path / "client"))
    return Ctx(a, cl, str(tmp_path))


def test_parallel_streams_append_one_contextual_journal_and_forward_progress(tmp_path):
    events = io.StringIO()
    snapshot = dict(phase="models", completed=0, total=90, elapsed_s=2.5,
                    active=[dict(model_id=1, model="synthetic.m2", stage="verify", work_done=3,
                                 work_total=40, stage_elapsed_s=1.1)])
    script = tmp_path / "child.py"
    script.write_text("""import os, sys
os.write(1, b'partial ')
os.write(1, 'café'.encode('utf-8')[:4])
os.write(1, 'café'.encode('utf-8')[4:] + b'\\n')
os.write(2, b'KILN_PROGRESS ' + SNAPSHOT + b'\\n')
os.write(2, b'z' * 200000 + b'\\n')
os.write(1, b'no final newline')
sys.exit(7)
""".replace("SNAPSHOT", repr(json.dumps(snapshot).encode())), encoding="utf-8")
    first = context(tmp_path)
    with contextlib.redirect_stdout(events), ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: run_cmd(first, [sys.executable, str(script)],
                                                    str(Path(first.logs) / (name + ".log")),
                                                    first.work), ["creatures", "other"]))
    assert results == [7, 7]
    assert sorted(p.name for p in Path(first.logs).iterdir()) == ["kiln.log"]
    journal = (Path(first.logs) / "kiln.log").read_text(encoding="utf-8")
    assert "partial café" in journal and "no final newline" in journal
    assert "# rc=7" in journal and "KILN_PROGRESS " in journal
    assert "step=creatures" in journal and "step=other" in journal
    assert "run=" in journal and "Z [" in journal
    assert journal.count("z") == 400000
    assert all(re.match(r"^\d{4}-\d\d-\d\dT.*Z \[run=[0-9a-f]+ step=(creatures|other) stream=\w+\] ", line)
               for line in journal.splitlines())
    progress = [json.loads(ln) for ln in events.getvalue().splitlines()]
    assert {e["step"] for e in progress} == {"creatures", "other"}
    assert all(e["event"] == "step_progress" and e["active"] == snapshot["active"] for e in progress)
    # A second process invocation/context appends; it does not replace the first run.
    second = context(tmp_path)
    run_cmd(second, [sys.executable, "-c", "print('second run')"], str(Path(second.logs) / "probe.log"), second.work)
    appended = (Path(second.logs) / "kiln.log").read_text(encoding="utf-8")
    assert appended.startswith(journal) and "second run" in appended
    assert first.run_id != second.run_id


def test_previous_step_logs_remain_untouched(tmp_path):
    ctx = context(tmp_path)
    Path(ctx.logs).mkdir(parents=True)
    historical = Path(ctx.logs) / "creatures.log"
    historical.write_bytes(b"previous investigation")
    assert run_cmd(ctx, [sys.executable, "-c", "print('new attempt')"], str(historical), ctx.work) == 0
    assert historical.read_bytes() == b"previous investigation"
    assert sorted(p.name for p in Path(ctx.logs).iterdir()) == ["creatures.log", "kiln.log"]


def test_functional_stdout_is_kept_while_stderr_goes_to_journal(tmp_path):
    ctx = context(tmp_path)
    target = tmp_path / "names.raw"
    assert run_cmd(ctx, [sys.executable, "-c", "import sys; print('folder/model.m2'); print('probe detail', file=sys.stderr)"],
                   str(Path(ctx.logs) / "probe.log"), ctx.work, stdout_path=str(target)) == 0
    assert target.read_text() == "folder/model.m2\n"
    assert sorted(p.name for p in Path(ctx.logs).iterdir()) == ["kiln.log"]
    assert "probe detail" in (Path(ctx.logs) / "kiln.log").read_text()


def test_probe_extraction_verify_and_quality_warnings_share_the_journal(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    scripts = Path(ctx.bakers) / "tools/psp"
    scripts.mkdir(parents=True)
    Path(ctx.data).mkdir(parents=True)
    (scripts / "probe.py").write_text("import sys; print('need: folder/model.m2'); print('probe detail',file=sys.stderr)")
    (scripts / "bake.py").write_text("import sys; from pathlib import Path; Path(sys.argv[1]).write_bytes(b'fixture'); print('quality detail', file=sys.stderr); sys.exit(4)")
    (scripts / "verify.py").write_text("print('verify detail')")
    original_program = ctx.program
    monkeypatch.setattr(ctx, "program", lambda token: [sys.executable, "-c", "print('extract detail')"]
                        if token == "@exe:psp_mpq_extract" else original_program(token))
    st = Step("fixture", "models", "synthetic", prefixes=[], run=[["@py:bake.py", "{D}/fixture.pak"]],
              verify=[["@py:verify.py"]], outputs=["data/fixture.pak"], warn_rc=[4])
    probe = Probe(["@py:probe.py"], "{L}/fixture.txt", stdout=True, keep="need: ")
    events = io.StringIO()
    with contextlib.redirect_stdout(events):
        assert run_probe(ctx, st, probe) == (0, 1)
        man = Manifest(ctx.out, ctx.data)
        assert run_step(ctx, man, st, 1, 1) == "ok"
        assert verify_existing(ctx, man, [st]) == (0, 0)
    assert (Path(ctx.lists) / "fixture.txt").read_text() == "folder\\model.m2\n"
    assert sorted(p.name for p in Path(ctx.logs).iterdir()) == ["kiln.log"]
    journal = (Path(ctx.logs) / "kiln.log").read_text()
    for detail in ("probe detail", "extract detail", "verify detail", "quality detail", "quality warnings (rc 4)"):
        assert detail in journal


def test_interrupted_new_step_is_persistent_before_child_starts(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    Path(ctx.logs).mkdir(parents=True)
    man = Manifest(ctx.out, ctx.data)
    st = Step("creatures", "models", "synthetic", prefixes=[], run=[["@exe:synthetic"]], outputs=["data/creatures.pak"])

    def interrupt(*_args, **_kwargs):
        recorded = Manifest(ctx.out, ctx.data).step("creatures")
        assert recorded is not None and recorded["status"] == "running"
        raise KeyboardInterrupt

    monkeypatch.setattr("kilnlib.pipeline.run_cmd", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_step(ctx, man, st, 1, 1)
    interrupted = Manifest(ctx.out, ctx.data)
    assert interrupted.step("creatures")["status"] == "running"
    assert kiln.update_reason(ctx, interrupted, st) == "interrupted or not started last time"


def test_interrupted_rebuild_retains_previous_output_and_identity(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    Path(ctx.data).mkdir(parents=True)
    output = Path(ctx.data) / "creatures.pak"
    output.write_bytes(b"previous successful resource")
    man = Manifest(ctx.out, ctx.data)
    identity = {"input": {"locale": "enUS"}, "programs": {}}
    man.record("creatures", status="ok", key="old-key", identity=identity,
               outputs=["data/creatures.pak"], rc=0, seconds=2)
    previous = dict(man.step("creatures"))
    man.save()
    st = Step("creatures", "models", "synthetic", prefixes=[], run=[["@exe:synthetic"]], outputs=["data/creatures.pak"])

    def interrupt(*_args, **_kwargs): raise KeyboardInterrupt
    monkeypatch.setattr("kilnlib.pipeline.run_cmd", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_step(ctx, man, st, 1, 1, must_run=True)
    record = Manifest(ctx.out, ctx.data).step("creatures")
    assert record["status"] == "running"
    assert record["previous"] == previous
    assert record["outputs"] == previous["outputs"] and record["identity"] == identity
    assert output.read_bytes() == b"previous successful resource"
    assert not man.up_to_date("creatures", "old-key", identity)


def test_missing_recipe_reason_does_not_claim_a_locale_was_added(tmp_path):
    ctx = context(tmp_path)
    st = Step("creatures", "models", "synthetic", prefixes=[], run=[], outputs=[])
    assert kiln.update_reason(ctx, Manifest(ctx.out, ctx.data), st) == "not made yet"


def test_failed_count_is_forwarded_and_progress_counters_are_validated():
    value = dict(phase="models", completed=1, total=8, elapsed_s=3, active=[], failed=1)
    assert progress_snapshot("KILN_PROGRESS " + json.dumps(value))["failed"] == 1
    for patch in ({"completed": 1.5}, {"total": True}, {"failed": 2}, {"elapsed_s": float("nan")}):
        assert progress_snapshot("KILN_PROGRESS " + json.dumps(value | patch)) is None
    assert progress_snapshot("KILN_PROGRESS {unfinished") is None
    assert progress_snapshot("KILN_PROGRESS " + "[" * 20000 + "]" * 20000) is None
    assert progress_snapshot("KILN_PROGRESS " + json.dumps(value | {"active": [
        dict(model_id=1, model="fixture", stage="verify", work_done=3, work_total=2, stage_elapsed_s=1)]})) is None


def test_reader_start_failure_stops_child_and_reports_original_error(tmp_path, monkeypatch):
    import kilnlib.pipeline as pipeline
    ctx = context(tmp_path)
    original_start = threading.Thread.start
    original_popen = pipeline.subprocess.Popen
    processes = []
    starts = 0

    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    def start(thread):
        nonlocal starts
        starts += 1
        if starts == 2:
            raise RuntimeError("synthetic reader creation failure")
        return original_start(thread)

    monkeypatch.setattr(pipeline.subprocess, "Popen", popen)
    monkeypatch.setattr(threading.Thread, "start", start)
    rc = run_cmd(ctx, [sys.executable, "-c", "import time; time.sleep(0.3)"],
                 str(Path(ctx.logs) / "creatures.log"), ctx.work)
    assert rc == 127
    assert processes[0].poll() is not None and processes[0].returncode != 0
    assert "synthetic reader creation failure" in (Path(ctx.logs) / "kiln.log").read_text()


def test_functional_write_failure_keeps_draining_both_large_streams(tmp_path, monkeypatch):
    import builtins
    ctx = context(tmp_path)
    target = tmp_path / "names.raw"
    real_open = builtins.open

    class FullDisk:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def write(self, _block): raise OSError("synthetic output full")

    monkeypatch.setattr(builtins, "open", lambda path, *args, **kwargs:
                        FullDisk() if str(path) == str(target) else real_open(path, *args, **kwargs))
    events = io.StringIO()
    with contextlib.redirect_stdout(events):
        rc = run_cmd(ctx, [sys.executable, "-c", "import os; os.write(1,b'x'*1000000); os.write(2,b'y'*1000000)"],
                     str(Path(ctx.logs) / "probe.log"), ctx.work, stdout_path=str(target))
    assert rc == 127
    assert "synthetic output full" in events.getvalue()
