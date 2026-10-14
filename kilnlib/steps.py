"""The step table: every file of the PSP data folder, the client paths it needs, the command that
makes it, what it writes and how it is checked.

Command templates are lists of strings. The first element names the program:
    "@exe:<name>"   a converter executable from --tools (name + ".exe" on Windows)
    "@py:<script>"  a Python baker from <bakers>/tools/psp/
Placeholders:
    {X}   the extracted client tree of the step's locale   {XG}  extra tree for the scene textures
    {D}   the step's output folder (data/ or data/locale/<xxYY>/)
    {W}   the step's work folder                           {L}   its list folder (probe output)
    {jobs} --jobs                                          {loc} the step's locale
    {map} a map's directory as the client names it         {lower} the same in lower case
    {map0} directory of map id 0 (read from the client's map table at run time)
    {tiles} expands to "--tile x_y ..." from the map's WDT (or "--around 32 32 32")
    {?mirror} expands to "--mirror <dir>" when Kiln runs with --mirror, else to nothing
Outputs are glob patterns relative to the output folder.

Locales: a step marked locale="full" reads text or art that differs per client locale; it runs once
for the primary locale (written to data/) and once for every other locale present (written to
data/locale/<xxYY>/, from a tree extracted with that locale's archives). locale="variant" runs a
smaller command for the other locales (only the localized part). locale="names" means the step's
output is localized (zone names, and world map art with painted place names from the locale
archives) but too large to duplicate; it is baked for the primary locale only (listed in the plan).
"""
from __future__ import annotations

import copy
import glob
import os
from dataclasses import dataclass, field

# Client path prefixes (lower case, backslash: the extractor's own form).
P_DBC = ["dbfilesclient\\"]
P_UI = ["interface\\", "fonts\\"]
P_MODELS = ["character\\", "creature\\", "item\\", "spells\\", "particles\\", "textures\\", "environments\\",
            "interface\\framexml\\"]
P_WORLD = ["world\\", "tileset\\", "dungeons\\", "environments\\", "interiors\\", "textures\\", "xtextures\\",
           "spells\\", "particles\\", "creature\\", "character\\", "item\\", "misc\\", "interface\\worldmap\\"]
P_SOUND = ["sound\\character\\", "sound\\item\\weapons\\", "sound\\spells\\enterwater", "sound\\spell\\fishingbobber",
           "sound\\interface\\", "world\\maps\\"]
P_MUSIC = ["sound\\music\\", "sound\\ambience\\", "sound\\interface\\", "world\\maps\\"]

PNG_SUBDIRS = ["interface"]      # the Python bakers read interface art as PNG side files


@dataclass
class Probe:
    """A run that lists client files the archives' own listfiles do not name; Kiln then extracts
    exactly those paths (extractor --names) before the step proper runs."""
    cmd: list
    list_file: str                 # template, e.g. "{L}/glue_missing.txt"
    target: str = "X"              # extract into {X} or {XG}
    stdout: bool = False           # the list comes on stdout
    keep: str = ""                 # keep only lines starting with this ("" = path-like lines)
    builtin: str = ""              # "wdt": Kiln writes the list itself (every map's WDT, from the map table)


@dataclass
class Variant:
    """What a locale="variant" step runs for the other locales."""
    run: list
    outputs: list
    prefixes: list
    title: str
    verify: list = field(default_factory=list)


@dataclass
class Step:
    name: str
    group: str
    title: str
    prefixes: list
    run: list                      # list of command templates
    outputs: list                  # glob patterns relative to <out>
    verify: list = field(default_factory=list)
    png: bool = False
    probes: list = field(default_factory=list)
    clean: list = field(default_factory=list)      # globs removed before the step runs
    default: bool = True
    cwd: str = "{W}"
    params: dict = field(default_factory=dict)     # {map}, {lower}, {tiles}, {loc}, folder overrides
    builtin: str = ""              # a step done by Kiln itself instead of a command
    mkdirs: list = field(default_factory=list)     # folders (templates) created before the step runs
    locale: str = ""               # "", "full", "variant", "names" (see the module text)
    variant: Variant | None = None
    base: str = ""                 # the step this one was cloned from (locale copies)
    # Return codes of the run commands that mean "written and complete, with quality warnings" (the model
    # converters' --verify: 4 = some animation samples off by more than the limit; 3 stays a failure).
    warn_rc: list = field(default_factory=list)

    def programs(self):
        cmds = list(self.run) + list(self.verify) + [p.cmd for p in self.probes if p.cmd]
        return sorted({c[0] for c in cmds if c and c[0].startswith("@")})

    @property
    def base_name(self):
        return self.base or self.name


