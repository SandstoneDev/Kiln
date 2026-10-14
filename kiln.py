#!/usr/bin/env python3
"""Kiln - builds the PSP data folder of the port from your own client files, on your own PC.

    python kiln.py --game <client folder> --out <output folder> [options]

The client folder is the one that holds the client's executable and its Data/ folder with the MPQ
archives. Kiln identifies the client release by the executable's hash (only build 12340 is
supported), extracts the files the converters need (read-only, into a work folder), runs every
converter and writes <out>/data/ plus a manifest and a personal-use note.

Locales: every locale present in Data/ is converted. The primary one (--locale, else the only one,
else English, else the first by name) goes to data/; the localized files of every other locale go to
data/locale/<xxYY>/. --add-locale adds the locales of another client folder of the same build (a copy
of the game in another language), extracted from that client's own archives. data/locales.txt lists
them for the EBOOT, with the language the game starts in (--default-locale).

Options:
  --steps LIST     steps or groups to run (comma separated): default, all, tables, interface, models, audio,
                   world, maps, continents, dungeons, battlegrounds, arenas, locales, @xxYY (one
                   locale's copies), a step name, or a wildcard such as terrain_*  (default: default)
  --skip LIST      step names to leave out
  --jobs N         steps run in parallel, and the worker count of converters that take one (default 1)
  --dry-run        check the client and print the plan; writes nothing
  --verify         check the files of an earlier run against the manifest and re-run the
                   converters' own verify passes; bakes nothing
  --check          integrity check of an earlier run: every output file against the manifest (size,
                   SHA-256), the steps' converter and input identity, data/locales.txt, files nobody
                   made; with --target also the copy there; --deep adds the converters' verify passes
  --update         re-make only the recorded steps whose converters/bakers or input changed, that are
                   unknown (older manifest) or failed, or whose files are damaged; then --target
  --repair         --check, then re-make the steps with damaged or missing files and re-copy the
                   damaged or missing files of --target
  --target DIR     a copy of the game folder (ms0:/PSP/GAME/<APP>/ on the memory stick): data/ goes
                   inside it; only missing or different files are copied (nothing is deleted)
  --deep           with --check / --repair: also the converters' own verify passes
  --force          re-run steps that are up to date
  --json           progress as one JSON object per line on stdout (for a GUI)
  --tools DIR      the converter executables (default: bin/ next to kiln.py)
  --bakers DIR     the Python bakers and profiles (default: bakers/ next to kiln.py)
  --dll-dir DIR    a folder with the converters' runtime libraries (added to PATH); default: none when
                   they are next to the converters, else KILN_DLL_DIR, else an MSYS2 UCRT64 bin folder
  --work DIR       where extracted client files go (default: <out>/work); put it on a fast disk
  --mirror DIR     a folder on a fast disk the items converter fills with the files it reads
  --python EXE     Python for the bakers (default: the one running Kiln)
  --app NAME       the game folder name on the memory stick (default: WOWEEPSP)
  --locale xxYY    the primary locale (written to data/)
  --locales LIST   the other locales of --game to convert (default: all present; 'none' for only the primary)
  --add-locale DIR another client folder of the same build (repeatable): its locales become extra locales
  --default-locale xxYY  the language the game starts in (the primary or an extra; default: the primary);
                   the player's choice in the game (wowee.cfg locale=) wins over it
  --remove-locale xxYY   remove an extra locale from an earlier output (data/locale/<xxYY>/, locales.txt;
                   with --target also from the copy); never the primary

A language added later: kiln.py --out <earlier output> --add-locale <client> [--target DIR] - without
--game: the primary locale and build come from the output's manifest, only the new locale's localized
steps run (the main client folder is not needed).
  --eboot FILE     your copy of the port's EBOOT.PBP: xmb/EBOOT.PBP gets the menu art made from your client
  --list-steps     print the step table and exit
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

KILN_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, KILN_ROOT)

from kilnlib import KILN_VERSION, blp, check as CK, client as clientmod, locales as L, steps as S, winrt  # noqa: E402
from kilnlib.manifest import Manifest  # noqa: E402
from kilnlib.pipeline import (Ctx, Heartbeat, build_steps, extract, extract_map_wdts, map_groups_wanted,  # noqa: E402
                              ExtractRecord, FailGate, resolve_map0, run_cmd, run_gated, run_probe, select,
                              verify_existing)

PERSONAL_USE = """PERSONAL USE ONLY

This folder was generated by Kiln {version} on {date} on this computer, from the client files
in the folder you selected (client build {build}, locales {locales}).

Everything under data/ and xmb/ was converted from YOUR OWN copy of the game, for YOUR OWN use
on your own device. It contains material that belongs to the game's rights holder.

  * Do not share, upload, sell or otherwise distribute these files, or any archive, image or
    build that contains them.
  * Kiln and its authors grant no rights to this material.
  * If you no longer own the game, delete this folder.

Install: copy the data folder to the memory stick as

    ms0:/PSP/GAME/{app}/data

