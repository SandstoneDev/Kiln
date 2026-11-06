"""New model formats must never be paired with the old engine by setup or CLI."""
import contextlib
import io
import json
import shutil
import urllib.error
from pathlib import Path

import pytest

import kiln
from kilnlib import updates as u
from kilnlib.manifest import Manifest
from test_kiln import EXE, KnownExe, fake_client, small_exe
from test_updates import archive, network, pbp


def runtime_archive(version):
    return archive([
        ("WOWEEPSP/EBOOT.PBP", pbp(version, b"release-code", b"neutral")),
        ("WOWEEPSP/sdbg.prx", b"release-debug"),
        ("WOWEEPSP/Data/opcodes/commands.json", b"{}"),
        ("WOWEEPSP/Data/expansions/wotlk/profile.json", b"{}"),
    ])


def test_setup_upgrades_old_engine_before_new_resources_and_preserves_settings(tmp_path):
    target = tmp_path / "installation"
    (target / "data").mkdir(parents=True)
    old = pbp("0.1.0", art=b"personal")
    (target / "EBOOT.PBP").write_bytes(old)
    (target / "wowee.cfg").write_bytes(b"server=chosen.invalid\n")
    (target / "data/characters.pak").write_bytes(b"existing models")
    opener, requests = network(runtime_archive("0.2.0"), version="0.2.0")
    result = u.ensure_engine(tmp_path / "cache", target, opener=opener)
    assert result["status"] == "installed"
    assert u.engine_version(target) == "0.2.0"
    assert len(requests) == 2
    assert (target / "wowee.cfg").read_bytes() == b"server=chosen.invalid\n"
    assert (target / "data/characters.pak").read_bytes() == b"existing models"
    from kilnlib.xmb import read_pbp
    assert read_pbp((target / "EBOOT.PBP").read_bytes())[1]["ICON0.PNG"] == b"personalICON0.PNG"


@pytest.mark.parametrize("version", ["0.2.0", "0.3.0"])
def test_setup_keeps_compatible_custom_build_without_network(tmp_path, version):
    target = tmp_path / "installation"
    target.mkdir()
    original = pbp(version, code=b"owner hardware build")
    (target / "EBOOT.PBP").write_bytes(original)
    def offline(*args, **kwargs):
        raise AssertionError("a compatible custom engine must remain usable offline")
    result = u.ensure_engine(tmp_path / "cache", target, opener=offline)
    assert result["status"] == "kept"
    assert (target / "EBOOT.PBP").read_bytes() == original
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("available", [False, True])
def test_setup_refuses_old_engine_when_compatible_release_unavailable(tmp_path, available):
    target = tmp_path / "installation"
    target.mkdir()
    old = pbp("0.1.0")
    (target / "EBOOT.PBP").write_bytes(old)
    (target / "wowee.cfg").write_bytes(b"saved settings")
    if available:
        opener, requests = network(runtime_archive("0.1.1"), version="0.1.1")
    else:
        requests = []
        def opener(request, timeout):
            requests.append(request.full_url)
            raise urllib.error.URLError("offline")
    with pytest.raises(u.UpdateError, match="0.2.0"):
        u.ensure_engine(tmp_path / "cache", target, opener=opener)
    assert len(requests) == 1
    assert (target / "EBOOT.PBP").read_bytes() == old
    assert (target / "wowee.cfg").read_bytes() == b"saved settings"
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("mode", ["run", "update"])
@pytest.mark.parametrize("engine_location", ["explicit", "installed"])
@pytest.mark.parametrize("name,version,allowed", [
    ("characters", "0.1.0", False),
    ("creatures", "0.1.0", False),
    ("characters", "0.2.0", True),
    ("creatures", "0.3.0", True),
    ("characters", None, True),
    ("xmb_art", "0.1.0", True),
])
def test_cli_model_gate_runs_before_baker_and_keeps_data_only_workflow(
        tmp_path, monkeypatch, mode, engine_location, name, version, allowed):
    game = fake_client(str(tmp_path), "client", ["enUS"])
    out = tmp_path / "out"
    out.mkdir()
    bakers = tmp_path / "bakers/tools/psp"
    bakers.mkdir(parents=True)
    (bakers / "build_probe_pak.py").write_text(
        'import sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_bytes(b"converted")\n')
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copy(small_exe(), tools / ("psp_mpq_extract" + EXE))
    recipe = kiln.S.Step(name, "models", "synthetic model baker", [],
                        [["@py:build_probe_pak.py", "{D}/probe.bin"]], ["data/probe.bin"])
    monkeypatch.setattr(kiln.S, "static_steps", lambda _: [recipe])
    args = ["--game", game, "--out", str(out), "--bakers", str(bakers.parent.parent),
            "--tools", str(tools), "--steps", name, "--json"]
    if version:
        selected = tmp_path / "selected.PBP" if engine_location == "explicit" else out / "EBOOT.PBP"
        selected.write_bytes(pbp(version))
        if engine_location == "explicit":
            args += ["--eboot", str(selected)]
    if mode == "update":
        man = Manifest(str(out), str(out / "data"))
        man.record(name, status="failed", key="old", outputs=[], rc=1, seconds=0)
        man.save()
        args += ["--update"]
    buf = io.StringIO()
    with KnownExe((b"MZ synthetic", 1)), contextlib.redirect_stdout(buf):
        rc = kiln.main(args)
    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    assert (out / "data/probe.bin").exists() == allowed
    assert (rc == 0) == allowed, events
    if not allowed:
        assert any(e["event"] == "error" and "0.2.0" in e.get("message", "") for e in events)