def _py(name, out, extra=(), verify=None, prefixes=None, png=True, title=None, default=True, group="interface",
        locale="full"):
    script = "build_%s_pak.py" % name
    v = [["@py:" + script, "--out", "{D}/%s" % out] + list(verify)] if verify else []
    return Step(name=name, group=group, title=title or "%s (Python baker %s)" % (out, script),
                prefixes=prefixes if prefixes is not None else P_DBC + P_UI,
                run=[["@py:" + script, "--extracted", "{X}", "--out", "{D}/%s" % out] + list(extra)],
                outputs=["data/" + out], verify=v, png=png, default=default, locale=locale)


# Python bakers with their own arguments; any other tools/psp/build_<name>_pak.py of the bakers is
# picked up with the defaults (interface art + client tables -> data/<name>.pak, localized).
PY_OVERRIDES = {
    "text": dict(prefixes=P_DBC, png=False, group="tables", title="text.pak: grammar and zone-name tables"),
    "craft": dict(prefixes=P_DBC + ["interface\\framexml\\"], png=False, group="tables",
                  title="craft.pak: trade skills"),
    "taxi": dict(extra=["--verify"], prefixes=P_DBC + ["interface\\taxiframe\\"], group="tables",
                 title="taxi.pak: flight paths"),
    "spells": dict(verify=["--verify"], prefixes=P_DBC + P_UI, group="tables",
                   title="spells.pak: spells, talents, skills"),
    "itemicons": dict(verify=["--verify"], prefixes=P_DBC + ["interface\\icons\\"], group="tables", locale="",
                      title="itemicons.pak: item icons"),
    "tutorial": dict(verify=["--verify"], prefixes=["interface\\framexml\\", "interface\\tutorialframe\\"],
                     title="tutorial.pak: tutorial texts and art"),
    "ui": dict(extra=["--no-glue"], prefixes=P_DBC + P_UI, title="ui.pak: interface art and fonts"),
    "strings": dict(extra=["--locale", "{loc}", "--verify"],
                    prefixes=P_DBC + ["interface\\framexml\\", "interface\\gluexml\\"], png=False, group="tables",
                    title="strings.pak: the client's interface strings"),
}
PY_SPECIAL = {"glue"}             # built by its own step below


def python_steps(bakers_dir):
    names = []
    for p in sorted(glob.glob(os.path.join(bakers_dir, "tools", "psp", "build_*_pak.py"))):
        n = os.path.basename(p)[len("build_"):-len("_pak.py")]
        if n not in PY_SPECIAL:
            names.append(n)
    if not names:                  # no bakers yet (dry run before the export): the known ones
        names = sorted(PY_OVERRIDES)
    return [_py(n, n + ".pak", **PY_OVERRIDES.get(n, {})) for n in names]