next to the port's EBOOT.PBP (the EBOOT is distributed separately; it contains no game data).
"""


def parse(argv):
    ap = argparse.ArgumentParser(prog="kiln.py", description="Builds the PSP data folder from your own client files.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", help="the client folder (holds the executable and Data/)")
    ap.add_argument("--out", help="output folder (gets data/, xmb/, logs/, work/ and the personal-use note)")
    ap.add_argument("--steps", default="default")
    ap.add_argument("--skip", default="")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tools")
    ap.add_argument("--bakers")
    ap.add_argument("--dll-dir")
    ap.add_argument("--work")
    ap.add_argument("--mirror")
    ap.add_argument("--python")
    ap.add_argument("--app", default="WOWEEPSP")
    ap.add_argument("--locale")
    ap.add_argument("--locales", default="all")
    ap.add_argument("--add-locale", action="append", default=[], metavar="DIR")
    ap.add_argument("--default-locale", metavar="xxYY")
    ap.add_argument("--remove-locale", action="append", default=[], metavar="xxYY")
    ap.add_argument("--eboot")
    ap.add_argument("--list-steps", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--repair", action="store_true")
    ap.add_argument("--target", metavar="DIR")
    ap.add_argument("--deep", action="store_true")
    a = ap.parse_args(argv)
    if not a.list_steps and not a.out:
        ap.error("--out is required")
    if not a.list_steps and not a.game and not (a.add_locale or a.remove_locale):
        ap.error("--game is required (without it only --add-locale or --remove-locale on an earlier output)")
    if a.remove_locale and (a.add_locale or a.dry_run or a.verify or a.check or a.update or a.repair):
        ap.error("--remove-locale goes alone (with --out, optionally --target and --json)")
    if not a.game and (a.verify or a.check or a.update or a.repair):
        ap.error("--verify, --check, --update and --repair need --game")
    modes = [m for m in ("dry_run", "verify", "check", "update", "repair") if getattr(a, m)]
    if len(modes) > 1:
        ap.error("choose one of --dry-run, --verify, --check, --update, --repair (got %s)"
                 % ", ".join("--" + m.replace("_", "-") for m in modes))
    if a.deep and not (a.check or a.repair):
        ap.error("--deep goes with --check or --repair (--verify always runs the verify passes)")
    a.mode = "update" if a.update else "repair" if a.repair else "check" if a.check else ""
    return a


def inside(path, root):
    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def show_cmd(argv):
    s = subprocess.list2cmdline(argv)
    return s if len(s) <= 220 else s[:200] + " ... (%d arguments)" % len(argv)


def _step_doc(st, group=None):
    """One step of --list-steps --json."""
    d = {"step": st.name, "group": group or st.group, "locale": st.locale or None, "what": st.title,
         "run": [list(c) for c in st.run], "verify": [list(c) for c in st.verify], "writes": list(st.outputs)}
    if group is None:
        d["client_paths"] = list(st.prefixes)
        d["png_side_files"] = bool(st.png)
        d["probes"] = [list(p.cmd) if p.cmd else {"builtin": p.builtin} for p in st.probes]
        d["builtin"] = st.builtin or None
        d["other_locales"] = ({"run": [list(c) for c in st.variant.run], "writes": list(st.variant.outputs)}
                              if st.variant else None)
    return d


def list_steps(a):
    bakers = os.path.abspath(a.bakers) if a.bakers else os.path.join(KILN_ROOT, "bakers")

    class _M:
        dir, lower, global_wmo, tiles = "<Map>", "<map>", False, [(0, 0)]
    if a.json:  # one JSON object (the GUI and scripts; the text table below is for people)
        import json
        doc = {"event": "steps", "kiln": KILN_VERSION,
               "steps": [_step_doc(st) for st in S.static_steps(bakers)],
               "map_steps": [_step_doc(st, "maps") for st in S.map_steps("<group>", _M)],
               "map_groups": list(S.MAP_GROUPS), "default_map_groups": list(S.DEFAULT_MAP_GROUPS),
               "not_provided": dict(S.NOT_PROVIDED)}
        print(json.dumps(doc, ensure_ascii=False), flush=True)
        return 0
    print("Kiln %s step table (per-map steps are generated from the client's map table)\n" % KILN_VERSION)
    print("%-16s %-8s %-8s %s" % ("step", "group", "locale", "what it makes"))
    for st in S.static_steps(bakers):
        print("%-16s %-8s %-8s %s" % (st.name, st.group, st.locale or "-", st.title))
        print("    client paths: %s%s" % (", ".join(st.prefixes) or "-", "  (+ PNG side files)" if st.png else ""))
        for p in st.probes:
            print("    probe:  %s" % (" ".join(p.cmd) if p.cmd else "(built into Kiln: %s list)" % p.builtin))
        for c in st.run:
            print("    run:    %s" % " ".join(c))
        if st.builtin:
            print("    run:    (built into Kiln: %s)" % st.builtin)
        for c in st.verify:
            print("    verify: %s" % " ".join(c))
        print("    writes: %s" % ", ".join(st.outputs))
        if st.variant:
            print("    other locales: %s -> %s" % (" ".join(st.variant.run[0]), ", ".join(st.variant.outputs)))

    for st in S.map_steps("<group>", _M):
        print("%-16s %-8s %-8s %s" % (st.name, "maps", st.locale or "-", st.title))
        for c in st.run:
            print("    run:    %s" % " ".join(c))
        for c in st.verify:
            print("    verify: %s" % " ".join(c))
    print("\nmap groups: %s (default: %s)" % (", ".join(S.MAP_GROUPS), ", ".join(S.DEFAULT_MAP_GROUPS)))
    print("locale: full = re-run per extra locale into data/locale/<xxYY>/; variant = a smaller run for the extra "
          "locales; names = localized names and map art inside, primary locale only")
    for k, v in S.NOT_PROVIDED.items():
        print("not provided: %-16s %s" % (k, v))
    return 0


def plan_doc(ctx, chosen, man):
    rows = []
    for s in chosen:
        state = "run"
        if man and man.up_to_date(s.name, ctx.step_key(s), ctx.step_identity(s)):
            state = "up-to-date"
        if any(not os.path.exists(ctx.program_path(t)) for t in s.programs()):
            state = "program-missing"
        rows.append({"name": s.name, "group": s.group, "title": s.title, "locale": ctx.step_locale(s),
                     "state": state, "outputs": s.outputs,
                     "last_seconds": (man.step(s.name) or {}).get("seconds") if man else None})
    return rows


def fail(a, msg, **fields):
    """A start-up error: a JSON 'error' event with --json (stdout carries nothing else), else text. -> 2"""
    if a.json:
        import json
        doc = {"event": "error", "message": msg}
        doc.update(fields)
        print(json.dumps(doc, ensure_ascii=False), flush=True)
    else:
        print("Kiln %s\nerror: %s" % (KILN_VERSION, msg))
    return 2


def resolve_locales(a, cl):
    """--locales, --add-locale and --default-locale applied to the identified client (cl.locales,
    cl.sources, cl.default). -> user-facing notes. Raises ClientError."""
    added = [clientmod.identify_added(d, cl) for d in a.add_locale]
    if a.locales.strip().lower() == "none":
        cl.locales = [cl.locale]
    elif a.locales.strip().lower() != "all":
        want = [x.strip() for x in a.locales.split(",") if x.strip()]
        known = set(cl.detected) | {x for c in added for x in c.detected}
        unknown = [x for x in want if x not in known]
        if unknown:
            raise clientmod.ClientError("--locales %s: not in Data/ of the client(s) (present: %s)"
                                        % (", ".join(unknown), ", ".join(sorted(known))))
        cl.locales = [cl.locale] + [x for x in want if x != cl.locale and x in cl.detected]
    notes = clientmod.merge_added(cl, added)
    if a.default_locale:
        if a.default_locale not in cl.locales:
            raise clientmod.ClientError("--default-locale %s: not one of the converted locales (%s)"
                                        % (a.default_locale, ", ".join(cl.locales)))
        cl.default = a.default_locale
    return notes


def recorded_locales(a):
    """--check / --update / --repair without --add-locale: the added clients and the start language the last
    run recorded in the manifest (when those client folders are still there). -> (manifest locales, notes)"""
    man = Manifest(os.path.abspath(a.out), os.path.join(os.path.abspath(a.out), "data"))
    rec = man.doc.get("locales", {})
    notes = []
    if not a.add_locale:
        main_dir = os.path.normcase(os.path.realpath(a.game))
        for loc, src in sorted(rec.get("sources", {}).items()):
            d = src.get("client", "")
            if not d or os.path.normcase(os.path.realpath(d)) == main_dir or d in a.add_locale:
                continue
            if os.path.isdir(d):
                a.add_locale.append(d)
                notes.append("%s: from the manifest (the client of %s last time)" % (d, loc))
            else:
                notes.append("%s: recorded as the client of %s, not found - %s is not re-made" % (d, loc, loc))
    return rec, notes


class _AnyMap:
    """A stand-in map for the map steps' command templates (which programs they run)."""
    id, dir, lower, global_wmo, tiles = 0, "<Map>", "<map>", False, [(0, 0)]


