"""A manifest adopted from an installation cannot name files outside it."""
import json

import pytest

from kilnlib.manifest import Manifest, ManifestError


@pytest.mark.parametrize("path", ["../outside", "data/../../outside", "/absolute/file", "C:/outside",
    "C:relative", "\\\\server\\share", "data/file:stream", "data/CON.txt", "data/file.", "data//file", ""])
def test_manifest_rejects_unsafe_recorded_paths(tmp_path, path):
    data = tmp_path / "data"
    data.mkdir()
    manifest = data / "kiln.manifest.json"
    doc = {"schema": 1, "steps": {"test": {"status": "ok", "outputs": [
        {"path": path, "size": 0, "sha256": "0" * 64}]}}}
    original = json.dumps(doc).encode()
    manifest.write_bytes(original)
    with pytest.raises(ManifestError):
        Manifest(str(tmp_path), str(data))
    assert manifest.read_bytes() == original


def test_manifest_keeps_valid_windows_relative_paths(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    path = "data\\music\\sound\\music\\track 01.mp3"
    (data / "kiln.manifest.json").write_text(json.dumps({"schema": 1, "steps": {"audio": {
        "outputs": [{"path": path, "size": 0, "sha256": "0" * 64}]}}}))
    assert Manifest(str(tmp_path), str(data)).step("audio")["outputs"][0]["path"] == path