def static_steps(bakers_dir):
    s = []
    s.append(Step("profile", "tables", "expansions/<id>/: the protocol profile for the identified client build",
                  prefixes=[], run=[], outputs=["data/expansions/*/*"], builtin="profile"))
    s.append(Step("dbc", "tables", "dbc.pak: faction tables", prefixes=P_DBC,
                  run=[["@exe:psp_dbc_conv", "--extracted", "{X}", "--out", "{D}/dbc.pak", "--verify"]],
                  outputs=["data/dbc.pak"]))
    s.append(Step("light", "tables", "light.pak: zone light and fog for the four continents", prefixes=P_DBC,
                  run=[["@exe:psp_light_conv", "--extracted", "{X}", "--out", "{D}/light.pak",
                        "--map", "0", "--map", "1", "--map", "530", "--map", "571"]],
                  verify=[["@exe:psp_light_conv", "--verify", "{D}/light.pak", "--extracted", "{X}"]],
                  outputs=["data/light.pak"]))
    s += python_steps(bakers_dir)
    s.append(Step("glue", "interface", "glue.pak: login and character screens (scene pictures rendered here)",
                  prefixes=P_DBC + P_UI + ["character\\"], png=True, locale="full",
                  mkdirs=["{W}/glue_probe", "{W}/glue_scenes"],
                  probes=[Probe([], "{L}/wdt_names.txt", builtin="wdt"),
                          Probe(["@exe:psp_glue_scene_conv", "--extracted", "{X}", "--out", "{W}/glue_probe",
                                 "--list-missing", "{L}/glue_missing.txt"], "{L}/glue_missing.txt", target="XG")],
                  run=[["@exe:psp_glue_scene_conv", "--extracted", "{X}", "--extracted", "{XG}",
                        "--out", "{W}/glue_scenes", "--ss", "3"],
                       ["@py:build_glue_pak.py", "--extracted", "{X}", "--scenes", "{W}/glue_scenes",
                        "--out", "{D}/glue.pak"]],
                  outputs=["data/glue.pak"]))
    s.append(Step("framexml", "interface", "framexml.pak: interface scripts and layouts", prefixes=P_DBC + P_UI,
                  locale="full",
                  probes=[Probe(["@exe:psp_framexml_conv", "--print-prefixes"], "{L}/framexml_names.txt",
                                stdout=True)],
                  run=[["@exe:psp_framexml_conv", "--extracted", "{X}", "--out", "{D}/framexml.pak"]],
                  verify=[["@exe:psp_framexml_conv", "--verify", "{D}/framexml.pak"]],
                  outputs=["data/framexml.pak"]))
    s.append(Step("emblems", "interface", "emblems.pak: guild emblems", prefixes=P_DBC + ["textures\\guildemblems\\"],
                  run=[["@exe:psp_emblem_conv", "--extracted", "{X}", "--out", "{D}/emblems.pak", "--verify",
                        "--require-all"]],
                  outputs=["data/emblems.pak"]))
    s.append(Step("xmb_art", "interface", "xmb/ICON0.PNG + xmb/PIC1.PNG: menu icon and background from your client's art",
                  prefixes=P_DBC + ["interface\\glues\\"], run=[], outputs=["xmb/*.PNG", "xmb/EBOOT.PBP"],
                  builtin="xmb_art"))
    s.append(Step("sounds", "audio", "sounds.pak (+ sounds_N.pak parts): sound effects and voices",
                  prefixes=P_DBC + P_SOUND, locale="full",
                  probes=[Probe(["@exe:psp_sound_conv", "--extracted", "{X}", "--out", "{W}/sounds_probe.pak",
                                 "--list-missing"], "{L}/sounds_missing.txt", stdout=True, keep="missing ")],
                  run=[["@exe:psp_sound_conv", "--extracted", "{X}", "--out", "{D}/sounds.pak", "--map", "{map0}",
                        "--verify"]],
                  clean=["data/sounds_*.pak"], outputs=["data/sounds.pak", "data/sounds_*.pak"]))
    s.append(Step("audio", "audio", "audio.pak + music/: zone music (tracks copied from the client)",
                  prefixes=P_DBC + P_MUSIC,
                  run=[["@exe:psp_audio_conv", "--extracted", "{X}", "--out", "{D}/audio.pak", "--music-out",
                        "{D}/music", "--no-original"]],
                  outputs=["data/audio.pak", "data/music/**/*"]))
    s.append(Step("characters", "models", "characters.pak: player races (+ the emote text table)",
                  prefixes=P_DBC + P_MODELS, locale="variant",
                  run=[["@exe:psp_char_conv", "--extracted", "{X}", "--out", "{D}/characters.pak", "--verify"]],
                  outputs=["data/characters.pak"], warn_rc=[4],
                  variant=Variant(run=[["@exe:psp_char_conv", "--extracted", "{X}", "--out", "{D}/emotetext.pak",
                                        "--emotetext-only", "--verify"]],
                                  outputs=["data/emotetext.pak"], prefixes=P_DBC + ["interface\\framexml\\"],
                                  title="emotetext.pak: the emote text table of this locale")))
    s.append(Step("items", "models", "items.pak: equipment models and textures", prefixes=P_DBC + P_MODELS,
                  run=[["@exe:psp_item_conv", "--extracted", "{X}", "--out", "{D}/items.pak", "--verify",
                        "{?mirror}"]],
                  outputs=["data/items.pak"]))
    s.append(Step("creatures", "models", "creatures.pak: creature models", prefixes=P_DBC + P_MODELS + ["world\\"],
                  run=[["@exe:psp_creature_conv", "--extracted", "{X}", "--out", "{D}/creatures.pak",
                        "--jobs", "{jobs}", "--verify"]],
                  outputs=["data/creatures.pak"], warn_rc=[4]))
    s.append(Step("spellfx", "models", "spellfx.pak: spell effects", prefixes=P_DBC + P_MODELS,
                  probes=[Probe(["@exe:psp_spellfx_conv", "--extracted", "{X}", "--out", "{W}/spellfx_probe.pak",
                                 "--list-missing", "{L}/spellfx_missing.txt"], "{L}/spellfx_missing.txt")],
                  run=[["@exe:psp_spellfx_conv", "--extracted", "{X}", "--out", "{D}/spellfx.pak"]],
                  verify=[["@exe:psp_spellfx_conv", "--verify", "{D}/spellfx.pak"]],
                  outputs=["data/spellfx.pak"]))
    for name, extra, title in (("gameobjects", ["--gameobjects"], "gameobjects.pak: object models"),
                               ("transports", ["--transports"], "transports.pak: ships and airships"),
                               ("gameobjects_wmo", ["--transports", "--wmo-set", "other"],
                                "gameobjects_wmo.pak: building-type objects")):
        s.append(Step(name, "models", title, prefixes=P_DBC + P_WORLD,
                      run=[["@exe:psp_model_conv", "--extracted", "{X}"] + extra + ["--out", "{D}/%s.pak" % name]],
                      verify=[["@exe:psp_model_conv", "--verify", "{D}/%s.pak" % name]],
                      outputs=["data/%s.pak" % name]))
    s.append(Step("worldmap", "world", "worldmap.pak: world and cosmic map levels",
                  prefixes=P_DBC + ["interface\\worldmap\\", "textures\\minimap\\"], locale="names",
                  run=[["@exe:psp_minimap_conv", "--extracted", "{X}", "--world", "--out", "{D}/worldmap.pak",
                        "--verify"]],
                  outputs=["data/worldmap.pak"]))
    return s