def converter_exes(ctx, steps, groups=()):
    """The converter executables the steps (and, with map groups, the map steps still to come) run."""
    toks = {"@exe:psp_mpq_extract"} | {t for st in steps for t in st.programs() if t.startswith("@exe:")}
    if groups:
        toks |= {t for st in S.map_steps("maps", _AnyMap) for t in st.programs() if t.startswith("@exe:")}
    return sorted(ctx.program_path(t) for t in toks)


def use_dlls(ctx, exes):
    """Pick the converters' DLL folder (--dll-dir, else next to them, KILN_DLL_DIR, MSYS2) and say which."""
    ctx.dll_dir, how = winrt.resolve_dll_dir(exes, ctx.a.dll_dir or "")
    ctx.say("converters' DLLs: %s%s" % (how, "" if not ctx.dll_dir or ctx.dll_dir in how else " - " + ctx.dll_dir))
    ctx.event("phase", phase="dlls", how=how, dir=ctx.dll_dir)


def start_check(ctx, exes):
    """Start every converter once (usage). -> list of failures; they are reported as one error."""
    present = [e for e in exes if os.path.exists(e)]
    bad = winrt.preflight(present, ctx.env(), [ctx.dll_dir, ctx.tools])
    if not bad:
        return []
    lines = ["%d converter(s) cannot start:" % len(bad)]
    dlls = sorted({d for _e, _r, miss in bad for d in miss}, key=str.lower)
    for exe, rc, miss in bad:
        lines.append("  %s: %s%s" % (os.path.basename(exe), rc, ("; missing: " + ", ".join(miss)) if miss else ""))
    lines.append("Fix: put %s next to the converters (%s; the Windows bundle has them there), or pass "
                 "--dll-dir <the folder that holds them> (a developer build with MSYS2: its ucrt64\\bin)."
                 % (", ".join(dlls) if dlls else "the DLLs they need", ctx.tools))
    ctx.say("error: " + "\n".join(lines))
    ctx.event("error", message="converters cannot start", failures=[
        {"exe": e, "rc": r, "missing": m} for e, r, m in bad], missing_dlls=dlls, dll_dir=ctx.dll_dir)
    return bad


