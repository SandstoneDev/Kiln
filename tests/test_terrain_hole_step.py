"""The lightweight hole recipe belongs to installed map update scope."""
from pathlib import Path
from types import SimpleNamespace

from kilnlib import mapdb, steps
from kilnlib.pipeline import Ctx
from kilnlib.manifest import Manifest
import kiln


def test_hole_recipe_uses_only_selected_map_inputs():
    m = mapdb.MapInfo(0, "RegionA", 0, 1, wdt=True, tiles=[(3, 7)])
    recipes = steps.map_steps("continents", m)
    holes = [s for s in recipes if s.name == "terrain_holes_regiona"]
    assert len(holes) == 1
    st = holes[0]
    assert st.prefixes == ["world\\maps\\regiona\\"]
    assert st.programs() == ["@py:build_terrain_hole_overrides.py"]
    assert st.run == [["@py:build_terrain_hole_overrides.py", "--extracted", "{X}",
                       "--map", "{map}", "--out", "{D}"]]
    assert st.outputs == ["data/terrain_holes_{lower}.pak"]
    assert not st.png


def test_hole_recipe_map_name_uses_longest_prefix():
    assert kiln._map_name("terrain_holes_regiona") == "regiona"


def test_old_installed_map_plan_offers_new_hole_recipe(tmp_path):
    import contextlib
    import io
    import json
    a = kiln.parse(["--game", "synthetic", "--out", str(tmp_path), "--plan-update", "--json"])
    cl = SimpleNamespace(locale="enUS", secondary=[], build=1, sha256="synthetic")
    ctx = Ctx(a, cl, kiln.KILN_ROOT)
    man = Manifest(ctx.out, ctx.data)
    man.doc["steps"]["terrain_regiona"] = {"status": "ok", "key": "old", "outputs": []}
    man.save()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert kiln.plan_update(ctx) == 0
    plan = next(json.loads(line) for line in buf.getvalue().splitlines() if json.loads(line)["event"] == "plan")
    row = next((s for s in plan["steps"] if s["name"] == "terrain_holes_regiona"), None)
    assert row is not None
    assert row["selected"] and row["deferred"]
    assert not any("holes_holes" in s["name"] for s in plan["steps"])


def test_exported_builder_writes_runtime_valid_empty_map(tmp_path):
    import struct
    import subprocess
    import sys
    script = Path("bakers/tools/psp/build_terrain_hole_overrides.py").resolve()
    assert script.is_file()
    root, out = tmp_path / "input", tmp_path / "out"
    (root / "world/maps/regiona").mkdir(parents=True)
    run = subprocess.run([sys.executable, str(script), "--extracted", str(root),
                          "--map", "RegionA", "--out", str(out)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    blob = (out / "terrain_holes_regiona.pak").read_bytes()
    assert struct.unpack_from("<4sIII", blob) == (b"WPAK", 1, 1, 16)
    name, offset, size, kind, _ = struct.unpack_from("<48sIIII", blob, 16)
    assert (name.rstrip(b"\0"), size, kind) == (b"_empty", 512, 3)
    assert blob[offset:offset + size] == bytes(512)