GLOBAL_MAX_TRIS = "110000"   # a global-WMO dungeon over this many source triangles is baked as a big WMO
# psp_model_conv names a map's big WMO paks "bigwmo/<map>_<stem>[_<hash>].pak" in at most 43 characters; a map
# name over 22 characters is cut to 22 first (e.g. a 23-character dungeon name: "<22 chars>__<hash>.pak").
BIGWMO_MAP_CHARS = 22


def bigwmo_prefix(lower):
    """The map part of the map's bigwmo/ pak names (the outputs and the clean-up glob of its doodads step)."""
    return lower[:BIGWMO_MAP_CHARS]


def map_steps(group, m):
    """terrain / doodads (+ data/bigwmo/) / minimap of one map."""
    p = {"map": m.dir, "lower": m.lower, "tiles": "global" if m.global_wmo else m.tiles,
         "bigwmo": bigwmo_prefix(m.lower)}
    extra = ["--global-max-tris", GLOBAL_MAX_TRIS] if m.global_wmo else []
    pre = P_DBC + P_WORLD + ["textures\\minimap\\"]
    return [
        Step("terrain_" + m.lower, "maps:" + group, "terrain_%s.pak" % m.lower, prefixes=pre, params=p,
             run=[["@exe:psp_terrain_conv", "--extracted", "{X}", "--map", "{map}", "--out",
                   "{D}/terrain_{lower}.pak", "{tiles}"]],
             outputs=["data/terrain_{lower}.pak"]),
        Step("doodads_" + m.lower, "maps:" + group, "doodads_%s.pak + bigwmo/%s_*.pak" % (m.lower, p["bigwmo"]),
             prefixes=pre, params=p, clean=["data/bigwmo/{bigwmo}_*.pak"],
             run=[["@exe:psp_model_conv", "--extracted", "{X}", "--map", "{map}", "--out",
                   "{D}/doodads_{lower}.pak"] + extra + ["{tiles}"]],
             verify=[["@exe:psp_model_conv", "--verify", "{D}/doodads_{lower}.pak"]],
             outputs=["data/doodads_{lower}.pak", "data/bigwmo/{bigwmo}_*.pak"]),
        Step("minimap_" + m.lower, "maps:" + group, "minimap_%s.pak" % m.lower, prefixes=pre, params=p,
             locale="names",
             run=[["@exe:psp_minimap_conv", "--extracted", "{X}", "--map", "{map}", "--all", "--worldmap", "all",
                   "--out", "{D}/minimap_{lower}.pak", "--verify"]],
             outputs=["data/minimap_{lower}.pak"]),
    ]


def _relocate(pattern, loc):
    return "data/locale/%s/%s" % (loc, pattern[len("data/"):]) if pattern.startswith("data/") else pattern


def locale_copy(st, loc, folders):
    """The copy of a localized step for another locale. folders: {X, XG, D, W, L} of that locale."""
    c = copy.deepcopy(st)
    c.base = st.name
    c.name = "%s@%s" % (st.name, loc)
    c.params = dict(st.params, loc=loc, **folders)
    if st.locale == "variant" and st.variant:
        c.run, c.verify, c.prefixes, c.title = st.variant.run, st.variant.verify, st.variant.prefixes, st.variant.title
        c.outputs, c.probes, c.png = st.variant.outputs, [], False
    c.title = "%s [%s]" % (c.title, loc)
    c.outputs = [_relocate(o, loc) for o in c.outputs]
    c.clean = [_relocate(o, loc) for o in c.clean]
    return c


MAP_GROUPS = ("continents", "dungeons", "battlegrounds", "arenas")
DEFAULT_MAP_GROUPS = ("continents", "dungeons", "battlegrounds")

# Not produced by Kiln, with the reason (printed by --list-steps and in the plan).
NOT_PROVIDED = {
    "movies/": "the cinematics need a PSP movie encoder that is not freely available; the port runs "
               "without them (a missing movie is skipped)",
    "music/original/": "the upstream project's own composed music is not part of the client and its "
                       "licence does not allow Kiln to copy it",
}