def main(argv):
    winrt.set_error_mode()        # a missing DLL ends a converter with an exit code, never with a dialog
    a = parse(argv)
    if a.list_steps:
        return list_steps(a)
    rec_locales, rec_notes = recorded_locales(a) if a.mode else ({}, [])
    for p, what in ((a.out, "--out"), (a.work, "--work"), (a.mirror, "--mirror"), (a.target, "--target")):
        if p and inside(p, KILN_ROOT):
            return fail(a, "%s must be outside the Kiln folder (%s): Kiln's folder never holds converted data."
                        % (what, KILN_ROOT))
        for g in [x for x in [a.game] + list(a.add_locale) if x]:
            if p and inside(p, g):
                return fail(a, "%s must be outside the client folder %s: Kiln never writes into your client."
                            % (what, g))

    later = None          # --add-locale / --remove-locale on an earlier output without --game: the new locales
    try:
        if a.remove_locale or not a.game:
            cl, later, notes = later_locales(a)
        else:
            cl = clientmod.identify(a.game, a.locale)
            notes = resolve_locales(a, cl)
    except clientmod.ClientError as e:
        return fail(a, str(e))
    if a.mode and not a.default_locale and rec_locales.get("default") in cl.locales:
        cl.default = rec_locales["default"]       # the start language of the last run
    notes = rec_notes + notes

    ctx = Ctx(a, cl, KILN_ROOT)
    ctx.say("Kiln %s" % KILN_VERSION, log=False)
    ctx.say("client: %s  sha256 %s  -> %s" % (cl.exe, cl.sha256, cl.label), log=False)
    ctx.say("        (the hash identifies the version of the files; it does not check who owns them)", log=False)
    for n in notes:
        ctx.say("added client " + n, log=False)
    ctx.say("locales: primary %s -> data/; %s" % (cl.locale, ("others -> data/locale/<xxYY>/: " + ", ".join(
        "%s%s" % (x, " (from %s)" % cl.sources[x].game_dir if x in cl.sources else "") for x in cl.secondary))
        if cl.secondary else "no other locale"), log=False)
    ctx.say("        the game starts in %s (data/locales.txt; the choice made in the game wins)" % cl.default_locale,
            log=False)
    ctx.event("client", exe=cl.exe, sha256=cl.sha256, build=cl.build, label=cl.label, primary=cl.locale,
              locales=cl.locales, extra=cl.secondary, default=cl.default_locale,
              sources={x: cl.source_of(x).game_dir for x in cl.locales}, notes=notes, kiln=KILN_VERSION)

    if a.mode and not os.path.exists(Manifest(ctx.out, ctx.data).path):
        return fail(a, "no Kiln manifest in %s: nothing to %s (run Kiln first)" % (ctx.data, a.mode))
    if a.check:
        return do_check(ctx)
    if a.remove_locale:
        return remove_locales(ctx, later)
    narrow, repair_paths = prepare_mode(ctx, later)

    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    groups = map_groups_wanted(a.steps)
    all_steps, maps_note = build_steps(ctx, groups)
    chosen = narrow(select(all_steps, a.steps, skip))
    if not chosen and not maps_note and not a.mode:     # map steps are only known once the map table is extracted
        ctx.say("error: --steps %r selects nothing (see --list-steps)" % a.steps)
        ctx.event("error", message="nothing selected")
        return 2
    exes = converter_exes(ctx, chosen, groups)
    use_dlls(ctx, exes)

    if a.dry_run:
        return dry_run(ctx, chosen, maps_note)
    if a.verify:
        man = Manifest(ctx.out, ctx.data)
        nbad, vfail = verify_existing(ctx, man, chosen)
        nbad += verify_locales_txt(ctx)
        ctx.say("verify: %d file problem(s), %d failed verify pass(es)" % (nbad, vfail))
        ctx.event("done", verify=True, problems=nbad, failed_verify=vfail)
        return 1 if nbad or vfail else 0

    missing = missing_programs(ctx, chosen)
    if missing:
        ctx.say("error: programs not found:\n  " + "\n  ".join(missing))
        ctx.say("Build or copy the converters into --tools (%s) and the bakers into --bakers (%s)."
                % (ctx.tools, ctx.bakers))
        ctx.event("error", message="programs not found", missing=missing)
        return 2
    if start_check(ctx, exes):     # before any extraction: a missing DLL is found in seconds, not after an hour
        return 2

    for d in (ctx.data, ctx.logs, ctx.work):
        os.makedirs(d, exist_ok=True)
    man = Manifest(ctx.out, ctx.data)
    man.doc["tool_version"] = KILN_VERSION
    man.doc["client"] = {"sha256": cl.sha256, "size": cl.size, "build": cl.build, "verified_with": cl.entry["locale"]}
    man.doc["locales"] = locales_doc(cl)
    man.save()
    write_personal_note(ctx)
    ctx.say("locales: %s (primary %s, extra %s, default %s)" % (
        L.write(ctx.data, cl.locale, cl.secondary, cl.default_locale), cl.locale, ", ".join(cl.secondary) or "none",
        cl.default_locale))
    t0 = time.time()

    # 1. extraction (read-only on the archives) of what the pending steps need, per locale
    def pending_of(steps):
        return [s for s in steps if a.mode or a.force or
                not man.up_to_date(s.name, ctx.step_key(s), ctx.step_identity(s))]

    pending = pending_of(chosen)
    done_prefixes = {}
    need_main = [s.name for s in pending if not cl.present and ctx.step_locale(s) == cl.locale]
    if need_main:
        return fail(a, "these steps need the main client's own files - pass --game <the main client folder>: %s"
                    % ", ".join(need_main))

    def extract_for(steps, extra=()):
        by_loc = {}
        for s in steps:
            by_loc.setdefault(ctx.step_locale(s), set()).update(s.prefixes)
        if extra:
            by_loc.setdefault(cl.locale, set()).update(extra)
        for loc, pre in sorted(by_loc.items()):
            pre = sorted(pre - done_prefixes.get(loc, set()))
            if not pre:
                continue
            target = ctx.x if loc == cl.locale else ctx.locale_folders(loc)["X"]
            lists = ctx.lists if loc == cl.locale else ctx.locale_folders(loc)["L"]
            record = ExtractRecord(lists, target, cl.data_dir_for(loc), loc)
            had = [p for p in pre if not a.force and record.covers(p)]
            if had:
                ctx.say("extract [%s]: %d client path prefixes already extracted from the same archives (--force "
                        "extracts them again)" % (loc, len(had)))
                done_prefixes.setdefault(loc, set()).update(had)
                pre = [p for p in pre if p not in had]
                if not pre:
                    continue
            ctx.say("extract [%s]: %d client path prefixes from %s -> %s (logs/extract_%s.log)"
                    % (loc, len(pre), cl.data_dir_for(loc), target, loc))
            ctx.event("phase", phase="extract", state="start", locale=loc, prefixes=pre, source=cl.data_dir_for(loc))
            te = time.time()
            ctx.running["extract:%s" % loc] = te
            ctx.running_locale["extract:%s" % loc] = loc
            try:
                rc = extract(ctx, pre, target=target, locale=loc, log_name="extract_%s.log" % loc)
            finally:
                ctx.running.pop("extract:%s" % loc, None)
                ctx.running_locale.pop("extract:%s" % loc, None)
            ctx.say("extract [%s]: rc %d, %.0f s" % (loc, rc, time.time() - te))
            ctx.event("phase", phase="extract", state="end", locale=loc, rc=rc, seconds=round(time.time() - te, 1))
            done_prefixes.setdefault(loc, set()).update(pre)
            if rc == 0:
                record.add(pre)

    hb = Heartbeat(ctx)        # 'running' events for the extraction, the PNG pass and the steps
    hb.start()
    uses_map0 = any("{map0}" in str(s.run) for s in pending)
    extract_for(pending, extra=S.P_DBC if (groups or uses_map0) and cl.present else ())
    resolve_map0(ctx)
    if groups and maps_note:
        # the client's map table is there now: the maps' WDT files (tiles, global WMOs), then the per-map steps
        extract_map_wdts(ctx)
        all_steps, maps_note = build_steps(ctx, groups)
        chosen = narrow(select(all_steps, a.steps, skip))
        if not chosen and not a.mode:
            hb.stop.set()
            ctx.say("error: --steps %r selects nothing (see --list-steps)" % a.steps)
            ctx.event("error", message="nothing selected")
            return 2
        known = {s.name for s in pending}
        new = [s for s in pending_of(chosen) if s.name not in known]
        extract_for(new)
        pending += new
        ctx.say("maps: %d per-map steps" % sum(1 for s in chosen if s.group.startswith("maps:")))
    ctx.event("plan", steps=plan_doc(ctx, chosen, man))

    # 2. PNG side files for the Python bakers, per extracted tree
    trees = sorted({ctx.folder(s, "X") for s in pending if s.png})
    for tree in trees:
        ctx.say("png: interface art -> PNG side files in %s" % tree)
        ctx.event("phase", phase="png", state="start", tree=tree)
        ctx.running["png"] = time.time()
        try:
            w, k, f = blp.png_sidecars(tree, S.PNG_SUBDIRS, jobs=ctx.jobs, log=ctx.say)
        finally:
            ctx.running.pop("png", None)
        ctx.say("png: %d written, %d up to date, %d failed" % (w, k, f))
        ctx.event("phase", phase="png", state="end", written=w, kept=k, failed=f)

    # 3. probes: client files the archives' listfiles do not name
    for s in pending:
        for p in s.probes:
            ctx.event("phase", phase="probe", step=s.name)
            run_probe(ctx, s, p)

    # 4. the steps (a failure stops what would fail the same way: pipeline.FailGate)
    total = len(chosen)
    results = {}
    gate = FailGate()
    try:
        if ctx.jobs > 1:
            with ThreadPoolExecutor(max_workers=ctx.jobs) as ex:
                futs = {s.name: ex.submit(run_gated, ctx, man, gate, s, i + 1, total, bool(a.mode))
                        for i, s in enumerate(chosen)}
                for n, f in futs.items():
                    results[n] = f.result()
        else:
            for i, s in enumerate(chosen):
                results[s.name] = run_gated(ctx, man, gate, s, i + 1, total, bool(a.mode))
    finally:
        hb.stop.set()
    man.save()

    ok = sum(1 for r in results.values() if r == "ok")
    sk = sum(1 for r in results.values() if r == "skipped")
    bad = [n for n, r in results.items() if r not in ("ok", "skipped")]
    blocked = [n for n, r in results.items() if r == "blocked"]
    copy = None
    if a.target:
        only = None
        if a.repair or later:  # (repair) the target files the check found bad; every file of a step made now
            only = set(repair_paths) | {CK.key(o["path"]) for n, r in results.items() if r == "ok"
                                        for o in (man.step(n) or {}).get("outputs", [])}
            only.add(CK.key("data/" + L.FILE_NAME))
        copy = target_copy(ctx, man, only)
    big = [o["path"] for st in man.doc["steps"].values() for o in st.get("outputs", []) if o["size"] >= 4 << 30]
    ctx.say("\ndone in %.0f s: %d made, %d up to date, %d failed%s%s" % (
        time.time() - t0, ok, sk, len(bad) - len(blocked), (" (" + ", ".join(n for n in bad if n not in blocked) + ")")
        if len(bad) > len(blocked) else "", (", %d not started after a failure (%s)" % (len(blocked), ", ".join(blocked)))
        if blocked else ""))
    if big:
        ctx.say("warning: files of 4 GB or more do not fit a FAT32 memory stick: %s" % ", ".join(big))
    ctx.say("manifest: %s" % man.path)
    ctx.say("\nNext: copy the folder\n    %s\nto the memory stick as\n    ms0:/PSP/GAME/%s/data\n"
            "next to the port's EBOOT.PBP (distributed separately)." % (ctx.data, a.app))
    ctx.say("The work folder %s holds files extracted from your client; delete it when you no longer\n"
            "need it (it speeds up a re-run). These files are for your own use - do not share them\n"
            "(see %s)." % (ctx.work, os.path.join(ctx.out, "README_PERSONAL_USE.txt")))
    ctx.event("done", made=ok, skipped=sk, failed=bad, seconds=round(time.time() - t0, 1), data=ctx.data,
              target="ms0:/PSP/GAME/%s/data" % a.app, manifest=man.path, too_big_for_fat32=big, mode=a.mode,
              copy=copy, blocked=blocked)
    return 1 if bad or (copy and copy["failed"]) else 0


