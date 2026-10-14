# Kiln

**Need help? Join the Sandstone_PSP Discord: [discord.gg/wV8x46ZTDu](https://discord.gg/wV8x46ZTDu)**

**Kiln** is a Windows command-line tool that builds the data folder of a PSP homebrew port of an
open-source game client. It runs on **your** PC and reads **your own** client files; nothing is
downloaded and nothing leaves your machine.

Kiln is for a legally owned copy of the 3.3.5a (build 12340) PC client.

No game files are included here, and none are ever written into this folder. Kiln only reads the
client folder you point it at and writes a `data/` folder that the port's EBOOT loads.

---

## What it does

1. **Identifies the client** by the SHA-256 hash of its executable. Only build 12340 is supported;
   any other version is refused with a message that lists what was found. The hash identifies the
   *version* of the files. It does not and cannot check who owns them. A client from a private-server
   distribution whose executable differs only at the few places such distributions patch (the
   large-address-aware flag, the PE checksum, a handful of code bytes) is
   accepted as the same release: the log says "patched executable".
2. **Extracts** the files the converters need from the client's MPQ archives into a work folder
   (read-only: the archives are never written, the client may stay open).
3. **Converts** them: every converter and baker of the port runs (independent steps in parallel
   with `--jobs`) and writes the PSP data files into `<out>/data/` (also `data/bigwmo/`,
   `data/music/` and `data/expansions/`).
4. **Converts every locale** your client has (`Data/<xxYY>/locale-<xxYY>.MPQ`), and the locales of
   any other copy of the game you add with `--add-locale`: the primary locale goes to `data/`, the
   localized files of every other locale to `data/locale/<xxYY>/`, so the port can switch language
   (see *Languages*). The list is written to `data/locales.txt` and recorded in the manifest.
5. **Makes menu art** for your copy (`xmb/ICON0.PNG`, `xmb/PIC1.PNG`) from your own client's art; the
   published EBOOT carries neutral art. With `--eboot <your EBOOT.PBP>` Kiln also writes
   `xmb/EBOOT.PBP` with that art packed in.
6. **Records** what it made in `<out>/data/kiln.manifest.json` (tool version, client build, locales,
   every file's size and SHA-256, the status and duration of every step) and writes
   `<out>/README_PERSONAL_USE.txt`.
7. **Tells you where to copy it**: `ms0:/PSP/GAME/<APP>/data`, next to the port's `EBOOT.PBP`.
   The EBOOT is distributed separately and contains no game data.

A second run skips every step whose files are still present and match the manifest (and whose
converter did not change), so an update rebuilds only what changed.

## Requirements

- Windows 10 or later.
- Python 3.11 or later with Pillow and numpy (`python -m pip install pillow numpy`).
- The converter executables in `bin/` (or any folder passed with `--tools`), with the runtime
  libraries they were built with (`--dll-dir`). See *Building the converters*.
- Your own installed 3.3.5a (build 12340) PC client.
- A PSP that runs homebrew, and the port's EBOOT (distributed separately).
- Free disk space: roughly 10 GB for the work folder and 10 GB for the output (estimate for a full
  run; a single step needs far less).

## Usage

```
python kiln.py --game <client folder> --out <output folder> --dry-run
python kiln.py --game <client folder> --out <output folder> --jobs 4
```

`<client folder>` is the folder that holds the client's executable and its `Data` folder.
`<output folder>` must be outside the Kiln folder and outside the client folder.

| option | meaning |
|---|---|
| `--dry-run` | check the client version and print the whole plan (prefixes, commands, outputs); writes nothing |
| `--steps LIST` | run only some steps or groups: `default`, `all`, `tables`, `interface`, `models`, `audio`, `world`, `maps`, `continents`, `dungeons`, `battlegrounds`, `arenas`, `locales` (the extra locales' copies), `@xxYY` (one locale), a step name (also selects its locale copies), or a wildcard like `terrain_*` |
| `--skip LIST` | leave steps out |
| `--jobs N` | run N steps at once (and give converters that take a worker count N workers) |
| `--verify` | re-check an earlier output against the manifest and re-run the converters' verify passes |
| `--check` | integrity check of an earlier output (see *Checking, updating, repairing*); `--deep` adds the converters' verify passes |
| `--update` | re-make only the recorded steps whose converters, bakers or input changed (or that are damaged, failed or unknown) |
| `--repair` | `--check`, then re-make the steps with damaged or missing files and re-copy damaged or missing files of `--target` |
| `--target DIR` | a copy of the game folder, e.g. `ms0:/PSP/GAME/<APP>/` on the memory stick: only missing or different files are copied into its `data/` |
| `--server HOST[:PORT]` | the realm list server of a 3.3.5a server you may play on: written as `server=` (and `port=`) into `wowee.cfg` of the game folder (`--target`, else `--out`); the other lines of the file stay. Kiln has no default server |
| `--force` | re-run steps that are up to date |
| `--tools DIR` | converter executables (default `bin/`) |
| `--bakers DIR` | Python bakers and profiles (default `bakers/`) |
| `--dll-dir DIR` | runtime libraries of the converters (added to PATH); default: none when they are next to the converters, else `KILN_DLL_DIR`, else an MSYS2 UCRT64 `bin` folder (the log says which) |
| `--work DIR` | where extracted client files go (default `<out>/work`); a fast disk helps most |
| `--mirror DIR` | a folder on a fast disk that the items converter fills with the files it reads |
| `--locale xxYY` | the primary locale (written to `data/`) |
| `--locales LIST` | the other locales of `--game` to convert (default all present, `none` for only the primary) |
| `--add-locale DIR` | another client folder of the same build in another language (repeatable): its locales become extra locales (see *Languages*) |
| `--default-locale xxYY` | the language the game starts in: the primary locale or an extra one (default: the primary) |
| `--remove-locale xxYY` | remove an extra locale from an earlier output (see *Add a language later*) |
| `--eboot FILE` | your copy of the port's EBOOT: `xmb/EBOOT.PBP` gets the menu art made from your client |
| `--json` | progress as one JSON object per line on stdout (for a front end; see below) |
| `--app NAME` | the folder name under `ms0:/PSP/GAME/` (default `WOWEEPSP`) |
| `--list-steps` | print the step table (with `--json`: one JSON object, `"event": "steps"`) |

Logs go to `<out>/logs/` (one file per step, plus `kiln.log` and `extract_<xxYY>.log`).

A failed step does not start the rest blindly: a converter that cannot start stops the queue, the other
locales' copies of a failed step are not started, and a converter that failed in three steps is not
started again in that run. Those steps are reported as `blocked` (and count as failed); the others go on.

### Speed

A full convert reads tens of thousands of files and writes several GB. What helps:

- `--jobs N`: independent steps run in parallel, and converters that take a worker count get N.
- `--work` on an SSD (the extracted tree is read many times), and `--mirror` for the items step.
- Re-runs skip finished steps (manifest + converter hashes); `--steps` runs a part only.
- The extraction is recorded per work tree (`lists/extracted_prefixes.json`): a re-run or `--update`
  does not scan the archives again for client paths it already extracted from the same archives
  (that scan alone takes minutes on a busy disk); `--force` extracts them again.
- The Python bakers (`text`, `ui`, `glue`, `spells`, `itemicons`, `tutorial`, `craft`, `taxi` and the
  other `build_<name>_pak.py` bakers) and Kiln's own BLP-to-PNG pass are single-threaded Python;
  `ui` takes about two minutes, the map and model converters take from minutes to hours.

### Machine-readable progress

With `--json`, stdout carries one JSON object per line and nothing else: `client` (version, hash,
locales: `primary`, `extra`, `default`, the client folder each locale comes from and the notes about
added clients), `plan` (every step with its locale, state and last duration), `phase` (extraction
per locale, PNG pass, probes), `step` (start / ok / failed / skipped with the step's `locale`,
seconds, files and bytes), `running` (every 10 s: the steps in progress, their elapsed seconds and
their locales), `log` (the text lines), `verify`, `done` and `error`, and for the modes above `check`, `update` and
`copy`. A front end can drive Kiln with nothing but this stream and the exit code.

### Steps

`python kiln.py --list-steps` prints the full table with the exact command lines. In short:

| group | files |
|---|---|
| tables | `expansions/<id>/*.json`, `dbc.pak`, `light.pak`, `text.pak`, `craft.pak`, `taxi.pak`, `spells.pak`, `itemicons.pak` |
| interface | `ui.pak`, `glue.pak`, `framexml.pak`, `emblems.pak`, `tutorial.pak`, every other `build_<name>_pak.py` baker of the port (`<name>.pak`), `xmb/` menu art |
| audio | `sounds.pak` (+ `sounds_N.pak`), `audio.pak` + `music/` |
| models | `characters.pak`, `items.pak`, `creatures.pak`, `spellfx.pak`, `gameobjects.pak`, `transports.pak`, `gameobjects_wmo.pak` |
| world | `worldmap.pak` |
| maps | per map: `terrain_<map>.pak`, `doodads_<map>.pak` (+ `bigwmo/<map>_*.pak`), `minimap_<map>.pak`. The maps are read from your client's own map table: the four open-world maps, the dungeons and raids (the port's own list), the battlegrounds; arenas on request (`--steps arenas`) |

Localized per locale (re-made for every extra locale into `data/locale/<xxYY>/`): `text`, `ui`, `glue`,
`framexml`, `tutorial`, `spells`, `craft`, `taxi`, the other Python bakers, `sounds` (voices), and the
emote text table of `characters` (`emotetext.pak`). `worldmap.pak` and `minimap_<map>.pak` are made
for the primary locale only (too large to duplicate per language), although they are localized: they
hold the zone names and the world map art, whose textures carry painted place names and come from the
locale archives (checked: 549 of the 1393 entries of one continent's minimap pak differ between an
English and a Russian client). With an extra locale the game shows the primary's map labels.

Not produced by Kiln:

- `movies/`: the cinematics need a PSP movie encoder that is not freely available. The port skips
  a missing movie.
- `music/original/`: the upstream project's own composed music is not part of the client, and its
  licence does not let Kiln copy it.

## Checking, updating, repairing

The manifest (`data/kiln.manifest.json`) records for every step its status, its command line, every
output file (path, size, SHA-256) and the step's **identity**: the SHA-256 of every converter
executable and baker script it ran (for the Python bakers also of their helper modules and art and
the Python, Pillow and NumPy versions; for steps done by Kiln itself, Kiln's version) and its
**input**: the SHA-256 of the client executable its locale was read from, the locale and the build.
File hashes are SHA-256 (Python's hashlib on OpenSSL, much faster than the disks it reads; no extra
dependency).

```
python kiln.py --game <client> --out <output> --check [--target <stick>/PSP/GAME/<APP>] [--deep] [--json]
python kiln.py --game <client> --out <output> --update [--add-locale <client>] [--target ...] [--json]
python kiln.py --game <client> --out <output> --repair [--target ...] [--json]
```

- `--check` reads every output file and compares it with the manifest (missing, other size, other
  SHA-256), reports steps that failed, steps recorded by an older Kiln without an identity
  (`unknown`), steps whose converters, bakers or input differ from the recorded identity (`outdated`),
  a `data/locales.txt` that does not match, files of 4 GB or more (FAT32), and files under `data/` or
  `xmb/` that no step made (`extra`: a warning, never deleted). With `--target` it checks the copy the
  same way (relative paths under its `data/`; `data/movies/` and `data/music/original/` are the
  player's own and never reported). `--deep` re-runs the converters' own verify passes. Exit code 0:
  no problem (extra files do not count); 1: problems.
- `--update` re-makes only the steps of the manifest (and their copies for a locale added with
  `--add-locale`) that failed, are unknown or outdated, whose command line changed, or whose files are
  missing or changed (size, or modification time + SHA-256, as a normal run). Steps that were never
  made are not added: run Kiln normally for those.
- `--repair` runs `--check` and re-makes the steps with missing or damaged files (and the failed
  ones); with `--target` it re-copies the target files that were missing or damaged.
- `--target DIR` (with `--update`, `--repair` or a normal run): after the steps, copies every
  missing or different file of `data/` (and `data/locales.txt`) into `DIR/data/`; nothing is deleted.
  A file is the same when it has the same size and, from Kiln's copy record `DIR/data/kiln.copy.json`,
  the same modification time and hash, else when its SHA-256 matches. Files of 4 GB or more are not
  copied (FAT32).
- Without `--add-locale`, these modes reuse the added clients and the start language recorded by the
  last run when those client folders still exist.

With `--json`: one `check` event per problem or extra file (`where` out/target, `problem`, `path`,
`step`, `locale`, `detail`), `update` events (step, reason), `copy` events, and a final `done` event
(for `--check`: `ok`, `files`, `bytes`, `problems`, `by_kind`, `extra`).

## Languages

The port shows the game in the languages of the client files it was made from. Kiln converts:

- every locale of the client folder (`--game`); the primary one (`--locale`, else the only one, else
  English, else the first by name) goes to `data/`, the others to `data/locale/<xxYY>/`
  (`--locales` limits them);
- with `--add-locale <folder>`, the locales of another copy of the game in another language. It must
  be the same build (3.3.5a, build 12340): Kiln identifies it by the same executable hash check and
  refuses an unknown or different version. Every locale in its `Data/<xxYY>/` that is not the main
  client's primary becomes an extra locale: its files are extracted from that copy's own archives
  and its localized files converted into `data/locale/<xxYY>/`. A locale that the main client has
  too is taken from the main client (Kiln says so). The option can be repeated.

Only the localized files (see *Steps*) are made per locale; everything else (models, maps, music) is
made once and is the same for every language.

```
python kiln.py --game <English client> --add-locale <client in another language> --default-locale ruRU --out <output folder>
```

Kiln writes `data/locales.txt` on every run (also when there is no extra locale), UTF-8 with LF line
ends:

```
# Kiln locales v1
primary enUS
extra ruRU
default ruRU
```

Exactly one `primary` (the locale of `data/`), zero or more `extra` (each has its files in
`data/locale/<xxYY>/`), exactly one `default`. Readers ignore lines they do not know.

Which language the game uses:

1. **In the game**: the language chosen in the game's menu or settings. The EBOOT stores it as
   `locale=xxYY` in `wowee.cfg` (next to the EBOOT on the memory stick); it wins over everything else.
2. **Launch parameter**: write `locale=xxYY` into `wowee.cfg` yourself, or choose the start language
   when you convert, with `--default-locale xxYY` (the `default` line of `locales.txt`).
3. Otherwise the game starts in the `default` locale, which is the primary one (the language of the
   main client you chose) unless `--default-locale` names an extra. Adding a language never changes
   the start language by itself.

### Add a language later

Converted once, and later got a copy of the game in another language? Add it to the existing output
without converting anything else - the main client folder does not even have to be there any more:

```
python kiln.py --out <earlier output> --add-locale <client in the other language> [--target <stick>/PSP/GAME/<APP>] [--json]
python kiln.py --out <earlier output> --remove-locale ruRU [--target ...] [--json]
```

- `--add-locale` without `--game`: the primary locale and the build come from the output's manifest.
  The new client must be the same build (hash check). Kiln extracts only the localized client paths
  from it, runs only that locale's localized steps (the ones the output was made with) into
  `data/locale/<xxYY>/`, adds an `extra` line to `data/locales.txt` (the `default` stays) and records
  it in the manifest. With `--target` it copies only the new files and `locales.txt` to the stick.
  Giving the same client again re-makes nothing that is finished.
- `--remove-locale xxYY` deletes `data/locale/<xxYY>/`, its steps in the manifest and its `extra`
  line (the primary cannot be removed; if it was the start language, the primary becomes it). With
  `--target` it also deletes the files Kiln copied there for it and updates `locales.txt` there.
  Its extracted files stay in the work folder until you delete it.
- A step that needs the main client's own files is refused with a message asking for `--game`
  (none of the localized steps does).

## Building the converters

The converters are C++ (a CMake project). `bakers/` holds their sources, the upstream sources they
compile, the Python bakers and the licences. In an MSYS2 UCRT64 shell with gcc, cmake, ninja, glm,
meshoptimizer and StormLib installed:

```
cmake -S bakers/tools/psp/converter -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Copy the `psp_*.exe` files into `bin/` (or pass `--tools build`) and copy the DLLs the executables
import next to them (the bundle's layout), or pass the MSYS2 `ucrt64/bin` folder as `--dll-dir` (Kiln
also finds it by itself at the installer's default place).

Before a run Kiln starts every converter it needs once (each prints its usage and exits). One that
cannot start - typically a missing DLL - stops Kiln at once, before any extraction, with the names of
the missing DLLs (read from the executables' import tables). Windows' error dialogs are switched off
for Kiln and the converters: a missing DLL is an exit code and a message, never a dialog per start.

Kiln can also run against the port's repository directly (`--bakers <port repository>`; the layout
is the same).

## GUI and Windows bundle

`gui/` is Kiln's Windows window (C# WinForms, .NET 8): one page in four steps - (1) the game folder
(checked at once with a dry run: version and languages), (2) other-language copies of the client
(`--add-locale`, start language `--default-locale`), (3) where the data goes: a PSP memory stick
(removable drives with a `PSP` folder are found by themselves; Kiln makes the data on the PC and
copies only what changed, `--target`) or a folder, (4) **Start** (resumes: finished steps are
skipped), **Update assets** (`--update`), **Check integrity** (`--check`) and **Repair**
(`--repair`). Advanced settings (work folder on the fastest local drive with room, jobs = physical
cores with one thread left free, what to make, low priority, rebuild, EBOOT for the menu art) are
pre-set for a full run. The Steps tab shows every step's state, time and output; the Summary tab
says what was made and where to copy it. The window drives `kiln.py` only through `--json` and the
exit code; it adds no logic of its own except the memory-stick copy when `kiln.py` has no `--target`.

```
dotnet build gui/Kiln.csproj                     # development: runs kiln.py with the Python on PATH
gui/bin/Debug/net8.0-windows/Kiln.exe
Kiln.exe --dry-run --game <client> --out <output>   # the window's dry run on the console
```

A ready-made Windows bundle (`Kiln.exe`, an embedded Python, the converters and their DLLs, and the
licence text of every component in `THIRD_PARTY/`) is attached to each release.

## Your output is for you only

The `data/` folder Kiln writes is made from your own copy of the game and contains material that
belongs to the game's rights holder. Use it on your own device. **Do not share, upload or sell it,
or any archive, image or build that contains it.** The same applies to the work folder (it holds
extracted client files): delete it when you no longer need it.

Kiln will never help you obtain the game, and questions about where to get it will not be answered.

## Credits

- **SandstoneDev** - Kiln and the PSP port. Developed with the help of
  [Claude Code](https://www.anthropic.com/claude-code) (Anthropic).
- **WoWee** by **Kelsi Davis** and the Wowee contributors -
  [github.com/Kelsidavis/WoWee](https://github.com/Kelsidavis/WoWee): the open-source client the port
  and its converters are built on (credit required by its licence; see `NOTICE`).
- Third-party libraries and art: see `NOTICE`.

## Disclaimer

Kiln is provided for interoperability and homebrew use with a copy of the game that you legally own.
This project ships no game data. It is not affiliated with, authorized or endorsed by the game's
publisher or by the maker of the PSP; all trademarks belong to their respective owners. Kiln is free
and non-commercial. Rightsholders with concerns are welcome to contact the maintainer at
`sandstonedevpsp@gmail.com` before filing a formal notice.