@pytest.mark.parametrize("mode", ["run", "update"])
@pytest.mark.parametrize("out_version,explicit_version,target_version,allowed", [
    (None, "0.2.0", "0.1.0", False),
    ("0.2.0", None, "0.1.0", False),
    ("0.1.0", "0.2.0", "0.2.0", False),
    ("0.2.0", "0.2.0", None, False),
    (None, "0.2.0", None, False),
    (None, "0.2.0", "0.2.0", True),
    ("0.2.0", None, "0.3.0", True),
])
def test_cli_checks_every_runtime_destination_before_model_write(
        tmp_path, monkeypatch, mode, out_version, explicit_version, target_version, allowed):
    game = fake_client(str(tmp_path), "client", ["enUS"])
    out, target = tmp_path / "out", tmp_path / "card"
    out.mkdir(); target.mkdir()
    for root, version in ((out, out_version), (target, target_version)):
        if version:
            (root / "EBOOT.PBP").write_bytes(pbp(version, code=b"custom build"))
    bakers = tmp_path / "bakers/tools/psp"
    bakers.mkdir(parents=True)
    (bakers / "build_probe_pak.py").write_text(
        'import sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_bytes(b"converted")\n')
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copy(small_exe(), tools / ("psp_mpq_extract" + EXE))
    recipe = kiln.S.Step("characters", "models", "synthetic model baker", [],
                        [["@py:build_probe_pak.py", "{D}/characters.bin"]], ["data/characters.bin"])
    monkeypatch.setattr(kiln.S, "static_steps", lambda _: [recipe])
    args = ["--game", game, "--out", str(out), "--target", str(target / "data"),
            "--bakers", str(bakers.parent.parent), "--tools", str(tools), "--steps", "characters", "--json"]
    if explicit_version:
        selected = tmp_path / "selected.PBP"
        selected.write_bytes(pbp(explicit_version))
        args += ["--eboot", str(selected)]
    if mode == "update":
        man = Manifest(str(out), str(out / "data"))
        man.record("characters", status="failed", key="old", outputs=[], rc=1, seconds=0)
        man.save()
        args += ["--update"]
    saved = {str(root): (root / "EBOOT.PBP").read_bytes() for root in (out, target)
             if (root / "EBOOT.PBP").exists()}
    buf = io.StringIO()
    with KnownExe((b"MZ synthetic", 1)), contextlib.redirect_stdout(buf):
        rc = kiln.main(args)
    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    assert (out / "data/characters.bin").exists() == allowed
    assert (target / "data/characters.bin").exists() == allowed
    assert (rc == 0) == allowed, events
    if not allowed:
        assert any(e["event"] == "error" and "0.2.0" in e.get("message", "") for e in events)
        for root, original in saved.items():
            assert (Path(root) / "EBOOT.PBP").read_bytes() == original


def test_gui_shared_resource_gate_prepares_both_actual_destinations():
    # Source-path regression for the GUI wiring; actual service and CLI side effects
    # are exercised above without opening a window or touching owner settings.
    root = Path(kiln.__file__).parent / "gui"
    shared = (root / "MainForm.cs").read_text(encoding="utf-8")
    assets = (root / "AssetUpdates.cs").read_text(encoding="utf-8")
    setup = (root / "EngineSetup.cs").read_text(encoding="utf-8")
    assert "await RunKilnAsync(options)" in assets
    body = shared.split("private async Task<int> RunKilnAsync(RunOptions opt)", 1)[1]
    assert body.index("await PrepareEngineAsync(opt)") < body.index("KilnCli.Args(opt")
    assert "PreparationTargets(options.Out, options.Target)" in setup
    assert "installation, cache, _runCts!.Token" in setup