def target_copy(ctx, man, only=None):
    """--target: copy what is missing or different there (size, then the copy record or SHA-256)."""
    rep = CK.Report(ctx)
    root = CK.target_root(ctx.a.target)
    ctx.say("target: %s (data/ inside it; only missing or different files are copied)" % root)
    copied, kept, failed, nbytes = CK.sync_target(ctx, man, ctx.a.target, rep, full=False, only=only)
    ctx.say("target: %d copied (%.1f MB), %d already there, %d failed" % (copied, nbytes / 1048576.0, kept, failed))
    return {"root": root, "copied": copied, "kept": kept, "failed": failed, "bytes": nbytes}


def later_locales(a):
    """--add-locale / --remove-locale on an earlier output (without --game, or --remove-locale): the main client
    from the output's manifest. -> (client, the locales added or removed, notes). Raises ClientError."""
    out = os.path.abspath(a.out)
    man = Manifest(out, os.path.join(out, "data"))
    if not os.path.exists(man.path):
        raise clientmod.ClientError("%s holds no Kiln output (no data/%s): convert with --game first"
                                    % (out, os.path.basename(man.path)))
    cl = clientmod.from_manifest(man.doc)
    if a.remove_locale:
        for x in a.remove_locale:
            if x == cl.locale:
                raise clientmod.ClientError("%s is the primary locale of this output (data/): it cannot be removed"
                                            % x)
            if x not in cl.secondary:
                raise clientmod.ClientError("%s is not an extra locale of this output (extra: %s)"
                                            % (x, ", ".join(cl.secondary) or "none"))
        return cl, list(a.remove_locale), []
    added = [clientmod.identify_added(d, cl) for d in a.add_locale]
    for c in added:                    # a locale given again: made from this client now (what is done is kept)
        for x in c.detected:
            if x in cl.sources:
                cl.sources.pop(x)
                cl.locales.remove(x)
    before = set(cl.locales)
    notes = clientmod.merge_added(cl, added)
    new = [x for x in cl.locales if x not in before]
    if not new:
        raise clientmod.ClientError("no locale to add (%s)" % "; ".join(notes))
    if a.default_locale:
        if a.default_locale not in cl.locales:
            raise clientmod.ClientError("--default-locale %s: not one of the locales (%s)"
                                        % (a.default_locale, ", ".join(cl.locales)))
        cl.default = a.default_locale
    notes.insert(0, "%s: the main client (%s, build %d) from the manifest of %s - its folder is not needed"
                 % (cl.game_dir or "?", cl.locale, cl.build, out))
    return cl, new, notes


