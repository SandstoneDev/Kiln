"""Selective updates use synthetic manifests and data; no game files are needed."""
from pathlib import Path
from types import SimpleNamespace

import pytest

import kiln
from kilnlib import steps as S
from kilnlib.manifest import Manifest
from kilnlib.pipeline import Ctx, select
import test_kiln as fixtures


@pytest.fixture
def ctx(tmp_path):
    a = kiln.parse(["--game", "synthetic", "--out", str(tmp_path), "--update", "--json"])
    client = SimpleNamespace(locale="enUS", secondary=[], build=1, sha256="synthetic")
    return Ctx(a, client, kiln.KILN_ROOT)


def step(name, group="models"):
    return S.Step(name, group, name, [], [], ["data/" + name + ".pak"])


def record(ctx, st):
    man = Manifest(ctx.out, ctx.data)
    path = Path(ctx.out, "data", st.name + ".pak")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"synthetic pack")
    man.record(st.name, status="ok", key=ctx.step_key(st), identity=ctx.step_identity(st),
               outputs=["data/" + st.name + ".pak"], rc=0, seconds=1)
    man.save()
    return man


def chosen(ctx, available):
    narrow, _ = kiln.prepare_mode(ctx, None)
    return narrow(select(available, ctx.a.steps, set()))


def test_explicit_update_leaves_unselected_changed_recipe_alone(ctx):
    a, b = step("alpha"), step("beta")
    record(ctx, a)
    record(ctx, b)
    a.run = [["changed-a"]]
    b.run = [["changed-b"]]
    ctx.a.steps = "alpha"
    ctx.a.steps_explicit = True
    assert [s.name for s in chosen(ctx, [a, b])] == ["alpha"]


def test_explicit_update_includes_new_selected_recipe(ctx):
    record(ctx, step("alpha"))
    ctx.a.steps = "beta"
    ctx.a.steps_explicit = True
    assert [s.name for s in chosen(ctx, [step("alpha"), step("beta")])] == ["beta"]


def test_default_update_adds_only_installed_groups_and_map_suffixes(ctx, monkeypatch):
    alpha, beta, ui = step("alpha"), step("beta"), step("panel", "interface")
    monkeypatch.setattr(S, "static_steps", lambda _path: [alpha, beta, ui])
    record(ctx, alpha)
    record(ctx, step("terrain_regiona", "maps:continents"))
    available = [alpha, beta, ui, step("terrain_regiona", "maps:continents"),
                 step("doodads_regiona", "maps:continents"), step("terrain_regionb", "maps:continents")]
    assert [s.name for s in chosen(ctx, available)] == ["beta", "doodads_regiona"]


def test_selected_current_recipe_stays_noop(ctx):
    alpha = step("alpha")
    record(ctx, alpha)
    ctx.a.steps, ctx.a.steps_explicit = "alpha", True
    assert chosen(ctx, [alpha]) == []


def test_explicit_map_group_does_not_expand_to_new_regions(ctx):
    record(ctx, step("terrain_regiona", "maps:continents"))
    ctx.a.steps, ctx.a.steps_explicit = "continents", True
    available = [step("doodads_regiona", "maps:continents"), step("doodads_regionb", "maps:continents")]
    assert [s.name for s in chosen(ctx, available)] == ["doodads_regiona"]
    ctx.a.steps, ctx.a.steps_explicit = "doodads_regionb", True
    assert [s.name for s in chosen(ctx, available)] == ["doodads_regionb"]


def test_definition_key_covers_input_and_output_recipe_metadata(ctx):
    import copy
    original = step("alpha")
    old = ctx.step_key(original)
    for field, value in (("outputs", ["data/new.pak"]), ("prefixes", ["interface\\"]),
                         ("clean", ["data/old_*.pak"]),
                         ("probes", [S.Probe([], "{L}/new.txt", target="XG", builtin="wdt")])):
        changed = copy.deepcopy(original)
        setattr(changed, field, value)
        assert ctx.step_key(changed) != old, field


@pytest.fixture
def cli():
    case = fixtures.CheckUpdateTest("test_modes")
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()


@pytest.mark.parametrize("payload", [b"{broken", b'{"schema":99,"steps":{}}',
                                     b'{"schema":1,"steps":[]}',
                                     b'{"schema":1,"steps":{"alpha":{"identity":[]}}}',
                                     b'{"schema":1,"steps":{},"locales":[]}'])
