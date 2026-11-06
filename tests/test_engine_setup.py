"""Fresh installations use the same verified release and rollback as updates."""
import json
from pathlib import Path

import pytest

from kilnlib import updates as u
from kilnlib.xmb import read_pbp
from test_updates import archive, network, pbp


def complete_archive():
    return archive([
        ("WOWEEPSP/EBOOT.PBP", pbp("0.2.0", b"release-code", b"neutral")),
        ("WOWEEPSP/sdbg.prx", b"debug"),
        ("WOWEEPSP/Data/opcodes/commands.json", b"{}"),
        ("WOWEEPSP/Data/expansions/wotlk/profile.json", b"{}"),
        ("WOWEEPSP/wowee.cfg.example", b"example"),
    ])


def test_empty_folder_downloads_complete_runtime_and_keeps_release_art(tmp_path):
    target = tmp_path / "my game"
    opener, requests = network(complete_archive(), version="0.2.0")
    result = u.ensure_engine(tmp_path / "cache", target, opener=opener)
    assert result["status"] == "installed"
    assert u.engine_version(target) == "0.2.0"
    assert (target / "sdbg.prx").read_bytes() == b"debug"
    assert json.loads((target / "Data/opcodes/commands.json").read_bytes()) == {}
    assert (target / "Data/expansions/wotlk/profile.json").exists()
    assert read_pbp((target / "EBOOT.PBP").read_bytes())[1]["ICON0.PNG"] == b"neutralICON0.PNG"
    assert not (target / "wowee.cfg.example").exists()
    assert len(requests) == 2


def test_existing_hardware_candidate_is_kept_offline(tmp_path):
    target = tmp_path / "game"
    target.mkdir()
    candidate = pbp("0.2.0", code=b"hardware-candidate")
    (target / "EBOOT.PBP").write_bytes(candidate)
    def no_network(*args, **kwargs):
        raise AssertionError("an existing installation must work offline")
    result = u.ensure_engine(tmp_path / "cache", target, opener=no_network)
    assert result["status"] == "kept"
    assert (target / "EBOOT.PBP").read_bytes() == candidate
    assert not (tmp_path / "cache").exists()


def test_invalid_existing_eboot_is_not_replaced(tmp_path):
    target = tmp_path / "game"
    target.mkdir()
    (target / "EBOOT.PBP").write_bytes(b"owner file")
    opener, requests = network(complete_archive())
    with pytest.raises(u.UpdateError, match="EBOOT"):
        u.ensure_engine(tmp_path / "cache", target, opener=opener)
    assert requests == []
    assert (target / "EBOOT.PBP").read_bytes() == b"owner file"


def test_fresh_install_preserves_data_and_config_and_rolls_back(tmp_path):
    target = tmp_path / "game"
    (target / "data").mkdir(parents=True)
    (target / "data/own.pak").write_bytes(b"local resource")
    (target / "wowee.cfg").write_bytes(b"saved config")
    (target / "sdbg.prx").write_bytes(b"existing debug")
    zipped = tmp_path / "engine.zip"
    zipped.write_bytes(complete_archive())
    def fail_eboot(source, destination):
        if Path(destination).name == "EBOOT.PBP":
            raise OSError("simulated removable drive failure")
        __import__("os").replace(source, destination)
    with pytest.raises(u.UpdateError, match="rolled back"):
        u.install_engine(zipped, target, "0.2.0", initialize=True, replace=fail_eboot)
    assert not (target / "EBOOT.PBP").exists()
    assert (target / "sdbg.prx").read_bytes() == b"existing debug"
    assert (target / "data/own.pak").read_bytes() == b"local resource"
    assert (target / "wowee.cfg").read_bytes() == b"saved config"
    assert not (target / ".kiln-update.lock").exists()


def test_incomplete_release_never_creates_installation(tmp_path):
    target = tmp_path / "game"
    opener, _ = network(archive([("WOWEEPSP/EBOOT.PBP", pbp("0.2.0"))]), version="0.2.0")
    with pytest.raises(u.UpdateError):
        u.ensure_engine(tmp_path / "cache", target, opener=opener)
    assert not target.exists()


def test_cancel_before_setup_writes_nothing(tmp_path):
    opener, requests = network(complete_archive())
    with pytest.raises(u.UpdateError) as error:
        u.ensure_engine(tmp_path / "cache", tmp_path / "game", opener=opener, cancel=lambda: True)
    assert error.value.code == "cancelled"
    assert requests == []
    assert not (tmp_path / "game").exists()


def test_personal_art_published_at_root_without_replacing_different_code(tmp_path):
    target = tmp_path / "game"
    (target / "xmb").mkdir(parents=True)
    original = pbp(code=b"candidate", art=b"neutral")
    personal = pbp(code=b"candidate", art=b"personal")
    (target / "EBOOT.PBP").write_bytes(original)
    (target / "xmb/EBOOT.PBP").write_bytes(personal)
    assert u.finish_engine_art(target)
    assert (target / "EBOOT.PBP").read_bytes() == personal
    candidate = pbp(code=b"different-candidate")
    (target / "EBOOT.PBP").write_bytes(candidate)
    assert not u.finish_engine_art(target)
    assert (target / "EBOOT.PBP").read_bytes() == candidate


def test_art_uses_prepared_engine_before_older_card_copy(tmp_path):
    from kilnlib.xmb import resolve_eboot
    out, card = tmp_path / "out", tmp_path / "card"
    out.mkdir(); card.mkdir()
    (out / "EBOOT.PBP").write_bytes(pbp("0.1.1"))
    (card / "EBOOT.PBP").write_bytes(pbp("0.1.0"))
    assert resolve_eboot(out=str(out), target=str(card)) == str(out / "EBOOT.PBP")
