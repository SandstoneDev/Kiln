"""Portable recipe metadata is copied only after successful synthetic data sync."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kilnlib import check
from kilnlib.manifest import Manifest


@pytest.fixture
def setup(tmp_path):
    out, target = tmp_path / "out", tmp_path / "card"
    (out / "data").mkdir(parents=True)
    target.mkdir()
    man = Manifest(str(out), str(out / "data"))
    man.doc.update(client={"build": 1}, locales={"primary": "enUS"})
    for name in ("alpha", "beta"):
        rel = "data/" + name + ".pak"
        (out / rel).write_bytes(("synthetic " + name).encode())
        man.record(name, status="ok", key="recipe-" + name, outputs=[rel], rc=0, seconds=1,
                   identity={"programs": {}, "input": {"locale": "enUS"}}, key_version=1)
    man.save()
    cfg = target / "wowee.cfg"
    cfg.write_bytes(b"server=synthetic.invalid\n")
    ctx = SimpleNamespace(out=str(out), data=str(out / "data"), json=True,
                          say=lambda *a, **k: None, event=lambda *a, **k: None)
    return ctx, man, target


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_successful_sync_mirrors_full_manifest_and_check_is_readonly(setup):
    ctx, man, target = setup
    result = check.sync_target(ctx, man, str(target), check.Report(ctx))
    assert result[:3] == (2, 0, 0)
    mirror = target / "data/kiln.manifest.json"
    assert mirror.is_file(), "a new PC needs the full recipe history on the card"
    assert json.loads(mirror.read_text(encoding="utf-8")) == man.doc
    assert Manifest(str(target), str(target / "data")).doc == man.doc
    assert (target / "wowee.cfg").read_bytes() == b"server=synthetic.invalid\n"
    before = snapshot(target)
    rep = check.Report(ctx)
    check.check_target(ctx, man, str(target), rep)
    assert rep.items == []
    assert snapshot(target) == before


def test_existing_target_manifest_is_known_metadata(setup):
    ctx, man, target = setup
    check.sync_target(ctx, man, str(target), check.Report(ctx))
    (target / "data/kiln.manifest.json").write_text(json.dumps(man.doc), encoding="utf-8")
    rep = check.Report(ctx)
    check.check_target(ctx, man, str(target), rep)
    assert not [i for i in rep.items if i["kind"] == "extra"]


def test_failed_copy_preserves_old_manifest_after_partial_success(setup, monkeypatch):
    ctx, man, target = setup
    (target / "data").mkdir()
    old = b'{"previous":"recipe history"}'
    mirror = target / "data/kiln.manifest.json"
    mirror.write_bytes(old)
    copy = check.shutil.copyfile

    def disk_failure(source, destination):
        if Path(source).name == "beta.pak":
            raise OSError("synthetic copy failure")
        return copy(source, destination)

    monkeypatch.setattr(check.shutil, "copyfile", disk_failure)
    result = check.sync_target(ctx, man, str(target), check.Report(ctx))
    assert result[:3] == (1, 0, 1)
    assert (target / "data/alpha.pak").read_bytes() == b"synthetic alpha"
    assert mirror.read_bytes() == old


def test_repair_only_sync_refreshes_complete_recipe_history(setup):
    ctx, man, target = setup
    check.sync_target(ctx, man, str(target), check.Report(ctx))
    (target / "data/alpha.pak").write_bytes(b"damaged")
    man.doc["tool_version"] = "synthetic-new"
    result = check.sync_target(ctx, man, str(target), check.Report(ctx), only={check.key("data/alpha.pak")})
    assert result[:3] == (1, 0, 0)
    mirror = target / "data/kiln.manifest.json"
    assert json.loads(mirror.read_text(encoding="utf-8")) == man.doc


def test_manifest_replace_failure_preserves_previous_snapshot(setup, monkeypatch):
    ctx, man, target = setup
    (target / "data").mkdir()
    mirror = target / "data/kiln.manifest.json"
    mirror.write_bytes(b"previous metadata")
    replace = check.os.replace

    def failure(source, destination):
        if Path(destination) == mirror:
            raise OSError("synthetic metadata replace failure")
        return replace(source, destination)

    monkeypatch.setattr(check.os, "replace", failure)
    rep = check.Report(ctx)
    result = check.sync_target(ctx, man, str(target), rep)
    assert result[2] == 1
    assert mirror.read_bytes() == b"previous metadata"
    assert [(i["kind"], i["path"]) for i in rep.items] == [("copy-failed", "data/kiln.manifest.json")]
