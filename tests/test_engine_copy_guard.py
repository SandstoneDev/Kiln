"""Resource sync must never undo an engine update or an unversioned test build."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from kilnlib import check
from kilnlib.manifest import Manifest
from test_updates import pbp


@pytest.mark.parametrize("source_version,target_version,source_code,target_code,copy", [
    ("0.1.0", "0.1.1", b"old", b"new", False),
    ("0.1.0", "0.1.0", b"old", b"hardware candidate", False),
    ("0.1.1", "0.1.0", b"new", b"old", True),
    ("0.1.0", "0.1.0", b"same", b"same", True),
])
def test_resource_sync_preserves_installed_engine(tmp_path, source_version, target_version, source_code, target_code, copy):
    out = tmp_path / "out"
    target = tmp_path / "card"
    (out / "xmb").mkdir(parents=True)
    target.mkdir()
    source = out / "xmb/EBOOT.PBP"
    source.write_bytes(pbp(source_version, source_code, b"new local art"))
    installed = target / "EBOOT.PBP"
    previous = pbp(target_version, target_code, b"personal art")
    installed.write_bytes(previous)
    manifest = Manifest(str(out), str(out / "data"))
    manifest.record("xmb_art", status="ok", key="x", outputs=["xmb/EBOOT.PBP"], rc=0, seconds=0)
    ctx = SimpleNamespace(out=str(out), data=str(out / "data"), json=False,
                          a=SimpleNamespace(target=str(target)), event=lambda *a, **k: None, say=lambda *a, **k: None)
    report = check.Report(ctx)
    check.check_target(ctx, manifest, str(target), report)
    if not copy:
        assert not report.problems(), "a protected engine is not a corrupt asset copy"
    check.sync_target(ctx, manifest, str(target), report)
    assert installed.read_bytes() == (source.read_bytes() if copy else previous)


@pytest.mark.parametrize("protect", [False, True])
def test_complete_runtime_reaches_card_without_downgrading_installed_runtime(tmp_path, protect):
    out, target = tmp_path / "out", tmp_path / "card"
    (out / "data/opcodes").mkdir(parents=True)
    target.mkdir()
    (out / "EBOOT.PBP").write_bytes(pbp(code=b"local engine"))
    (out / "sdbg.prx").write_bytes(b"local debug")
    (out / "data/opcodes/commands.json").write_bytes(b"{}")
    if protect:
        (target / "EBOOT.PBP").write_bytes(pbp("0.1.1", b"newer engine"))
        (target / "sdbg.prx").write_bytes(b"newer debug")
    manifest = Manifest(str(out), str(out / "data"))
    manifest.save()
    ctx = SimpleNamespace(out=str(out), data=str(out / "data"), json=True,
                          event=lambda *a, **k: None, say=lambda *a, **k: None)
    result = check.sync_target(ctx, manifest, str(target), check.Report(ctx))
    assert result[2] == 0
    if protect:
        assert (target / "sdbg.prx").read_bytes() == b"newer debug"
        assert not (target / "data/opcodes/commands.json").exists()
    else:
        assert (target / "EBOOT.PBP").read_bytes() == (out / "EBOOT.PBP").read_bytes()
        assert (target / "sdbg.prx").read_bytes() == b"local debug"
        assert (target / "data/opcodes/commands.json").read_bytes() == b"{}"