def remove_locales(ctx, gone):
    """--remove-locale: data/locale/<xxYY>/, its steps in the manifest, locales.txt; --target: the files Kiln
    copied there for it, and locales.txt."""
    cl, a = ctx.client, ctx.a
    man = Manifest(ctx.out, ctx.data)
    for x in gone:
        folder = os.path.join(ctx.data, "locale", x)
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        dropped = [n for n in list(man.doc.get("steps", {})) if n.endswith("@" + x)]
        for n in dropped:
            del man.doc["steps"][n]
        cl.locales.remove(x)
        cl.sources.pop(x, None)
        if cl.default == x:
            cl.default = ""
            ctx.say("remove: %s was the start language - the game now starts in the primary %s" % (x, cl.locale))
        ctx.say("remove: %s - data/locale/%s/ deleted, %d step(s) dropped from the manifest (its extracted files "
                "stay in the work folder until you delete it)" % (x, x, len(dropped)))
        ctx.event("locale", action="removed", locale=x, steps=dropped)
    man.doc["locales"] = locales_doc(cl)
    man.save()
    ctx.say("locales: %s (primary %s, extra %s, default %s)" % (
        L.write(ctx.data, cl.locale, cl.secondary, cl.default_locale), cl.locale, ", ".join(cl.secondary) or "none",
        cl.default_locale))
    copy = None
    if a.target:
        root = CK.target_root(a.target)
        record = CK.load_copy_record(root)
        removed = 0
        for rel in sorted(record):
            if any(rel.startswith("data/locale/%s/" % x) for x in gone):
                try:
                    os.remove(os.path.join(root, rel))
                    removed += 1
                except OSError:
                    pass
                del record[rel]
        CK.save_copy_record(root, record)
        for x in gone:
            d = os.path.join(root, "data", "locale", x)
            for dp, _dns, _fns in sorted(os.walk(d, topdown=False)):
                try:
                    os.rmdir(dp)
                except OSError:
                    ctx.say("target: %s keeps files Kiln did not copy there" % dp)
        copy = target_copy(ctx, man, {CK.key("data/" + L.FILE_NAME)})
        copy["removed"] = removed
    ctx.event("done", removed=gone, locales=cl.locales, default=cl.default_locale, copy=copy)
    return 0


def locales_doc(cl):
    return {"primary": cl.locale, "all": cl.locales, "extra": cl.secondary, "default": cl.default_locale,
            "sources": {x: {"client": cl.source_of(x).game_dir, "sha256": cl.source_of(x).sha256} for x in cl.locales},
            "layout": "data/ = primary locale; data/locale/<xxYY>/ = the localized files of the others"}


