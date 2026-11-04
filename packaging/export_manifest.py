"""Regenerate or check the manifest of Git-tracked baker files."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BAKERS = ROOT / "bakers"
MANIFEST = BAKERS / "EXPORT_MANIFEST.json"


def source_digest(path):
    data = path.read_bytes()
    if b"\x00" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    previous = json.loads(MANIFEST.read_bytes())
    paths = subprocess.check_output(["git", "ls-files", "-z", "--", "bakers"], cwd=ROOT)
    files = {}
    for name in sorted(set(paths.decode("utf-8").split("\0")) - {"", "bakers/EXPORT_MANIFEST.json"}):
        path = Path(name).relative_to("bakers").as_posix()
        files[path] = {
            "sha256": source_digest(BAKERS / path),
            "why": previous["files"].get(path, {}).get("why", "Tracked Kiln baker source or dependency"),
        }
    if args.check:
        if files != previous["files"]:
            raise SystemExit("Baker manifest differs from tracked source. Run packaging/export_manifest.py.")
        print(f"Verified {len(files)} baker files.")
    else:
        previous["files"] = files
        previous["local_changes"] = "Includes the tracked baker tree and converter dependencies. UTF-8 text hashes use LF line endings."
        MANIFEST.write_bytes((json.dumps(previous, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
