"""Official releases and reversible engine installation, using synthetic bytes only."""
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

import pytest


def service():
    try:
        return importlib.import_module("kilnlib.updates")
    except ModuleNotFoundError:
        pytest.fail("the bounded official release service is not implemented")


def sfo(version):
    key = b"APP_VER\0"
    value = version.encode() + b"\0"
    return (struct.pack("<4sIIII", b"\0PSF", 0x101, 36, 36 + len(key), 1)
            + struct.pack("<HHIII", 0, 0x204, len(value), len(value), 0) + key + value)


def pbp(version="0.1.0", code=b"old-code", art=b"personal"):
    # Use the real existing PBP codec, without decoding any artwork.
    from kilnlib.xmb import write_pbp
    sections = {"PARAM.SFO": sfo(version), "DATA.PSP": code, "DATA.PSAR": b"archive"}
    for name in ("ICON0.PNG", "ICON1.PMF", "PIC0.PNG", "PIC1.PNG", "SND0.AT3"):
        sections[name] = art + name.encode()
    return write_pbp(0x10000, sections)


def archive(entries=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (entries or [
            ("WOWEEPSP/EBOOT.PBP", pbp("0.1.1", b"new-code", b"neutral")),
            ("WOWEEPSP/sdbg.prx", b"new-debug"),
            ("WOWEEPSP/Data/opcodes/new.json", b"{}"),
            ("WOWEEPSP/wowee.cfg.example", b"example"),
            ("README.md", b"readme"),
        ]):
            z.writestr(name, data)
    return out.getvalue()


def release(product="engine", data=b"zip", version="0.1.1"):
    repo = "WoWee-psp-port" if product == "engine" else "Kiln"
    name = f"WoWeePSP-{version}.zip" if product == "engine" else f"Kiln-{version}-win64.zip"
    base = f"https://github.com/SandstoneDev/{repo}/releases"
    return {"tag_name": "v" + version, "draft": False, "prerelease": False,
            "html_url": base + "/tag/v" + version,
            "assets": [{"name": name, "size": len(data),
                        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                        "browser_download_url": base + "/download/v" + version + "/" + name}]}


class Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url


def network(data=b"zip", version="0.1.1", mutate=None):
    requests = []

    def open_request(request, timeout):
        url = request.full_url
        requests.append(url)
        if url.startswith("https://api.github.com/"):
            product = "engine" if "/WoWee-psp-port/" in url else "kiln"
            doc = release(product, data, version)
            if mutate:
                mutate(doc)
            return Response(json.dumps(doc).encode(), url)
        return Response(data, url)
    return open_request, requests


@pytest.fixture
def target(tmp_path):
    p = tmp_path / "target"
    p.mkdir()
    (p / "EBOOT.PBP").write_bytes(pbp())
    (p / "sdbg.prx").write_bytes(b"old-debug")
    (p / "wowee.cfg").write_bytes(b"private config")
    (p / "data").mkdir()
    (p / "data/characters.pak").write_bytes(b"private game pack")
    return p


def test_versions_and_equal_version_copy_guard(target, tmp_path):
    u = service()
    source = tmp_path / "source.PBP"
    source.write_bytes(pbp(art=b"changed-art"))
    assert u.engine_version(target) == "0.1.0"
    assert u.should_replace_engine(source, target / "EBOOT.PBP")
    source.write_bytes(pbp(code=b"different-build"))
    assert not u.should_replace_engine(source, target / "EBOOT.PBP")
    source.write_bytes(pbp("0.0.9"))
    assert not u.should_replace_engine(source, target / "EBOOT.PBP")
    source.write_bytes(pbp("0.1.1"))
    assert u.should_replace_engine(source, target / "EBOOT.PBP")
    source.write_bytes(b"invalid")
    assert not u.should_replace_engine(source, target / "EBOOT.PBP")


def test_check_and_no_network_at_import(target):
    u = service()
    with patch("urllib.request.build_opener", side_effect=AssertionError("automatic network")):
        importlib.reload(u)
    opener, requests = network()
    result = u.check_updates(target, opener=opener)
    from kilnlib import KILN_VERSION
    assert result["kiln"]["current"] == KILN_VERSION
    assert result["engine"]["current"] == "0.1.0"
    assert result["engine"]["latest"] == "0.1.1"
    assert result["engine"]["update_available"]
    assert len(requests) == 2
    assert all(url.startswith("https://api.github.com/repos/SandstoneDev/") for url in requests)
    assert u.check_updates(opener=opener)["engine"]["current"] is None


@pytest.mark.parametrize("change", [
    lambda d: d.update(tag_name="v0.1.1-rc1"),
    lambda d: d.update(prerelease=True),
    lambda d: d["assets"][0].update(digest=None),
    lambda d: d["assets"][0].update(browser_download_url="https://example.org/engine.zip"),
    lambda d: d["assets"][0].update(size=9999999999),
])
def test_bad_metadata_never_downloads(tmp_path, change):
    u = service()
    opener, requests = network(mutate=change)
    with pytest.raises(u.UpdateError):
        u.download_release("engine", tmp_path / "downloads", opener=opener)
    assert len(requests) == 1
    assert not (tmp_path / "downloads").exists()


@pytest.mark.parametrize("url", ["http://github.com/x", "https://localhost/x",
    "https://github.com.evil.test/x", "https://user@github.com/x", "https://github.com:444/x"])
def test_redirect_allowlist(url):
    u = service()
    request = __import__("urllib.request", fromlist=["Request"]).Request("https://github.com/x")
    with pytest.raises(u.UpdateError):
        u.OfficialRedirectHandler().redirect_request(request, None, 302, "Found", {}, url)


def test_offline_and_digest_truncated_cancelled(tmp_path):
    u = service()
    def offline(request, timeout):
        raise urllib.error.URLError("offline secret detail")
    with pytest.raises(u.UpdateError, match="unavailable"):
        u.check_updates(opener=offline)
    for failure in ("digest", "truncated", "cancel"):
        opener, requests = network(b"expected payload")
        def broken(request, timeout):
            response = opener(request, timeout)
            if not request.full_url.startswith("https://api.github.com/"):
                response = Response(b"bad" if failure == "truncated" else b"x" * len(b"expected payload"), request.full_url)
            return response
        destination = tmp_path / failure
        with pytest.raises(u.UpdateError):
            u.download_release("engine", destination, opener=broken,
                               cancel=(lambda: True) if failure == "cancel" else None)
        assert not list(destination.glob("*.zip"))


def test_download_only_and_existing_destination_not_overwritten(tmp_path):
    u = service()
    opener, _ = network(b"portable executable is never run")
    result = u.download_release("kiln", tmp_path, opener=opener)
    assert result["product"] == "kiln"
    assert Path(result["path"]).read_bytes() == b"portable executable is never run"
    Path(result["path"]).write_bytes(b"owner file")
    with pytest.raises(u.UpdateError):
        u.download_release("kiln", tmp_path, opener=opener)
    assert Path(result["path"]).read_bytes() == b"owner file"


def test_engine_backup_artwork_and_private_data(target, tmp_path):
    u = service()
    from kilnlib.xmb import read_pbp
    old = (target / "EBOOT.PBP").read_bytes()
    opener, _ = network(archive())
    result = u.update_engine(tmp_path / "download", target, opener=opener)
    assert u.engine_version(target) == "0.1.1"
    _, before = read_pbp(old)
    _, after = read_pbp((target / "EBOOT.PBP").read_bytes())
    for name in ("ICON0.PNG", "ICON1.PMF", "PIC0.PNG", "PIC1.PNG", "SND0.AT3"):
        assert after[name] == before[name]
    assert after["DATA.PSP"] == b"new-code"
    backup = Path(result["backup"])
    assert (backup / "EBOOT.PBP").read_bytes() == old
    assert (backup / "sdbg.prx").read_bytes() == b"old-debug"
    assert (target / "wowee.cfg").read_bytes() == b"private config"
    assert (target / "data/characters.pak").read_bytes() == b"private game pack"
    assert not (target / "wowee.cfg.example").exists()


def test_rollback_restores_old_and_removes_new_files(target, tmp_path):
    u = service()
    z = tmp_path / "release.zip"
    z.write_bytes(archive())
    old = (target / "EBOOT.PBP").read_bytes()
    calls = []
    def fail_last(source, destination):
        calls.append(Path(destination).name)
        if Path(destination).name == "sdbg.prx":
            raise OSError("injected disk error")
        os.replace(source, destination)
    with pytest.raises(u.UpdateError, match="rolled back"):
        u.install_engine(z, target, "0.1.1", replace=fail_last)
    assert "new.json" in calls
    assert (target / "EBOOT.PBP").read_bytes() == old
    assert (target / "sdbg.prx").read_bytes() == b"old-debug"
    assert not (target / "Data/opcodes/new.json").exists()
    assert list((target / ".kiln-backups").glob("*/created/Data/opcodes/new.json"))
    assert not (target / "Data/opcodes").exists()  # only this transaction created it


@pytest.mark.parametrize("extra", ["../escape", "/absolute", "WOWEEPSP/../escape",
    "WOWEEPSP/C:escape", "WOWEEPSP/Data/opcodes/NUL", "WOWEEPSP/data/../account.dat",
    "EBOOT.PBP", "woweepsp/eboot.pbp", "WOWEEPSP/Data/opcodes/a/../../escape"])
def test_zip_path_and_duplicate_roots_never_modify_target(target, tmp_path, extra):
    u = service()
    before = (target / "EBOOT.PBP").read_bytes()
    z = tmp_path / "bad.zip"
    z.write_bytes(archive([("WOWEEPSP/EBOOT.PBP", pbp("0.1.1")), (extra, b"evil")]))
    with pytest.raises(u.UpdateError):
        u.install_engine(z, target, "0.1.1")
    assert (target / "EBOOT.PBP").read_bytes() == before
    assert not (target / ".kiln-backups").exists()


def test_symlinks_bombs_equal_version_and_unknown_target(target, tmp_path):
    u = service()
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as out:
        out.writestr("WOWEEPSP/EBOOT.PBP", pbp("0.1.1"))
        link = zipfile.ZipInfo("WOWEEPSP/Data/opcodes/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        out.writestr(link, "../../outside")
    with pytest.raises(u.UpdateError):
        u.install_engine(z, target, "0.1.1")
    z.write_bytes(archive([("WOWEEPSP/EBOOT.PBP", pbp("0.1.1")),
                           ("WOWEEPSP/Data/opcodes/bomb", b"0" * 1000000)]))
    with pytest.raises(u.UpdateError):
        u.install_engine(z, target, "0.1.1")
    z.write_bytes(archive())
    with pytest.raises(u.UpdateError):
        u.install_engine(z, target, "0.1.0")
    (target / "EBOOT.PBP").write_bytes(b"invalid")
    opener, requests = network(archive())
    with pytest.raises(u.UpdateError):
        u.update_engine(tmp_path / "downloads", target, opener=opener)
    assert not requests


def test_cli_one_json_error_without_network():
    service()
    result = subprocess.run([sys.executable, "-m", "kilnlib.updates", "download",
                             "--product", "unknown", "--destination", "unused"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "error" in json.loads(result.stdout)


def test_cli_help_is_also_one_json_document():
    service()
    result = subprocess.run([sys.executable, "-m", "kilnlib.updates", "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "help" in json.loads(result.stdout)


def test_prevalidated_descriptor_cannot_bypass_official_asset_rules(tmp_path):
    u = service()
    opener, _ = network()
    descriptor = u.fetch_release("engine", opener=opener)
    descriptor["asset"]["name"] = "../owner-file.zip"
    with pytest.raises(u.UpdateError):
        u.download_release("engine", tmp_path / "downloads", opener=opener, release=descriptor)
    assert not (tmp_path / "downloads").exists()


def test_keyboard_interrupt_rolls_back_new_runtime_files(target, tmp_path):
    u = service()
    z = tmp_path / "release.zip"
    z.write_bytes(archive())
    before = (target / "EBOOT.PBP").read_bytes()
    def interrupt(source, destination):
        if Path(destination).name == "sdbg.prx":
            raise KeyboardInterrupt()
        os.replace(source, destination)
    with pytest.raises(u.UpdateError, match="rolled back"):
        u.install_engine(z, target, "0.1.1", replace=interrupt)
    assert (target / "EBOOT.PBP").read_bytes() == before
    assert not (target / "Data/opcodes/new.json").exists()


def test_deadline_bounds_progressing_stream_and_cleans_partial_file(tmp_path):
    u = service()
    opener, _ = network(b"payload")
    ticks = iter([0, 0, 400])
    # The deadline is checked after each available chunk, not only when a
    # potentially long read(n) has filled an entire requested block.
    with patch.object(u.time, "monotonic", side_effect=lambda: next(ticks)):
        with pytest.raises(u.UpdateError):
            u.download_release("engine", tmp_path / "downloads", opener=opener,
                               release={"product": "engine", "version": "0.1.1",
                                        "asset": {"name": "WoWeePSP-0.1.1.zip", "size": 7,
                                                  "sha256": hashlib.sha256(b"payload").hexdigest(),
                                                  "url": "https://github.com/SandstoneDev/WoWee-psp-port/releases/download/v0.1.1/WoWeePSP-0.1.1.zip"},
                                        "release_url": "https://github.com/SandstoneDev/WoWee-psp-port/releases/tag/v0.1.1"})
    assert not list((tmp_path / "downloads").glob("*.zip"))


def test_receipt_failure_after_eboot_commit_restores_everything(target, tmp_path):
    u = service()
    z = tmp_path / "release.zip"
    z.write_bytes(archive())
    original = (target / "EBOOT.PBP").read_bytes()
    write_text = Path.write_text
    def fail_receipt(path, *args, **kwargs):
        if path.name == "receipt.json":
            assert u.engine_version(target) == "0.1.1"
            raise OSError("receipt disk failure")
        return write_text(path, *args, **kwargs)
    with patch.object(Path, "write_text", fail_receipt):
        with pytest.raises(u.UpdateError, match="rolled back"):
            u.install_engine(z, target, "0.1.1")
    assert (target / "EBOOT.PBP").read_bytes() == original
    assert (target / "sdbg.prx").read_bytes() == b"old-debug"
    assert not (target / "Data/opcodes/new.json").exists()


def test_unsafe_final_response_url_is_rejected(tmp_path):
    u = service()
    opener, requests = network()
    descriptor = u.fetch_release("engine", opener=opener)
    def redirected(request, timeout):
        return Response(b"zip", "https://evil.example/release")
    with pytest.raises(u.UpdateError):
        u.download_release("engine", tmp_path / "downloads", opener=redirected, release=descriptor)
    assert not list((tmp_path / "downloads").glob("*.zip"))


def test_file_in_runtime_parent_is_rejected_before_backup(target, tmp_path):
    u = service()
    (target / "Data/opcodes").write_bytes(b"owner file")
    z = tmp_path / "release.zip"
    z.write_bytes(archive())
    with pytest.raises(u.UpdateError):
        u.install_engine(z, target, "0.1.1")
    assert not (target / ".kiln-backups").exists()
    assert (target / "Data/opcodes").read_bytes() == b"owner file"


def test_target_link_is_rejected_before_backup(target, tmp_path):
    u = service()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (target / "Data/opcodes").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating a Windows directory symlink is not permitted")
    z = tmp_path / "release.zip"
    z.write_bytes(archive())
    with pytest.raises(u.UpdateError):
        u.install_engine(z, target, "0.1.1")
    assert not (target / ".kiln-backups").exists()
    assert not list(outside.iterdir())


def test_invalid_runtime_json_is_rejected_before_backup(target, tmp_path):
    u = service()
    z = tmp_path / "release.zip"
    z.write_bytes(archive([("WOWEEPSP/EBOOT.PBP", pbp("0.1.1")),
                           ("WOWEEPSP/Data/opcodes/canonical.json", b"{broken")]))
    with pytest.raises(u.UpdateError):
        u.install_engine(z, target, "0.1.1")
    assert not (target / ".kiln-backups").exists()


def test_cancel_after_staging_does_not_start_commit(target, tmp_path):
    u = service()
    z = tmp_path / "release.zip"
    z.write_bytes(archive())
    cancelled = False
    write = Path.write_bytes
    def stage_write(path, data):
        nonlocal cancelled
        result = write(path, data)
        if "kiln-engine-stage-" in str(path):
            cancelled = True
        return result
    with patch.object(Path, "write_bytes", stage_write):
        with pytest.raises(u.UpdateError, match="cancelled"):
            u.install_engine(z, target, "0.1.1", cancel=lambda: cancelled)
    assert u.engine_version(target) == "0.1.0"
    assert not (target / ".kiln-backups").exists()


@pytest.mark.parametrize("status", [403, 429])
def test_rate_limit_is_structured_and_does_not_expose_request_details(status):
    u = service()
    def limit(request, timeout):
        raise urllib.error.HTTPError("https://api.github.com/?private=value", status, "hidden detail", {}, None)
    with pytest.raises(u.UpdateError) as error:
        u.check_updates(opener=limit)
    assert error.value.code == "rate_limited"
    assert "private" not in str(error.value) and "hidden" not in str(error.value)


def test_redirects_are_bound_to_one_official_request():
    u = service()
    from urllib.request import Request
    api = "https://api.github.com/repos/SandstoneDev/Kiln/releases/latest"
    asset = "https://github.com/SandstoneDev/Kiln/releases/download/v0.1.1/Kiln-0.1.1-win64.zip"
    for initial, final in [(api, api), (api, "https://api.github.com/repos/Other/Repo/releases/latest"),
                           (asset, "https://github.com/Other/Repo/releases/download/v0.1.1/x.zip"),
                           (asset, api)]:
        handler = u.OfficialRedirectHandler(initial)
        with pytest.raises(u.UpdateError):
            handler.redirect_request(Request(initial), None, 302, "Found", {}, final)
    handler = u.OfficialRedirectHandler(asset)
    cdn = "https://release-assets.githubusercontent.com/github-production-release-asset/1/file?signature=test"
    assert handler.redirect_request(Request(asset), None, 302, "Found", {}, cdn).full_url == cdn


def test_final_url_cannot_bypass_request_binding(tmp_path):
    u = service()
    opener, _ = network()
    descriptor = u.fetch_release("engine", opener=opener)
    def redirected(request, timeout):
        return Response(b"zip", "https://github.com/Other/Repo/releases/download/v1.0.0/x.zip")
    with pytest.raises(u.UpdateError):
        u.download_release("engine", tmp_path / "downloads", opener=redirected, release=descriptor)


def test_replaced_cached_zip_is_reverified_before_target_mutation(target, tmp_path):
    u = service()
    original = (target / "EBOOT.PBP").read_bytes()
    opener, _ = network(archive())
    download = u.download_release
    def swapped(*args, **kwargs):
        result = download(*args, **kwargs)
        Path(result["path"]).write_bytes(archive([("WOWEEPSP/EBOOT.PBP", pbp("0.1.1", b"wrong-code"))]))
        return result
    with patch.object(u, "download_release", swapped):
        with pytest.raises(u.UpdateError):
            u.update_engine(tmp_path / "downloads", target, opener=opener)
    assert (target / "EBOOT.PBP").read_bytes() == original
    assert not (target / ".kiln-backups").exists()


def test_zip_parser_uses_pinned_verified_bytes(target, tmp_path):
    u = service()
    from kilnlib.xmb import read_pbp
    z = tmp_path / "release.zip"
    data = archive()
    z.write_bytes(data)
    replacement = archive([("WOWEEPSP/EBOOT.PBP", pbp("0.1.1", b"wrong-code"))])
    original_zip = zipfile.ZipFile
    def swap_before_parse(*args, **kwargs):
        z.write_bytes(replacement)
        return original_zip(*args, **kwargs)
    with patch.object(u.zipfile, "ZipFile", swap_before_parse):
        u.install_engine(z, target, "0.1.1", expected_asset={
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    assert read_pbp((target / "EBOOT.PBP").read_bytes())[1]["DATA.PSP"] == b"new-code"


@pytest.mark.parametrize("command", ["check", "download"])
def test_cli_cancel_file_immediately_cancels_without_a_request(tmp_path, command):
    service()
    flag = tmp_path / "cancel"
    flag.write_bytes(b"cancel")
    args = [sys.executable, "-m", "kilnlib.updates", command, "--cancel-file", str(flag)]
    if command == "download":
        args += ["--product", "kiln", "--destination", str(tmp_path / "downloads")]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]["code"] == "cancelled"
    assert flag.read_bytes() == b"cancel"
    assert not (tmp_path / "downloads").exists()


@pytest.mark.parametrize("failure,code", [("offline", "offline"), ("oversize", "invalid_release"),
                                         ("cancel_stream", "cancelled")])
def test_actual_cli_network_failures_are_json_and_leave_no_archive(tmp_path, failure, code):
    service()
    data = b"synthetic payload only"
    doc = release("engine", data)
    if failure == "oversize":
        doc["assets"][0]["size"] = 9999999999
    stub = tmp_path / "network-stub"
    stub.mkdir()
    flag = tmp_path / "cancel"
    # Replace the subprocess's standard HTTP transport before it imports the
    # service. No URL, including localhost, is actually contacted by this test.
    script = '''import io, json, http.client, urllib.request
from pathlib import Path
DOC = %r
PAYLOAD = %r
FAILURE = %r
FLAG = Path(%r)
class Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data); self.url=url; self.headers={"Content-Length":str(len(data))}
    def geturl(self): return self.url
    def read1(self, n):
        data=super().read1(n)
        if data and FAILURE=="cancel_stream" and "/releases/latest" not in self.url:
            FLAG.write_text("cancel")
        return data
def transport(self, request, timeout=15):
    if FAILURE=="offline": raise http.client.RemoteDisconnected("SECRET transport detail")
    data=json.dumps(DOC).encode() if "/releases/latest" in request.full_url else PAYLOAD
    return Response(data,request.full_url)
urllib.request.OpenerDirector.open=transport
''' % (doc, data, failure, str(flag))
    (stub / "sitecustomize.py").write_text(script, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(stub))
    destination = tmp_path / "downloads"
    result = subprocess.run([sys.executable, "-m", "kilnlib.updates", "download", "--product", "engine",
                             "--destination", str(destination), "--cancel-file", str(flag)],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]["code"] == code
    assert "Traceback" not in result.stderr and "SECRET" not in result.stdout + result.stderr
    assert not list(destination.glob("*.zip"))
    assert not list(destination.glob("*.part"))