def prepare_mode(ctx, later=None):
    """--update / --repair: which recorded steps are re-made. Sets --steps to the recorded steps and returns
    (narrow(chosen) -> the steps to re-make, the target files the repair check found bad). A language added
    later: the new locales' copies of the localized steps the output was made with."""
    a = ctx.a
    if later:
        man = Manifest(ctx.out, ctx.data)
        made = {n.split("@", 1)[0] for n, st in man.doc.get("steps", {}).items() if st.get("status") == "ok"}
        a.steps = ",".join("@" + x for x in later)

        def narrow(steps):
            copies = [st for st in steps if st.base and ctx.step_locale(st) in later]
            return [st for st in copies if st.base_name in made] or copies
        return narrow, set()
    if not a.mode:
        return (lambda steps: steps), set()
    man = Manifest(ctx.out, ctx.data)
    recorded = man.doc.get("steps", {})
    if a.update:
        names = set(recorded) | {n.split("@", 1)[0] for n in recorded}   # + the copies for a newly added locale
        a.steps = ",".join(sorted(names))
        said = set()

        def narrow(steps):
            out = []
            for st in steps:
                why = update_reason(ctx, man, st)
                if why:
                    out.append(st)
                    if st.name not in said:
                        said.add(st.name)
                        ctx.say("update: %-34s %s" % (st.name, why))
                        ctx.event("update", step=st.name, reason=why, locale=ctx.step_locale(st))
            return out
        return narrow, set()
    # repair: the full check first
    rep = CK.Report(ctx)
    ctx.say("repair: checking %s" % ctx.out)
    CK.check_out(ctx, man, rep, full=True)
    if a.deep:
        deep_verify(ctx, man, rep)
    bad_paths = set()
    if a.target:
        CK.check_target(ctx, man, a.target, rep, full=True)
        bad_paths = {CK.key(i["path"]) for i in rep.problems("target") if i["path"]}
    fix = {i["step"] for i in rep.problems("out") if i["kind"] in ("missing", "size", "hash", "failed", "verify")}
    ctx.say("repair: %d step(s) to re-make%s, %d target file(s) to re-copy" % (
        len(fix), (" (" + ", ".join(sorted(fix)) + ")") if fix else "", len(bad_paths)))
    a.steps = ",".join(sorted(fix)) if fix else "-none-"
    return (lambda steps: [st for st in steps if st.name in fix]), bad_paths


def update_reason(ctx, man, st):
    """Why --update re-makes a step (None: it is current)."""
    rec = man.step(st.name)
    if rec is None:
        return "new (a locale added since the last run)"
    if rec.get("status") != "ok":
        return "failed last time"
    if "identity" not in rec:
        return "identity unknown (recorded by an older Kiln)"
    ident = ctx.step_identity(st)
    if rec["identity"] != ident:
        return "changed: " + CK.identity_diff(rec["identity"], ident)
    if rec.get("key") != ctx.step_key(st):
        return "command line changed"
    if not man.up_to_date(st.name, rec["key"], ident):
        return "files missing or changed"
    return None


def do_check(ctx):
    a = ctx.a
    man = Manifest(ctx.out, ctx.data)
    rep = CK.Report(ctx)
    t0 = time.time()
    ctx.say("check: %s (SHA-256 of every output file)" % ctx.out)
    nfiles, nbytes = CK.check_out(ctx, man, rep, full=True)
    if a.deep:
        deep_verify(ctx, man, rep)
    root = CK.check_target(ctx, man, a.target, rep, full=True) if a.target else None
    by = rep.count()
    probs = rep.problems()
    ctx.say("check: %d files (%.1f MB) in %d steps; %d problem(s)%s, %d extra file(s)%s; %.0f s" % (
        nfiles, nbytes / 1048576.0, len(man.doc.get("steps", {})), len(probs),
        (" (" + ", ".join("%s %d" % kv for kv in sorted(by.items()) if kv[0] != "extra") + ")") if probs else "",
        by.get("extra", 0), ("; target %s" % root) if root else "", time.time() - t0))
    ctx.event("done", check=True, ok=not probs, files=nfiles, bytes=nbytes, problems=len(probs), by_kind=by,
              extra=by.get("extra", 0), target=root, hash=man.doc.get("hash", "sha256"),
              seconds=round(time.time() - t0, 1))
    return 1 if probs else 0


def deep_verify(ctx, man, rep):
    """The converters' own verify passes of the recorded steps that have one."""
    recorded = man.doc.get("steps", {})
    spec = ",".join(sorted(recorded))
    groups = map_groups_wanted(spec)
    all_steps, maps_note = build_steps(ctx, groups)
    steps = {s.name: s for s in select(all_steps, spec, set())}
    exes = converter_exes(ctx, steps.values(), groups)
    use_dlls(ctx, exes)
    for exe, rc, miss in winrt.preflight([e for e in exes if os.path.exists(e)], ctx.env(), [ctx.dll_dir, ctx.tools]):
        rep.add("out", "verify", step=os.path.basename(exe), detail="cannot start: %s%s" % (
            rc, ("; missing " + ", ".join(miss)) if miss else ""))
        return
    if maps_note:
        ctx.say("check: the map steps' verify passes need the map table in the work folder - skipped (%s)"
                % maps_note)
    for name, st in sorted(steps.items()):
        if not st.verify or (recorded.get(name) or {}).get("status") != "ok":
            continue
        log = os.path.join(ctx.logs, name + ".verify.log")
        for cmd in st.verify:
            if run_cmd(ctx, ctx.expand(cmd, st), log, ctx.folder(st, "W")):
                rep.add("out", "verify", step=name, locale=ctx.step_locale(st),
                        detail="verify pass failed (logs/%s.verify.log)" % name)
                break


