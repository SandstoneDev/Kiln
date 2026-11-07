"""Selective retries must finish the recorded installation, not just the current selection."""
import contextlib
import io
import json
import shutil

import pytest

import kiln
from kilnlib.manifest import Manifest
from test_kiln import EXE, KnownExe, fake_client, small_exe
from test_updates import pbp


BAKER = '''import argparse
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--extracted")
ap.add_argument("--out")
a = ap.parse_args()
Path(a.out).write_bytes(b"synthetic converted resource")
'''


@pytest.fixture
def installation(tmp_path):
    game = fake_client(str(tmp_path), "client", ["enUS"])
    out = tmp_path / "out"
    (out / "data").mkdir(parents=True)
    (out / "xmb").mkdir()
    neutral, personal = pbp(art=b"neutral"), pbp(art=b"personal")
    (out / "EBOOT.PBP").write_bytes(neutral)
    (out / "xmb/EBOOT.PBP").write_bytes(personal)
    (out / "data/installed.pak").write_bytes(b"already converted")
    man = Manifest(str(out), str(out / "data"))
    man.record("installed", status="ok", key="old-recipe", outputs=["data/installed.pak"], rc=0, seconds=2)
    original = dict(man.step("installed"))
    for name, status in (("retrya", "failed"), ("retryb", "blocked"), ("retryc", "failed")):
        man.record(name, status=status, key="old-recipe", outputs=[], rc=1, seconds=1)
    man.save()
    bakers = tmp_path / "bakers"
    scripts = bakers / "tools/psp"
    scripts.mkdir(parents=True)
    for name in ("retrya", "retryb", "retryc"):
        (scripts / f"build_{name}_pak.py").write_text(BAKER, encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copy(small_exe(), tools / ("psp_mpq_extract" + EXE))

    def run(steps, jobs=1, options=()):
        buf = io.StringIO()
        with KnownExe((b"MZ synthetic", 1)), contextlib.redirect_stdout(buf):
            rc = kiln.main(["--game", game, "--out", str(out), "--bakers", str(bakers),
                            "--tools", str(tools), "--steps", steps, "--jobs", str(jobs), "--json"] + list(options))
        events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
        return rc, events

    return out, man, original, neutral, personal, run


def test_successful_partial_retry_keeps_installation_incomplete(installation):
    out, _, original, neutral, _, run = installation
    rc, events = run("retrya")
    assert rc == 1
    assert set(events[-1]["failed"]) == {"retryb", "retryc"}
    assert not (out / "data/client/game.exe").exists()
    assert (out / "EBOOT.PBP").read_bytes() == neutral
    logs = "\n".join(e["text"] for e in events if e["event"] == "log")
    assert "Next: copy" not in logs
    assert "Your EBOOT with menu art" not in logs
    recorded = Manifest(str(out), str(out / "data"))
    assert recorded.step("retrya")["status"] == "ok"
    assert recorded.step("retryb")["status"] == "blocked"
    assert recorded.step("installed") == original


def test_retrying_only_the_last_failures_completes_without_rebuilding_healthy_steps(installation):
    out, _, original, _, personal, run = installation
    rc, events = run("retrya,retryb,retryc")
    assert rc == 0
    assert events[-1]["failed"] == []
    made = {e["name"] for e in events if e["event"] == "step" and e["state"] == "ok"}
    assert made == {"retrya", "retryb", "retryc"}
    assert (out / "data/client/game.exe").read_bytes() == b"MZ synthetic"
    assert (out / "EBOOT.PBP").read_bytes() == personal
    recorded = Manifest(str(out), str(out / "data"))
    assert recorded.step("installed") == original
    assert recorded.step("client_exe")["status"] == "ok"
    # A healthy selective re-run remains successful and reuses the converted resource.
    rc, events = run("retrya")
    assert rc == 0
    assert events[-1]["failed"] == []
    assert [(e["name"], e["state"]) for e in events if e["event"] == "step"] == [("retrya", "skipped")]


def test_unavailable_failed_recipe_still_prevents_completion(installation):
    out, man, _, neutral, _, run = installation
    man.record("old_converter_not_available", status="failed", key="old", outputs=[], rc=1, seconds=0)
    man.save()
    rc, events = run("retrya,retryb,retryc")
    assert rc == 1
    assert events[-1]["failed"] == ["old_converter_not_available"]
    assert (out / "EBOOT.PBP").read_bytes() == neutral
    assert not (out / "data/client/game.exe").exists()


def test_recorded_client_exe_failure_is_retried_after_converters_succeed(installation):
    out, man, _, _, personal, run = installation
    man.record("client_exe", status="failed", key="old", outputs=[], rc=1, seconds=0)
    man.save()
    rc, events = run("retrya,retryb,retryc")
    assert rc == 0
    assert events[-1]["failed"] == []
    assert (out / "data/client/game.exe").read_bytes() == b"MZ synthetic"
    assert (out / "EBOOT.PBP").read_bytes() == personal


def test_engine_only_update_does_not_invalidate_unchanged_client_executable(installation):
    out, _, _, _, _, run = installation
    assert run("retrya,retryb,retryc")[0] == 0
    before = Manifest(str(out), str(out / "data")).step("client_exe")
    (out / "EBOOT.PBP").write_bytes(pbp(code=b"updated engine", art=b"personal"))
    # --check computes identity_now for the real recorded client_exe identity.
    _, events = run("retrya", options=("--check",))
    client_problems = [e for e in events if e["event"] == "check" and e.get("step") == "client_exe"]
    assert client_problems == []
    assert Manifest(str(out), str(out / "data")).step("client_exe") == before
    assert (out / "data/client/game.exe").read_bytes() == b"MZ synthetic"


def test_first_interruption_records_unstarted_selected_recipes(installation, monkeypatch):
    out, _, _, _, _, run = installation
    script = out.parent / "bakers/tools/psp/build_retryd_pak.py"
    script.write_text(BAKER, encoding="utf-8")

    def interrupt(*_args, **_kwargs): raise KeyboardInterrupt
    monkeypatch.setattr(kiln, "run_gated", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run("retrya,retryd")
    record = Manifest(str(out), str(out / "data")).step("retryd")
    assert record is not None and record["status"] == "pending"
    assert record["outputs"] == []


def memory_baker(installation, message, *, always_fail=False):
    out = installation[0]
    script = out.parent / "bakers/tools/psp/build_retrya_pak.py"
    script.write_text('''import argparse, sys
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--extracted")
ap.add_argument("--out")
ap.add_argument("--jobs", default="unset")
a = ap.parse_args()
out = Path(a.out)
count = out.with_suffix(".attempts")
attempt = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(attempt))
with out.with_suffix(".workers").open("a") as worker_log:
    worker_log.write(a.jobs + "\\n")
if attempt == 1 or ALWAYS_FAIL:
    print(MESSAGE, file=sys.stderr)
    sys.exit(42)
# Recovery must happen after the other workers have finished producing their outputs.
if not all((out.parent / (name + ".pak")).exists() for name in ("retryb", "retryc")):
    sys.exit(43)
out.write_bytes(b"recovered resource")
'''.replace("MESSAGE", repr(message)).replace("ALWAYS_FAIL", repr(always_fail)), encoding="utf-8")


def test_bad_alloc_is_retried_once_after_workers_finish_without_rerunning_successes(installation, monkeypatch):
    out, _, original, _, personal, run = installation
    memory_baker(installation, "terminate called after throwing an instance of 'std::bad_alloc'")
    monkeypatch.setitem(kiln.S.PY_OVERRIDES, "retrya", {"extra": ["--jobs", "{jobs}"]})
    rc, events = run("retrya,retryb,retryc", jobs=3)
    assert rc == 0
    assert events[-1]["failed"] == []
    assert (out / "data/retrya.attempts").read_text() == "2"
    assert (out / "data/retrya.workers").read_text().splitlines() == ["3", "1"]
    assert (out / "data/retrya.pak").read_bytes() == b"recovered resource"
    assert "std::bad_alloc" in (out / "logs/kiln.log").read_text()
    assert sorted(p.name for p in (out / "logs").iterdir()) == ["kiln.log"]
    for name in ("retryb", "retryc"):
        assert len([e for e in events if e["event"] == "step" and e["name"] == name and e["state"] == "start"]) == 1
    assert Manifest(str(out), str(out / "data")).step("installed") == original
    assert (out / "EBOOT.PBP").read_bytes() == personal


def test_unrelated_converter_crash_is_not_retried(installation):
    out, _, _, neutral, _, run = installation
    memory_baker(installation, "segmentation fault")
    rc, events = run("retrya,retryb,retryc", jobs=3)
    assert rc == 1
    assert events[-1]["failed"] == ["retrya"]
    assert (out / "data/retrya.attempts").read_text() == "1"
    assert not (out / "logs/retrya.oom-first.log").exists()
    assert (out / "EBOOT.PBP").read_bytes() == neutral


def test_persistent_bad_alloc_stops_after_one_retry(installation):
    out, _, _, neutral, _, run = installation
    memory_baker(installation, "std::bad_alloc", always_fail=True)
    rc, events = run("retrya,retryb,retryc", jobs=3)
    assert rc == 1
    assert events[-1]["failed"] == ["retrya"]
    assert (out / "data/retrya.attempts").read_text() == "2"
    assert "std::bad_alloc" in (out / "logs/kiln.log").read_text()
    assert sorted(p.name for p in (out / "logs").iterdir()) == ["kiln.log"]
    assert (out / "EBOOT.PBP").read_bytes() == neutral


def test_new_gate_blocked_step_remains_incomplete_after_retrying_only_the_failures(installation, monkeypatch):
    out, man, original, neutral, _, run = installation
    # A fresh queue has no record at all for the fourth recipe when it gets blocked.
    man.doc["steps"] = {"installed": original}
    man.save()
    script = out.parent / "bakers/tools/psp/build_gate_pak.py"
    script.write_text('''import argparse, sys
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--out")
a = ap.parse_args()
if not Path(__file__).with_suffix(".ready").exists():
    sys.exit(42)
Path(a.out).write_bytes(b"recovered resource")
''', encoding="utf-8")
    recipes = [kiln.S.Step(name, "interface", "synthetic shared converter", prefixes=[],
                          run=[["@py:build_gate_pak.py", "--out", "{D}/" + name + ".pak"]],
                          outputs=["data/" + name + ".pak"])
               for name in ("gatea", "gateb", "gatec", "gated")]
    # Supply synthetic recipes; execution, failure counting, manifest and CLI remain real.
    monkeypatch.setattr(kiln.S, "static_steps", lambda _bakers: recipes)
    rc, first_events = run("gatea,gateb,gatec,gated")
    assert rc == 1
    assert first_events[-1]["blocked"] == ["gated"]
    script.with_suffix(".ready").write_bytes(b"ready")
    rc, events = run("gatea,gateb,gatec")
    assert rc == 1
    assert events[-1]["failed"] == ["gated"]
    recorded = Manifest(str(out), str(out / "data"))
    assert recorded.step("gated")["status"] == "blocked"
    assert "failed in 3 steps" in recorded.step("gated")["note"]
    assert recorded.step("installed") == original
    assert (out / "EBOOT.PBP").read_bytes() == neutral
    assert not (out / "data/client/game.exe").exists()


@pytest.mark.parametrize("damaged", [False, True], ids=["healthy", "outdated"])
@pytest.mark.parametrize("options", [(), ("--force",), ("--update",)], ids=["resume", "force", "update"])
def test_gate_preserves_current_output_but_records_blocked_outdated_work(installation, monkeypatch, damaged, options):
    out, man, original, _, _, run = installation
    man.doc["steps"] = {"installed": original}
    man.save()
    script = out.parent / "bakers/tools/psp/build_gate_pak.py"
    script.write_text('''import argparse, sys
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--out")
a = ap.parse_args()
if Path(a.out).name != "gated.pak":
    sys.exit(42)
Path(a.out).write_bytes(b"already complete resource")
''', encoding="utf-8")
    recipes = [kiln.S.Step(name, "interface", "synthetic shared converter", prefixes=[],
                          run=[["@py:build_gate_pak.py", "--out", "{D}/" + name + ".pak"]],
                          outputs=["data/" + name + ".pak"])
               for name in ("gatea", "gateb", "gatec", "gated")]
    monkeypatch.setattr(kiln.S, "static_steps", lambda _bakers: recipes)
    assert run("gated")[0] == 0
    previous = Manifest(str(out), str(out / "data")).step("gated")
    if damaged:
        (out / "data/gated.pak").write_bytes(b"damaged")
    rc, events = run("gatea,gateb,gatec,gated", options=options)
    assert rc == 1
    states = [e["state"] for e in events if e["event"] == "step" and e["name"] == "gated"]
    recorded = Manifest(str(out), str(out / "data")).step("gated")
    if damaged or options == ("--force",):
        assert states == ["blocked"]
        assert recorded["status"] == "blocked"
        assert recorded["outputs"] == []
        assert (out / "data/gated.pak").read_bytes() == (b"damaged" if damaged else b"already complete resource")
    else:
        # --update filters a healthy recipe out before the execution queue;
        # ordinary resume reaches run_gated and emits its normal skipped event.
        assert states == ([] if options == ("--update",) else ["skipped"])
        assert recorded == previous
        assert (out / "data/gated.pak").read_bytes() == b"already complete resource"
