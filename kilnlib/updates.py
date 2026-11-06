"""Explicit official release checks/downloads and reversible PSP engine updates.

Importing this module never contacts the network. Kiln archives are downloaded
only; this service never launches executables or replaces the running program.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib

from . import KILN_VERSION

PRODUCTS = {"kiln": ("Kiln", "Kiln-{version}-win64.zip", 256 << 20),
            "engine": ("WoWee-psp-port", "WoWeePSP-{version}.zip", 64 << 20)}
HOSTS = frozenset({"api.github.com", "github.com", "release-assets.githubusercontent.com",
                   "objects.githubusercontent.com"})
ART_SECTIONS = ("ICON0.PNG", "ICON1.PMF", "PIC0.PNG", "PIC1.PNG", "SND0.AT3")
MAX_PBP = 32 << 20
MAX_EXPANDED = 64 << 20
TIMEOUT = 15
DOWNLOAD_SECONDS = 300
VERSION = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z")
MIN_MODEL_ENGINE_VERSION = "0.2.0"


class UpdateError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _version(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise UpdateError("invalid_version", "A stable X.Y.Z release version is required.")
    return tuple(int(part) for part in value.split("."))


def _product(product):
    if product not in PRODUCTS:
        raise UpdateError("invalid_product", "Choose the kiln or engine product.")
    return PRODUCTS[product]


def _url(url):
    if not isinstance(url, str):
        raise UpdateError("untrusted_url", "The release URL is not an official HTTPS URL.")
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname in HOSTS
                 and parsed.port in (None, 443) and parsed.username is None
                 and parsed.password is None and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid or any(ord(c) < 32 for c in url):
        raise UpdateError("untrusted_url", "The release URL is not an official HTTPS URL.")
    return url


def _initial_kind(url):
    _url(url)
    for _product_name, (repo, pattern, _maximum) in PRODUCTS.items():
        if url == f"https://api.github.com/repos/SandstoneDev/{repo}/releases/latest":
            return "metadata"
        prefix = f"https://github.com/SandstoneDev/{repo}/releases/download/v"
        if url.startswith(prefix):
            rest = url[len(prefix):].split("/")
            if len(rest) == 2:
                version, name = rest
                _version(version)
                if name == pattern.format(version=version):
                    return "asset"
    raise UpdateError("untrusted_url", "Only the two official release endpoints may be requested.")


def _bound_url(initial, final, *, redirect=False):
    kind = _initial_kind(initial)
    _url(final)
    if kind == "metadata":
        if redirect or final != initial:
            raise UpdateError("untrusted_redirect", "Release metadata redirects are not permitted.")
        return
    if final == initial:
        return
    parsed = urllib.parse.urlsplit(final)
    if (parsed.hostname in {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
            and parsed.path.startswith("/github-production-release-asset")):
        return
    raise UpdateError("untrusted_redirect", "The download redirected outside this official release asset.")


class OfficialRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, initial=None):
        super().__init__()
        self.initial = initial

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        initial = self.initial or req.full_url
        _bound_url(initial, req.full_url)
        _bound_url(initial, newurl, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _cancel(cancel):
    if cancel and cancel():
        raise UpdateError("cancelled", "The release operation was cancelled.")


def _response(url, opener=None):
    _bound_url(url, url)
    request = urllib.request.Request(_url(url), headers={
        "User-Agent": "Kiln/" + KILN_VERSION,
        "Accept": "application/vnd.github+json" if "api.github.com/" in url else "application/octet-stream",
    })
    try:
        if opener is None:
            opener = urllib.request.build_opener(OfficialRedirectHandler(url)).open
        response = opener(request, timeout=TIMEOUT)
        try:
            _bound_url(url, response.geturl())
        except UpdateError:
            response.close()
            raise
        return response
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise UpdateError("rate_limited", "GitHub is rate limiting this request; retry later.") from None
        raise UpdateError("network", "The official release is unavailable.") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        raise UpdateError("offline", "The official release service is unavailable; check the connection.") from None


def _read_chunk(response):
    # HTTPResponse.read1 performs at most one underlying socket read. read(n)
    # can keep filling a block indefinitely on a slowly progressing connection.
    return getattr(response, "read1", response.read)(65536)


def _metadata_bytes(response, cancel):
    deadline, result = time.monotonic() + 30, bytearray()
    while True:
        _cancel(cancel)
        if time.monotonic() > deadline:
            raise UpdateError("timeout", "The release check exceeded its time limit.")
        chunk = _read_chunk(response)
        if not chunk:
            return bytes(result)
        result.extend(chunk)
        if len(result) > 2 << 20:
            raise UpdateError("invalid_release", "The official release metadata exceeds its size limit.")


def _descriptor(release, product):
    repo, pattern, maximum = _product(product)
    try:
        version = release["version"]
        _version(version)
        name = pattern.format(version=version)
        base = f"https://github.com/SandstoneDev/{repo}/releases"
        asset = release["asset"]
        if (release["product"] != product or release["release_url"] != base + "/tag/v" + version
                or asset["name"] != name or asset["url"] != base + "/download/v" + version + "/" + name
                or type(asset["size"]) is not int or not 0 < asset["size"] <= maximum
                or not isinstance(asset["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])):
            raise ValueError("invalid release descriptor")
    except (KeyError, TypeError, ValueError):
        raise UpdateError("invalid_release", "The release descriptor is not an official bounded asset.") from None
    return release


def fetch_release(product, *, opener=None, cancel=None):
    """Return validated metadata for one official latest stable release."""
    repo, pattern, maximum = _product(product)
    _cancel(cancel)
    endpoint = f"https://api.github.com/repos/SandstoneDev/{repo}/releases/latest"
    try:
        with _response(endpoint, opener) as response:
            raw = _metadata_bytes(response, cancel)
        doc = json.loads(raw)
        tag = doc["tag_name"]
        if not isinstance(tag, str) or not tag.startswith("v") or doc.get("draft") or doc.get("prerelease"):
            raise ValueError("not stable")
        version = tag[1:]
        _version(version)
        name = pattern.format(version=version)
        release_url = f"https://github.com/SandstoneDev/{repo}/releases/tag/{tag}"
        url = f"https://github.com/SandstoneDev/{repo}/releases/download/{tag}/{name}"
        if doc["html_url"] != release_url:
            raise ValueError("unexpected release URL")
        assets = [asset for asset in doc["assets"] if asset.get("name") == name]
        if len(assets) != 1:
            raise ValueError("missing or duplicate asset")
        asset = assets[0]
        size, digest = asset["size"], asset.get("digest")
        if type(size) is not int or not 0 < size <= maximum or asset["browser_download_url"] != url:
            raise ValueError("unexpected asset")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise UpdateError("digest_unavailable", "GitHub has no valid SHA-256 digest for this release asset.")
        _cancel(cancel)
        return {"product": product, "version": version, "release_url": release_url,
                "asset": {"name": name, "size": size, "url": url, "sha256": digest[7:].lower()}}
    except UpdateError:
        raise
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise UpdateError("invalid_release", "GitHub returned invalid official release metadata.") from None
    except (OSError, TimeoutError, http.client.HTTPException):
        raise UpdateError("offline", "The official release service is unavailable; retry later.") from None


def _plain_path(path):
    """Reject existing symlink/junction ancestors before any write."""
    path = Path(os.path.abspath(os.fspath(path)))
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise UpdateError("unsafe_path", "Update paths cannot contain links or junctions.")
        try:
            if getattr(ancestor.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise UpdateError("unsafe_path", "Update paths cannot contain reparse points.")
        except FileNotFoundError:
            pass
        except NotADirectoryError:
            raise UpdateError("unsafe_path", "An update path has a non-directory parent.") from None
        if ancestor != path and ancestor.exists() and not ancestor.is_dir():
            raise UpdateError("unsafe_path", "An update path has a non-directory parent.")
    return path


def _pbp_data(path):
    path = _plain_path(path)
    if not path.is_file():
        raise ValueError("invalid EBOOT size")
    with path.open("rb") as f:
        data = f.read(MAX_PBP + 1)
    if not 40 <= len(data) <= MAX_PBP:
        raise ValueError("invalid EBOOT size")
    return data


def _pbp(path):
    from .xmb import read_pbp
    return read_pbp(_pbp_data(path))


def _sfo_version(data):
    if len(data) < 20:
        raise ValueError("short SFO")
    magic, _fmt, keys, values, count = struct.unpack_from("<4sIIII", data)
    if magic != b"\0PSF" or count > 256 or not 20 + count * 16 <= keys <= values <= len(data):
        raise ValueError("invalid SFO tables")
    found = []
    for i in range(count):
        key, fmt, size, capacity, offset = struct.unpack_from("<HHIII", data, 20 + i * 16)
        start = keys + key
        end = data.find(b"\0", start, values)
        if not keys <= start < values or end < 0 or size > capacity or values + offset + capacity > len(data):
            raise ValueError("invalid SFO entry")
        if data[start:end] == b"APP_VER":
            raw = data[values + offset:values + offset + size]
            if fmt != 0x204 or not raw.endswith(b"\0"):
                raise ValueError("invalid APP_VER")
            version = raw[:-1].decode("ascii")
            _version(version)
            found.append(version)
    if len(found) != 1:
        raise ValueError("missing or duplicate APP_VER")
    return found[0]


def engine_version(path_or_target):
    """Local-only APP_VER, or None for a missing/unknown/corrupt engine."""
    path = Path(path_or_target)
    if path.is_dir():
        path /= "EBOOT.PBP"
    try:
        return _sfo_version(_pbp(path)[1]["PARAM.SFO"])
    except (OSError, ValueError, KeyError, UpdateError):
        return None


def should_replace_engine(source_eboot, target_eboot):
    """Local copy guard: preserve newer engines and different same-version builds."""
    try:
        source_eboot, target_eboot = _plain_path(source_eboot), _plain_path(target_eboot)
    except (OSError, UpdateError):
        return False
    source = engine_version(source_eboot)
    if source is None:
        return False
    target = Path(target_eboot)
    if not target.exists():
        return True
    installed = engine_version(target)
    if installed is None or _version(source) < _version(installed):
        return False
    if source != installed:
        return True
    from .xmb import executable_identity
    try:
        return executable_identity(os.fspath(source_eboot)) == executable_identity(os.fspath(target_eboot))
    except (OSError, ValueError):
        return False


def check_updates(target=None, *, opener=None, cancel=None):
    result = {}
    for product in PRODUCTS:
        release = fetch_release(product, opener=opener, cancel=cancel)
        current = KILN_VERSION if product == "kiln" else engine_version(target) if target else None
        result[product] = {"current": current, "latest": release["version"],
                           "update_available": current is not None and _version(current) < _version(release["version"]),
                           "asset": release["asset"], "release_url": release["release_url"]}
    return result


def _hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def download_release(product, destination, *, opener=None, cancel=None, release=None):
    """Download a verified official archive to a PC directory; never execute it."""
    _product(product)
    release = _descriptor(release or fetch_release(product, opener=opener, cancel=cancel), product)
    asset = release["asset"]
    destination = _plain_path(destination)
    output = _plain_path(destination / asset["name"])
    _cancel(cancel)
    if output.exists():
        if output.is_file() and output.stat().st_size == asset["size"] and _hash_file(output) == asset["sha256"]:
            return dict(product=product, version=release["version"], path=str(output),
                        sha256=asset["sha256"], size=asset["size"], release_url=release["release_url"])
        raise UpdateError("file_exists", "The download filename already exists with different contents.")
    temp = None
    try:
        destination.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + DOWNLOAD_SECONDS
        with _response(asset["url"], opener) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != asset["size"]:
                raise UpdateError("integrity", "The release download size does not match GitHub metadata.")
            with tempfile.NamedTemporaryFile(prefix=".kiln-download-", suffix=".part", dir=destination, delete=False) as f:
                temp = Path(f.name)
                digest, received = hashlib.sha256(), 0
                while True:
                    _cancel(cancel)
                    if time.monotonic() > deadline:
                        raise UpdateError("timeout", "The release download exceeded its time limit.")
                    block = _read_chunk(response)
                    if not block:
                        break
                    received += len(block)
                    if received > asset["size"]:
                        raise UpdateError("integrity", "The release download exceeds its declared size.")
                    digest.update(block)
                    f.write(block)
                f.flush()
                os.fsync(f.fileno())
        _cancel(cancel)
        if received != asset["size"] or digest.hexdigest() != asset["sha256"]:
            raise UpdateError("integrity", "The release download failed its size or SHA-256 check.")
        # Windows rename refuses an existing destination, including on FAT32.
        # POSIX link provides the same no-clobber publication semantics.
        if os.name == "nt":
            os.rename(temp, output)
        else:
            os.link(temp, output)
        return dict(product=product, version=release["version"], path=str(output),
                    sha256=asset["sha256"], size=received, release_url=release["release_url"])
    except UpdateError:
        raise
    except (OSError, ValueError, TimeoutError, http.client.HTTPException):
        raise UpdateError("download_failed", "The release could not be downloaded safely; retry or choose another directory.") from None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _zip_name(info):
    name = info.filename
    if name != info.orig_filename or "\\" in name or name.startswith("/"):
        raise ValueError("unsafe archive path")
    parts = name.rstrip("/").split("/")
    reserved = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.I)
    if not parts or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) or p in (".", "..")
                        or p.endswith((".", " ")) or reserved.fullmatch(p) for p in parts):
        raise ValueError("unsafe archive component")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if (kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.external_attr & 0x400
            or info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
        raise ValueError("unsupported archive entry")
    return "/".join(parts)


def _runtime_name(relative):
    parts = relative.split("/")
    folded = relative.casefold()
    if folded == "eboot.pbp":
        return "EBOOT.PBP"
    if folded == "sdbg.prx":
        return "sdbg.prx"
    for prefix in ("Data/expansions/wotlk/", "Data/opcodes/"):
        if folded.startswith(prefix.casefold()) and len(relative) > len(prefix):
            if not folded.endswith(".json"):
                raise ValueError("runtime profiles must be JSON tables")
            return prefix + "/".join(parts[len(prefix.rstrip("/").split("/")):])
    return None


def _archive_bytes(archive, expected_asset=None):
    try:
        with open(archive, "rb") as f:
            data = f.read((64 << 20) + 1)
        if len(data) > 64 << 20:
            raise ValueError("archive too large")
        if expected_asset is not None:
            if (type(expected_asset["size"]) is not int or len(data) != expected_asset["size"]
                    or hashlib.sha256(data).hexdigest() != expected_asset["sha256"]):
                raise ValueError("archive changed")
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise UpdateError("integrity", "The engine archive no longer matches its verified size and SHA-256.") from None


def _engine_archive(archive, expected_version, old_sections):
    from .xmb import read_pbp, write_pbp
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            infos = z.infolist()
            if len(infos) > 4096:
                raise ValueError("too many entries")
            names, total = {}, 0
            for info in infos:
                name = _zip_name(info)
                key = name.casefold()
                if key in names:
                    raise ValueError("duplicate archive path")
                names[key] = info
                total += info.file_size
                if (info.file_size > MAX_PBP or total > MAX_EXPANDED
                        or info.file_size > max(info.compress_size, 1) * 200):
                    raise ValueError("archive expansion limit")
            for key in names:
                for parent in PurePosixPath(key).parents:
                    if str(parent) in names and not names[str(parent)].is_dir():
                        raise ValueError("file/directory collision")
            eboots = [key for key in names if key.split("/")[-1] == "eboot.pbp"]
            if len(eboots) != 1 or eboots[0] not in ("eboot.pbp", "woweepsp/eboot.pbp"):
                raise ValueError("ambiguous engine root")
            root = "woweepsp/" if eboots[0].startswith("woweepsp/") else ""
            for name, info in names.items():
                if info.is_dir():
                    continue
                relative = info.filename[len(root):] if name.startswith(root) else ""
                runtime = _runtime_name(relative)
                if runtime:
                    files[runtime] = z.read(info)
                    if runtime.startswith("Data/"):
                        if not isinstance(json.loads(files[runtime]), (dict, list)):
                            raise ValueError("runtime table must contain a JSON object or array")
                else:
                    # CRC/decompression validation covers ignored files too.
                    with z.open(info) as f:
                        while f.read(65536):
                            pass
            version, sections = read_pbp(files["EBOOT.PBP"])
            if _sfo_version(sections["PARAM.SFO"]) != expected_version or not sections["DATA.PSP"]:
                raise ValueError("release EBOOT version mismatch")
            if old_sections is not None:
                for name in ART_SECTIONS:
                    sections[name] = old_sections[name]
            files["EBOOT.PBP"] = write_pbp(version, sections)
        return files
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, NotImplementedError, RuntimeError, zlib.error, EOFError):
        raise UpdateError("invalid_archive", "The engine archive is invalid or contains unsafe entries.") from None


def _target(target):
    target = _plain_path(target)
    current = engine_version(target)
    if not target.is_dir() or current is None:
        raise UpdateError("invalid_target", "Choose an existing engine installation with a valid APP_VER.")
    return target, current


def _runtime_directories(parent, target, created):
    missing = []
    while parent != target and not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        try:
            directory.mkdir()
            created.append(directory)
        except FileExistsError:
            if not directory.is_dir():
                raise


def install_engine(archive, target, version, *, cancel=None, replace=None, expected_asset=None, initialize=False):
    """Validate/stage first; back up every replacement and roll back failures."""
    from .xmb import read_pbp
    target = _plain_path(target)
    old_blob, old_sections, current = None, None, None
    try:
        # Version comparison, art preservation and the commit guard all refer
        # to one bounded snapshot of the installed engine.
        if initialize:
            if target.exists() and not target.is_dir():
                raise ValueError("not a directory")
            if (target / "EBOOT.PBP").exists():
                raise ValueError("an engine already exists")
        else:
            old_blob = _pbp_data(target / "EBOOT.PBP")
            _, old_sections = read_pbp(old_blob)
            current = _sfo_version(old_sections["PARAM.SFO"])
    except (OSError, ValueError, KeyError, UpdateError):
        raise UpdateError("invalid_target", "Choose an existing engine installation with a valid APP_VER.") from None
    _version(version)
    if current is not None and _version(version) <= _version(current):
        raise UpdateError("not_newer", "This release is not newer than the installed engine.")
    # Pin one bounded byte string: verification and ZIP parsing use the same
    # bytes even if the downloaded filename is concurrently replaced.
    files = _engine_archive(_archive_bytes(archive, expected_asset), version, old_sections)
    if initialize and ("sdbg.prx" not in files or
                       not any(n.startswith("Data/opcodes/") for n in files) or
                       not any(n.startswith("Data/expansions/wotlk/") for n in files)):
        raise UpdateError("invalid_archive", "The engine release is missing required runtime files.")
    for relative in files:
        destination = _plain_path(target / relative)
        if destination.exists() and not destination.is_file():
            raise UpdateError("unsafe_path", "A runtime destination is not a regular file.")
    backup_parent = _plain_path(target / ".kiln-backups")
    if backup_parent.exists() and not backup_parent.is_dir():
        raise UpdateError("unsafe_path", "The engine backup directory is not a directory.")
    replace = replace or os.replace
    with tempfile.TemporaryDirectory(prefix="kiln-engine-stage-", ignore_cleanup_errors=True) as temporary:
        stage = Path(temporary)
        for relative, data in files.items():
            staged = stage / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(data)
        _cancel(cancel)  # cancellation never interrupts the commit/rollback transaction
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
        backup = backup_parent / stamp
        changed, originals, created_dirs = [], set(), []
        lock = target / ".kiln-update.lock"
        try:
            target.mkdir(parents=True, exist_ok=True)
            with lock.open("xb") as f:
                f.write(stamp.encode("ascii"))
        except OSError:
            raise UpdateError("busy", "Another engine update may be active; the installation was not changed.") from None
        try:
            if ((initialize and (target / "EBOOT.PBP").exists()) or
                    (not initialize and _pbp_data(target / "EBOOT.PBP") != old_blob)):
                raise UpdateError("target_changed", "The selected engine changed while preparing the update.")
            backup.mkdir(parents=True)
            for relative in files:
                destination = target / relative
                if destination.exists():
                    original = backup / relative
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, original)
                    originals.add(relative)
            # Install the EBOOT last, after all supporting runtime tables.
            order = sorted(files, key=lambda name: (name == "EBOOT.PBP", name))
            for relative in order:
                destination = target / relative
                _runtime_directories(destination.parent, target, created_dirs)
                # A temp sibling supports atomic replacement on removable drives.
                pending = destination.with_name(destination.name + ".kiln-" + uuid.uuid4().hex + ".tmp")
                try:
                    shutil.copyfile(stage / relative, pending)
                    replace(pending, destination)
                    changed.append(relative)
                finally:
                    pending.unlink(missing_ok=True)
            (backup / "receipt.json").write_text(json.dumps({"previous_version": current, "version": version,
                "installed": order, "created": sorted(set(files) - originals)}, indent=2), encoding="utf-8")
            lock.unlink()  # a cleanup error is still inside the rollback boundary
            return {"target": str(target), "previous_version": current, "version": version,
                    "backup": str(backup), "installed": order}
        except BaseException as exc:
            try:
                for relative in reversed(changed):
                    destination = target / relative
                    if relative in originals:
                        restore = destination.with_name(destination.name + ".kiln-restore-" + uuid.uuid4().hex)
                        shutil.copy2(backup / relative, restore)
                        os.replace(restore, destination)
                    else:
                        created = backup / "created" / relative
                        created.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(destination, created)
                for directory in reversed(created_dirs):
                    try:
                        directory.rmdir()  # only empty directories created by this transaction
                    except OSError:
                        pass  # preserve any concurrently added owner files
            except OSError:
                raise UpdateError("rollback_failed", "Engine update failed; recovery files remain in " + str(backup)) from None
            if isinstance(exc, UpdateError):
                raise
            if isinstance(exc, KeyboardInterrupt):
                raise UpdateError("cancelled", "Engine update was interrupted and rolled back; backup: " + str(backup)) from None
            raise UpdateError("install_failed", "Engine update failed and was rolled back; backup: " + str(backup)) from None
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass  # never hide a completed rollback/recovery error


def update_engine(destination, target, *, opener=None, cancel=None):
    target, current = _target(target)  # fail closed before making a request
    release = fetch_release("engine", opener=opener, cancel=cancel)
    if _version(release["version"]) <= _version(current):
        raise UpdateError("not_newer", "This release is not newer than the installed engine.")
    downloaded = download_release("engine", destination, opener=opener, cancel=cancel, release=release)
    return {**downloaded, **install_engine(downloaded["path"], target, release["version"],
                                           cancel=cancel, expected_asset=release["asset"])}


def require_model_engine(path):
    """Reject an old/invalid selected engine before writing the new model formats."""
    current = engine_version(path)
    if current is None or _version(current) < _version(MIN_MODEL_ENGINE_VERSION):
        raise UpdateError("engine_required", "Engine %s or newer is required for characters/creatures. "
                          "Update the installed engine before converting new model data."
                          % MIN_MODEL_ENGINE_VERSION)
    return current


def ensure_engine(destination, target, *, opener=None, cancel=None):
    """Keep a compatible local build; install a verified compatible release otherwise."""
    _cancel(cancel)
    target = _plain_path(target)
    if target.exists() and not target.is_dir():
        raise UpdateError("invalid_target", "Choose a folder for the engine installation.")
    existing = (target / "EBOOT.PBP").exists()
    if existing:
        current = engine_version(target)
        if current is None:
            raise UpdateError("invalid_target", "The existing EBOOT.PBP is invalid; choose another installation folder.")
        if _version(current) >= _version(MIN_MODEL_ENGINE_VERSION):
            return {"target": str(target), "version": current, "status": "kept"}
    try:
        release = fetch_release("engine", opener=opener, cancel=cancel)
        if _version(release["version"]) < _version(MIN_MODEL_ENGINE_VERSION):
            raise UpdateError("engine_required", "The official release is too old for these model formats.")
        downloaded = download_release("engine", destination, opener=opener, cancel=cancel, release=release)
    except UpdateError as exc:
        if exc.code == "cancelled":
            raise
        raise UpdateError("engine_required", "Engine %s or newer is required before resource conversion. "
                          "A compatible official release could not be obtained: %s"
                          % (MIN_MODEL_ENGINE_VERSION, exc)) from None
    installed = install_engine(downloaded["path"], target, release["version"], initialize=not existing,
                               cancel=cancel, expected_asset=release["asset"])
    return {**downloaded, **installed, "status": "installed"}


def finish_engine_art(target):
    """Publish local XMB artwork at the root only for the same engine executable."""
    target = _plain_path(target)
    source = _plain_path(target / "xmb" / "EBOOT.PBP")
    installed = _plain_path(target / "EBOOT.PBP")
    if not source.is_file() or not installed.is_file():
        return False
    from .xmb import executable_identity
    if not should_replace_engine(source, installed) or executable_identity(source) != executable_identity(installed):
        return False
    blob = _pbp_data(source)
    if blob == _pbp_data(installed):
        return False
    pending = installed.with_name(".kiln-art-" + uuid.uuid4().hex + ".tmp")
    try:
        pending.write_bytes(blob)
        os.replace(pending, installed)
    finally:
        pending.unlink(missing_ok=True)
    return True


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UpdateError("arguments", "Invalid update command arguments. Use --help for usage.")

    def print_help(self, file=None):
        print(json.dumps({"help": self.format_help()}), file=file or sys.stdout)


def main(argv=None):
    try:
        parser = _Parser(description=__doc__)
        commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
        check = commands.add_parser("check")
        check.add_argument("--target")
        check.add_argument("--cancel-file")
        download = commands.add_parser("download")
        download.add_argument("--product", choices=tuple(PRODUCTS), required=True)
        download.add_argument("--destination", required=True)
        download.add_argument("--cancel-file")
        engine = commands.add_parser("update-engine")
        engine.add_argument("--destination", required=True)
        engine.add_argument("--target", required=True)
        setup = commands.add_parser("ensure-engine")
        setup.add_argument("--destination", required=True)
        setup.add_argument("--target", required=True)
        setup.add_argument("--cancel-file")
        args = parser.parse_args(argv)
        flag = getattr(args, "cancel_file", None)
        cancel = (lambda: Path(flag).exists()) if flag else None
        if args.command == "check":
            result = check_updates(args.target, cancel=cancel)
        elif args.command == "download":
            result = download_release(args.product, args.destination, cancel=cancel)
        elif args.command == "ensure-engine":
            result = ensure_engine(args.destination, args.target, cancel=cancel)
        else:
            result = update_engine(args.destination, args.target)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except UpdateError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"error": {"code": "cancelled", "message": "The release operation was cancelled."}}))
        return 1
    except (OSError, ValueError, zipfile.BadZipFile):
        print(json.dumps({"error": {"code": "local_error", "message": "The update operation could not access a required local file."}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