def verify_locales_txt(ctx):
    """--verify: data/locales.txt must be there, readable, and match this run's locales. -> problems"""
    cl = ctx.client
    try:
        doc = L.read(ctx.data)
    except (OSError, L.LocalesError) as e:
        problem = "missing or unreadable (%s)" % e
    else:
        want = {"primary": cl.locale, "extra": cl.secondary, "default": cl.default_locale}
        diff = [k for k in want if doc[k] != want[k]]
        missing = [x for x in doc["extra"] if not os.path.isdir(os.path.join(ctx.data, "locale", x))]
        if not diff and not missing:
            return 0
        problem = "; ".join((["%s is %s, this run: %s" % (k, doc[k], want[k]) for k in diff]) +
                            ["data/locale/%s/ missing" % x for x in missing])
    ctx.say("  %-24s %-50s %s" % ("locales", "data/" + L.FILE_NAME, problem))
    ctx.event("verify", step="locales", path="data/" + L.FILE_NAME, problem=problem, locale="")
    return 1


def missing_programs(ctx, chosen):
    need = {"@exe:psp_mpq_extract"}
    for s in chosen:
        need |= set(s.programs())
    return sorted(ctx.program_path(t) for t in need if not os.path.exists(ctx.program_path(t)))


def write_personal_note(ctx):
    text = PERSONAL_USE.format(version=KILN_VERSION, date=time.strftime("%Y-%m-%d"), build=ctx.client.build,
                               locales=", ".join(ctx.client.locales), app=ctx.a.app)
    with open(os.path.join(ctx.out, "README_PERSONAL_USE.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(text)


def dry_run(ctx, chosen, maps_note):
    man = Manifest(ctx.out, ctx.data) if os.path.isdir(ctx.data) else None
    if ctx.json:
        ctx.event("plan", dry_run=True, steps=plan_doc(ctx, chosen, man), maps_note=maps_note,
                  not_provided=S.NOT_PROVIDED)
        return 0
    print("\nPLAN (dry run - nothing is written)")
    print("  output data : %s" % ctx.data)
    print("  work folder : %s" % ctx.work)
    print("  converters  : %s" % ctx.tools)
    print("  bakers      : %s%s" % (ctx.bakers, "" if os.path.isdir(ctx.bakers) else "  (NOT FOUND)"))
    cl = ctx.client
    for loc in cl.locales:
        print("  locale %s : %s from %s" % (loc, "data/" if loc == cl.locale else "data/locale/%s/" % loc,
                                          cl.data_dir_for(loc)))
    print("  locales.txt : primary %s, extra %s, default %s" % (cl.locale, ", ".join(cl.secondary) or "none",
                                                              cl.default_locale))
    missing = missing_programs(ctx, chosen)
    print("  programs    : %s" % ("all found" if not missing else "%d missing: %s" % (
        len(missing), ", ".join(os.path.basename(m) for m in missing))))
    print("  DLLs        : %s" % (ctx.dll_dir or "next to the converters / system"))
    if not missing and start_check(ctx, converter_exes(ctx, chosen, map_groups_wanted(ctx.a.steps))):
        return 2
    by_loc = {}
    for s in chosen:
        by_loc.setdefault(ctx.step_locale(s), set()).update(s.prefixes)
    exe = ctx.program_path("@exe:psp_mpq_extract")
    print("\n1. extract (read-only on the archives):")
    for loc, pre in sorted(by_loc.items()):
        pre = sorted(pre)
        if not pre:
            continue
        print("   [%s] %d client path prefixes: %s" % (loc, len(pre), " ".join(pre)))
        if os.path.exists(exe):
            argv = [exe, "--data", ctx.client.data_dir_for(loc), "--out", ctx.x if loc == ctx.client.locale else
                    ctx.locale_folders(loc)["X"], "--locale", loc, "--dry-run"]
            for p in pre:
                argv += ["--prefix", p]
            r = subprocess.run(argv, capture_output=True, text=True, env=ctx.env(argv), errors="replace")
            lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
            print("   extractor dry run (rc %d): %s; %s" % (r.returncode, lines[0] if lines else "no output",
                                                         lines[-1] if lines else ""))
    if any(s.png for s in chosen):
        print("\n2. PNG side files for the interface art (built into Kiln), per extracted tree")
    probes = [(s, p) for s in chosen for p in s.probes]
    if probes:
        print("\n3. probes (list client files the archives do not name, then extract exactly those):")
        for s, p in probes:
            print("   %-14s %s" % (s.name, show_cmd(ctx.expand(p.cmd, s)) if p.cmd else "(Kiln: %s list)" % p.builtin))
    print("\n4. steps (%d):" % len(chosen))
    for i, (s, row) in enumerate(zip(chosen, plan_doc(ctx, chosen, man)), 1):
        print("  %3d. %-28s [%s] %s" % (i, s.name, row["state"], s.title))
        if s.builtin:
            print("        (done by Kiln)")
        for c in s.run:
            print("        $ %s" % show_cmd(ctx.expand(c, s)))
        for c in s.verify:
            print("        verify $ %s" % show_cmd(ctx.expand(c, s)))
        if s.locale == "names":
            print("        (localized names and map art: primary locale %s only)" % ctx.client.locale)
    if maps_note:
        print("\n  note: " + maps_note)
    for k, v in S.NOT_PROVIDED.items():
        print("  not provided: %s - %s" % (k, v))
    print("\nThen copy %s to ms0:/PSP/GAME/%s/data next to the EBOOT.PBP." % (ctx.data, ctx.a.app))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