def test_invalid_manifest_is_error_and_is_not_overwritten(cli, payload):
    path = Path(cli.out, "data/kiln.manifest.json")
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    rc, events = cli.kiln("--update", "--steps", "profile")
    assert rc == 2
    assert "manifest" in events[-1]["message"].lower()
    assert path.read_bytes() == payload


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_plan_update_readonly_includes_reasons_and_current_steps(cli):
    assert cli.kiln("--steps", "profile")[0] == 0
    before = snapshot(Path(cli.tmp.name))
    try:
        rc, events = cli.kiln("--plan-update", "--steps", "profile", "--server", "localhost")
    except SystemExit:
        pytest.fail("--plan-update must be accepted by the CLI")
    assert rc == 0
    plan = next(e for e in events if e["event"] == "plan")
    assert plan["read_only"] and plan["update"]
    assert [(s["name"], s["state"], s["reason"], s["selected"]) for s in plan["steps"]] == [
        ("profile", "up-to-date", None, False)]
    assert snapshot(Path(cli.tmp.name)) == before


def test_plan_update_without_work_keeps_recorded_map_visible(cli):
    assert cli.kiln("--steps", "profile")[0] == 0
    man = cli.manifest()
    man["steps"]["terrain_regiona"] = {"status": "ok", "key": "old", "outputs": [], "identity": {}}
    cli.manifest(man)
    before = snapshot(Path(cli.tmp.name))
    try:
        rc, events = cli.kiln("--plan-update", "--steps", "terrain_regiona")
    except SystemExit:
        pytest.fail("--plan-update must be accepted by the CLI")
    plan = next(e for e in events if e["event"] == "plan")
    assert rc == 0
    assert [(s["name"], s["state"], s["deferred"]) for s in plan["steps"]] == [
        ("terrain_regiona", "needs-work", True)]
    assert snapshot(Path(cli.tmp.name)) == before


def test_legacy_definition_key_does_not_force_unchanged_rebake(cli):
    assert cli.kiln("--steps", "profile")[0] == 0
    man = cli.manifest()
    # v0.1.0 definition key for a build-1 builtin with no commands or params.
    man["steps"]["profile"]["key"] = "c87c56d3772144965fbd0731bb0405eabdfe9ac3d327cdcb13837765f71caf11"
    man["steps"]["profile"].pop("key_version", None)
    cli.manifest(man)
    rc, events = cli.kiln("--update", "--steps", "profile")
    assert rc == 0
    assert not [e for e in events if e["event"] in ("update", "step")]


def test_plan_without_work_shows_new_recipes_for_installed_region(cli):
    assert cli.kiln("--steps", "profile")[0] == 0
    man = cli.manifest()
    man["steps"]["terrain_regiona"] = {"status": "ok", "key": "old", "outputs": [], "identity": {}}
    cli.manifest(man)
    before = snapshot(Path(cli.tmp.name))
    rc, events = cli.kiln("--plan-update", "--steps", "continents")
    rows = next(e["steps"] for e in events if e["event"] == "plan")
    assert rc == 0
    assert {s["name"] for s in rows} == {"terrain_regiona", "doodads_regiona", "minimap_regiona", "terrain_holes_regiona"}
    assert all(s["deferred"] and s["selected"] for s in rows)
    assert snapshot(Path(cli.tmp.name)) == before


def test_actual_update_runs_new_selected_recipe_only(cli, monkeypatch):
    assert cli.kiln("--steps", "profile")[0] == 0
    before = cli.manifest()["steps"]["profile"]
    original = S.static_steps
    monkeypatch.setattr(S, "static_steps", lambda path: original(path) + [
        S.Step("extra_profile", "tables", "synthetic profile", [], [],
               ["data/expansions/*/*"], builtin="profile")])
    rc, events = cli.kiln("--update", "--steps", "extra_profile")
    assert rc == 0
    assert [e["name"] for e in events if e["event"] == "step" and e["state"] == "ok"] == ["extra_profile"]
    assert cli.manifest()["steps"]["profile"] == before


def test_missing_manifest_plan_fails_without_creating_output(cli):
    rc, events = cli.kiln("--plan-update")
    assert rc == 2
    assert "manifest" in events[-1]["message"].lower()
    assert not Path(cli.out).exists()
