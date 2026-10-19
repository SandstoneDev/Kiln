"""Kiln self-tests on synthetic data only (no client files are needed or used).

    python -m unittest discover -s tests
"""
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from kilnlib import blp, client, locales as L, mapdb, steps as S  # noqa: E402
from kilnlib.manifest import Manifest  # noqa: E402


def blp2(comp, alpha_depth, alpha_enc, w, h, top, palette=None):
    hdr = struct.pack("<4sIBBBBII", b"BLP2", 1, comp, alpha_depth, alpha_enc, 0, w, h)
    offs = [0] * 16
    sizes = [0] * 16
    head = len(hdr) + 128 + 1024
    offs[0], sizes[0] = head, len(top)
    pal = struct.pack("<256I", *(palette or [0] * 256))
    return hdr + struct.pack("<16I", *offs) + struct.pack("<16I", *sizes) + pal + top


class BlpTest(unittest.TestCase):
    def test_dxt1_four_colour(self):
        # c0 = pure red (0xF800) > c1 = pure blue (0x001F): four opaque colours
        block = struct.pack("<HHI", 0xF800, 0x001F, 0b11100100 | (0b11100100 << 8) | (0b11100100 << 16) | (0b11100100 << 24))
        img = blp.decode(blp2(2, 0, 0, 4, 4, block))
        self.assertEqual(img.shape, (4, 4, 4))
        self.assertEqual(tuple(img[0, 0]), (255, 0, 0, 255))     # index 0
        self.assertEqual(tuple(img[0, 1]), (0, 0, 255, 255))     # index 1
        self.assertEqual(tuple(img[0, 2]), (170, 0, 85, 255))    # (2*c0 + c1) / 3, truncated
        self.assertEqual(tuple(img[0, 3]), (85, 0, 170, 255))

    def test_dxt1_punch_through(self):
        block = struct.pack("<HHI", 0x001F, 0xF800, 0xFFFFFFFF)   # c0 <= c1, every index 3
        img = blp.decode(blp2(2, 1, 0, 4, 4, block))
        self.assertTrue((img[:, :, 3] == 0).all())

    def test_rgb565_expansion(self):
        # 5-bit 1 -> 1 * 255 // 31 = 8 (not 1 << 3 = 8 either way); 5-bit 30 -> 246 (bit shift gives 240)
        block = struct.pack("<HHI", (30 << 11) | (62 << 5) | 30, 0, 0)
        img = blp.decode(blp2(2, 0, 0, 4, 4, block))
        self.assertEqual(tuple(img[0, 0, :3]), (30 * 255 // 31, 62 * 255 // 63, 30 * 255 // 31))

    def test_dxt5_alpha(self):
        alpha = bytes([255, 0]) + bytes(6)                         # all indices 0 -> alpha 255
        color = struct.pack("<HHI", 0xFFFF, 0, 0)
        img = blp.decode(blp2(2, 8, 7, 4, 4, alpha + color))
        self.assertTrue((img[:, :, 3] == 255).all())
        self.assertTrue((img[:, :, :3] == 255).all())

    def test_palette_alpha8(self):
        pal = [0] * 256
        pal[1] = (0x10 << 16) | (0x20 << 8) | 0x30                  # R, G, B
        top = bytes([1, 0, 1, 0]) + bytes([200, 100, 50, 25])
        img = blp.decode(blp2(1, 8, 0, 2, 2, top, pal))
        self.assertEqual(tuple(img[0, 0]), (0x10, 0x20, 0x30, 200))
        self.assertEqual(tuple(img[1, 1]), (0, 0, 0, 25))

    def test_odd_size_and_short_data(self):
        img = blp.decode(blp2(2, 0, 0, 6, 5, struct.pack("<HHI", 0xFFFF, 0xFFFF, 0)))
        self.assertEqual(img.shape, (5, 6, 4))
        self.assertTrue((img[4:, 4:] == 0).all())                  # blocks past the data stay zero

    def test_rejects_garbage(self):
        with self.assertRaises(blp.BlpError):
            blp.decode(b"NOPE" + bytes(2000))

    def test_empty_file_is_skipped_not_failed(self):
        # a private-server patch ships a 0-byte INV_Shoulder_94.tga.blp beside the real icon
        with tempfile.TemporaryDirectory() as d:
            icons = os.path.join(d, "interface", "icons")
            os.makedirs(icons)
            open(os.path.join(icons, "empty.tga.blp"), "wb").close()
            with open(os.path.join(icons, "real.blp"), "wb") as f:
                f.write(blp2(2, 0, 0, 4, 4, struct.pack("<HHI", 0xFFFF, 0xFFFF, 0)))
            notes = []
            w, k, f, z = blp.png_sidecars(d, ["interface"], log=notes.append)
            self.assertEqual((w, k, f, z), (1, 0, 0, 1))
            self.assertTrue(any("empty file in the client" in n for n in notes))

    def test_human_time(self):
        import kiln
        self.assertEqual(kiln.human_time(13976), "3 h 52 min 56 s")
        self.assertEqual(kiln.human_time(125), "2 min 05 s")
        self.assertEqual(kiln.human_time(9.4), "9 s")


def wdbc(rows, fields=66, strings=b"\0"):
    size = fields * 4
    recs = b"".join(struct.pack("<%dI" % fields, *(r + [0] * (fields - len(r)))) for r in rows)
    return struct.pack("<4sIIII", b"WDBC", len(rows), fields, size, len(strings)) + recs + strings


def wdt(tiles=(), global_wmo=None):
    out = b""
    mphd = 1 if global_wmo else 0
    out += b"DHPM" + struct.pack("<I", 32) + struct.pack("<8I", mphd, 0, 0, 0, 0, 0, 0, 0)
    main = [0] * 8192
    for x, y in tiles:
        main[(y * 64 + x) * 2] = 1
    out += b"NIAM" + struct.pack("<I", 8192 * 4) + struct.pack("<8192I", *main)
    if global_wmo:
        name = global_wmo.encode() + b"\0"
        out += b"OMWM" + struct.pack("<I", len(name)) + name
    return out


class MapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        x = self.tmp.name
        strings = b"\0OpenA\0InstB\0FieldC\0"
        rows = [[0, 1, 0], [33, 7, 1], [489, 13, 3]]
        rows[1] += [0] * 54 + [5]                                  # loading screen of the instance
        os.makedirs(os.path.join(x, "dbfilesclient"))
        with open(os.path.join(x, "dbfilesclient", "map.dbc"), "wb") as f:
            f.write(wdbc(rows, strings=strings))
        for d, data in (("opena", wdt([(32, 48), (31, 48)])), ("instb", wdt(global_wmo="a\\b.wmo")),
                        ("fieldc", wdt([(1, 2)]))):
            os.makedirs(os.path.join(x, "world", "maps", d))
            with open(os.path.join(x, "world", "maps", d, d + ".wdt"), "wb") as f:
                f.write(data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_select(self):
        chosen, allmaps = mapdb.select_maps(self.tmp.name, {"continents", "dungeons", "battlegrounds"})
        got = {(g, m.lower) for g, m in chosen}
        self.assertEqual(got, {("continents", "opena"), ("dungeons", "instb"), ("battlegrounds", "fieldc")})
        self.assertEqual(allmaps[0].tiles, [(31, 48), (32, 48)])   # x-major order
        self.assertTrue(allmaps[33].global_wmo)

    def test_map_steps(self):
        _, allmaps = mapdb.select_maps(self.tmp.name, {"continents"})
        st = S.map_steps("continents", allmaps[0])
        self.assertEqual([s.name for s in st], ["terrain_opena", "doodads_opena", "minimap_opena"])
        g = S.map_steps("dungeons", allmaps[33])
        self.assertIn("--global-max-tris", g[1].run[0])

    def test_map_steps_wait_for_the_wdts(self):
        # the map steps are read from the map table AND every map's WDT: until Kiln has extracted the WDTs
        # (lists/wdt_maps.txt) they are announced, not guessed from whatever another step extracted
        import shutil
        from types import SimpleNamespace
        from kilnlib.pipeline import Ctx, build_steps, map_wdt_names, WDT_LIST
        with tempfile.TemporaryDirectory() as work:
            a = SimpleNamespace(out=os.path.join(work, "out"), work=work, tools=None,
                                bakers=os.path.join(work, "no-bakers"), python=None, dll_dir=None, mirror=None, jobs=1,
                                json=False)
            cl = SimpleNamespace(secondary=[], locale="enUS")
            ctx = Ctx(a, cl, ROOT)
            shutil.copytree(self.tmp.name, ctx.x)
            steps, note = build_steps(ctx, ["continents", "dungeons"])
            self.assertTrue(note)
            self.assertFalse([s for s in steps if s.group.startswith("maps:")])
            self.assertEqual(map_wdt_names(os.path.join(ctx.x, "dbfilesclient", "map.dbc")),
                             ["World\\Maps\\FieldC\\FieldC.wdt", "World\\Maps\\InstB\\InstB.wdt",
                              "World\\Maps\\OpenA\\OpenA.wdt"])
            os.makedirs(ctx.lists)
            open(os.path.join(ctx.lists, WDT_LIST), "w").close()
            steps, note = build_steps(ctx, ["continents", "dungeons"])
            self.assertIsNone(note)
            self.assertEqual(sorted(s.name for s in steps if s.group.startswith("maps:")),
                             ["doodads_instb", "doodads_opena", "minimap_instb", "minimap_opena", "terrain_instb",
                              "terrain_opena"])

    def test_bigwmo_glob_of_a_long_map_name(self):
        # the converter cuts a map name over 22 characters in the bigwmo/ pak names
        m = mapdb.MapInfo(id=1, dir="TwentyThreeCharsMapName", itype=1, loading=1, wdt=True, global_wmo=True)
        doodads = S.map_steps("dungeons", m)[1]
        self.assertEqual(doodads.params["bigwmo"], "twentythreecharsmapnam")
        with tempfile.TemporaryDirectory() as out:
            os.makedirs(os.path.join(out, "data", "bigwmo"))
            for fn in ("doodads_twentythreecharsmapname.pak", "bigwmo/twentythreecharsmapnam__712ec90e.pak",
                       "bigwmo/othermap_x.pak"):
                open(os.path.join(out, "data", fn), "wb").close()
            from types import SimpleNamespace
            from kilnlib.pipeline import Ctx
            a = SimpleNamespace(out=out, work=None, tools=None, bakers=None, python=None, dll_dir=None, mirror=None,
                                jobs=1, json=False)
            got = Ctx(a, None, ROOT).outputs_glob(doodads)
            self.assertEqual([p.replace(os.sep, "/") for p in got],
                             ["data/bigwmo/twentythreecharsmapnam__712ec90e.pak",
                              "data/doodads_twentythreecharsmapname.pak"])


class ManifestTest(unittest.TestCase):
    def test_up_to_date(self):
        with tempfile.TemporaryDirectory() as out:
            data = os.path.join(out, "data")
            os.makedirs(data)
            p = os.path.join(data, "a.pak")
            with open(p, "wb") as f:
                f.write(b"x" * 100)
            m = Manifest(out, data)
            m.record("a", status="ok", key="k1", outputs=["data/a.pak"], rc=0, seconds=1)
            m.save()
            m2 = Manifest(out, data)
            self.assertTrue(m2.up_to_date("a", "k1"))
            self.assertFalse(m2.up_to_date("a", "k2"))
            with open(p, "wb") as f:
                f.write(b"y" * 100)
            os.utime(p, ns=(0, 0))
            self.assertFalse(m2.up_to_date("a", "k1"))
            self.assertEqual(m2.check_files(), [("a", "data/a.pak", "hash changed")])


class ClientTest(unittest.TestCase):
    def test_unknown_refused_and_known_accepted(self):
        with tempfile.TemporaryDirectory() as g:
            os.makedirs(os.path.join(g, "Data", "xxYY"))
            open(os.path.join(g, "Data", "xxYY", "locale-xxYY.MPQ"), "wb").close()
            with open(os.path.join(g, "game.exe"), "wb") as f:
                f.write(b"MZ synthetic")
            with self.assertRaises(client.ClientError) as e:
                client.identify(g)
            self.assertIn("unknown client version", str(e.exception))
            digest = client.sha256_file(os.path.join(g, "game.exe"))
            row = {"locale": "xxYY", "build": 1, "version": "t", "size": 12, "sha256": digest}
            client.KNOWN_CLIENTS.append(row)
            try:
                os.makedirs(os.path.join(g, "Data", "enUS"))
                open(os.path.join(g, "Data", "enUS", "locale-enUS.MPQ"), "wb").close()
                info = client.identify(g)
                self.assertEqual((info.exe, info.locale, info.build), ("game.exe", "enUS", 1))
                self.assertEqual(info.locales, ["enUS", "xxYY"])
                self.assertEqual(info.secondary, ["xxYY"])
                self.assertEqual(client.identify(g, "xxYY").locale, "xxYY")
            finally:
                client.KNOWN_CLIENTS.remove(row)

    def test_patched_executable_is_the_same_release(self):
        orig = b"MZ" + bytes(range(30))
        masked = bytearray(orig)
        masked[2:4] = bytes(2)
        masked[10:11] = bytes(1)
        row = {"locale": "xxYY", "build": 7, "version": "t", "size": len(orig), "sha256": hashlib.sha256(orig).hexdigest(),
               "masked_sha256": hashlib.sha256(masked).hexdigest(), "patch_sites": [(2, 2, "a"), (10, 1, "b")]}
        client.KNOWN_CLIENTS.append(row)
        try:
            with tempfile.TemporaryDirectory() as root:
                patched = bytearray(orig)
                patched[3] = 0xFF
                patched[10] = 0xEB
                info = client.identify(fake_client(root, "p", ["enUS"], bytes(patched)))
                self.assertEqual((info.build, info.patched), (7, ["a", "b"]))
                self.assertIn("patched executable (differs only at 2 known places: a, b)", info.label)
                self.assertEqual(client.identify(fake_client(root, "o", ["enUS"], orig)).patched, [])
                other = bytearray(patched)
                other[20] ^= 1  # a byte outside the sites: another executable
                with self.assertRaises(client.ClientError):
                    client.identify(fake_client(root, "x", ["enUS"], bytes(other)))
        finally:
            client.KNOWN_CLIENTS.remove(row)

    def test_table_wording(self):
        self.assertIn("does not and cannot verify", " ".join(client.__doc__.split()))


class GameCfgTest(unittest.TestCase):
    def test_server_line_written_and_other_lines_kept(self):
        from kilnlib import gamecfg
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "wowee.cfg")
            gamecfg.set_server(p, "logon.example.org", None)
            self.assertIn("server=logon.example.org", open(p, encoding="utf-8").read())
            with open(p, "w", encoding="utf-8") as f:
                f.write("# mine\nserver=old.example\nlocale=ruRU\nserver=dup\n")
            gamecfg.set_server(p, "example.org", 3725)
            lines = open(p, encoding="utf-8").read().splitlines()
            self.assertEqual(lines, ["# mine", "server=example.org", "locale=ruRU", "port=3725"])
        self.assertEqual(gamecfg.parse_server("host:3724"), ("host", 3724))
        for bad in ("a.b:99999", "bad host", "x:y"):
            with self.assertRaises(ValueError):
                gamecfg.parse_server(bad)


class StepsTest(unittest.TestCase):
    def test_names_unique_and_outputs_in_data(self):
        st = S.static_steps(os.path.join(ROOT, "no-bakers-here"))
        names = [s.name for s in st]
        self.assertEqual(len(names), len(set(names)))
        for s in st:
            for o in s.outputs:
                self.assertTrue(o.startswith(("data/", "xmb/")), o)

    def test_out_inside_kiln_refused(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "kiln.py"), "--game", ROOT, "--out",
                            os.path.join(ROOT, "out"), "--dry-run"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("outside the Kiln folder", r.stdout)

    def test_list_steps_json(self):
        import json
        r = subprocess.run([sys.executable, os.path.join(ROOT, "kiln.py"), "--list-steps", "--json"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0)
        doc = json.loads(r.stdout)  # one object, nothing else on stdout
        self.assertEqual(doc["event"], "steps")
        names = [s["step"] for s in doc["steps"]]
        self.assertIn("dbc", names)
        self.assertTrue(doc["map_steps"])
        self.assertTrue(all(w.startswith(("data/", "xmb/")) for s in doc["steps"] for w in s["writes"]))


class LocaleTest(unittest.TestCase):
    def test_copies(self):
        st = {s.name: s for s in S.static_steps(os.path.join(ROOT, "no-bakers-here"))}
        folders = {"X": "x2", "XG": "xg2", "D": "d2", "W": "w2", "L": "l2"}
        t = S.locale_copy(st["text"], "xxYY", folders)
        self.assertEqual((t.name, t.base_name, t.outputs), ("text@xxYY", "text", ["data/locale/xxYY/text.pak"]))
        c = S.locale_copy(st["characters"], "xxYY", folders)
        self.assertIn("--emotetext-only", c.run[0])
        self.assertEqual(c.outputs, ["data/locale/xxYY/emotetext.pak"])
        self.assertEqual(st["worldmap"].locale, "names")
        self.assertEqual(st["light"].locale, "")
        from kilnlib.pipeline import select
        chosen = select(list(st.values()) + [t], "text", set())
        self.assertEqual([x.name for x in chosen], ["text", "text@xxYY"])
        self.assertEqual([x.name for x in select(list(st.values()) + [t], "@xxYY", set())], ["text@xxYY"])


class XmbTest(unittest.TestCase):
    def test_pbp_roundtrip(self):
        from kilnlib import xmb
        sec = {n: (n.encode() * 3) for n in xmb.PBP_SECTIONS}
        sec["ICON1.PMF"] = b""
        data = xmb.write_pbp(0x10000, sec)
        version, back = xmb.read_pbp(data)
        self.assertEqual(version, 0x10000)
        self.assertEqual(back, sec)


def fake_client(root, name, locales, exe=b"MZ synthetic"):
    """A synthetic client folder: <root>/<name>/game.exe + Data/<xxYY>/locale-<xxYY>.MPQ (empty files)."""
    g = os.path.join(root, name)
    for loc in locales:
        os.makedirs(os.path.join(g, "Data", loc), exist_ok=True)
        open(os.path.join(g, "Data", loc, "locale-%s.MPQ" % loc), "wb").close()
    with open(os.path.join(g, "game.exe"), "wb") as f:
        f.write(exe)
    return g


class KnownExe:
    """Registers the synthetic executables' hashes as known clients for the duration of a test."""

    def __init__(self, *rows):
        self.rows = [{"locale": "xxYY", "build": b, "version": "t", "size": len(e), "sha256": hashlib.sha256(e).hexdigest()}
                     for e, b in rows]

    def __enter__(self):
        client.KNOWN_CLIENTS.extend(self.rows)

    def __exit__(self, *exc):
        for r in self.rows:
            client.KNOWN_CLIENTS.remove(r)


class LocalesFileTest(unittest.TestCase):
    CONTRACT = "# Kiln locales v1\nprimary enUS\nextra ruRU\ndefault ruRU\n"

    def test_render_matches_contract(self):
        self.assertEqual(L.render("enUS", ["ruRU"], "ruRU"), self.CONTRACT)
        self.assertEqual(L.render("enUS", []), "# Kiln locales v1\nprimary enUS\ndefault enUS\n")
        self.assertEqual(L.render("enUS", ["deDE", "ruRU"]),
                         "# Kiln locales v1\nprimary enUS\nextra deDE\nextra ruRU\ndefault enUS\n")

    def test_render_refuses_bad_default(self):
        for args in (("enUS", ["ruRU"], "frFR"), ("enUS", ["ruRU", "ruRU"], None), ("english", [], None)):
            with self.assertRaises(L.LocalesError):
                L.render(*args)

    def test_parse_ignores_unknown_lines(self):
        doc = L.parse("# Kiln locales v1\r\nprimary enUS\nfuture_key 42\n\n# note\nextra ruRU\nextra deDE\n"
                      "default deDE\n")
        self.assertEqual(doc, {"primary": "enUS", "extra": ["ruRU", "deDE"], "default": "deDE"})
        for bad in ("primary enUS\n", "primary enUS\nprimary ruRU\ndefault enUS\n",
                    "primary enUS\ndefault ruRU\n", "primary en\ndefault en\n"):
            with self.assertRaises(L.LocalesError):
                L.parse(bad)

    def test_write(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "data")
            path = L.write(data, "enUS", ["ruRU"], "ruRU")
            with open(path, "rb") as f:
                self.assertEqual(f.read(), self.CONTRACT.encode("utf-8"))   # UTF-8, LF only
            self.assertTrue(os.path.isdir(os.path.join(data, "locale", "ruRU")))
            self.assertEqual(L.read(data)["default"], "ruRU")
            L.write(data, "enUS", [])                                     # every run rewrites it
            self.assertEqual(L.read(data), {"primary": "enUS", "extra": [], "default": "enUS"})


class AddLocaleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge(self):
        main = fake_client(self.root, "main", ["enUS", "deDE"])
        other = fake_client(self.root, "other", ["enUS", "ruRU"])
        with KnownExe((b"MZ synthetic", 1)):
            cl = client.identify(main)
            added = client.identify_added(other, cl)
            notes = client.merge_added(cl, [added])
        self.assertEqual(cl.locales, ["enUS", "deDE", "ruRU"])
        self.assertEqual(cl.secondary, ["deDE", "ruRU"])
        self.assertEqual(cl.data_dir_for("ruRU"), os.path.join(other, "Data"))
        self.assertEqual(cl.data_dir_for("deDE"), os.path.join(main, "Data"))
        self.assertEqual(cl.data_dir_for("enUS"), os.path.join(main, "Data"))
        self.assertTrue(any("enUS is the primary" in n for n in notes), notes)

    def test_main_client_wins(self):
        main = fake_client(self.root, "main", ["enUS", "ruRU"])
        other = fake_client(self.root, "other", ["ruRU"])
        third = fake_client(self.root, "third", ["frFR"])
        fourth = fake_client(self.root, "fourth", ["frFR"])
        with KnownExe((b"MZ synthetic", 1)):
            cl = client.identify(main)
            cl.locales = [cl.locale]                       # --locales none
            notes = client.merge_added(cl, [client.identify_added(g, cl) for g in (other, third, fourth)])
        self.assertEqual(cl.locales, ["enUS", "ruRU", "frFR"])
        self.assertEqual(cl.data_dir_for("ruRU"), os.path.join(main, "Data"))
        self.assertEqual(cl.data_dir_for("frFR"), os.path.join(third, "Data"))
        self.assertTrue(any("ruRU is in the main client too - using the main client's" in n for n in notes), notes)
        self.assertTrue(any("frFR is already added from" in n for n in notes), notes)

    def test_refused(self):
        main = fake_client(self.root, "main", ["enUS"])
        unknown = fake_client(self.root, "unknown", ["ruRU"], exe=b"MZ other build")
        newer = fake_client(self.root, "newer", ["ruRU"], exe=b"MZ newer")
        with KnownExe((b"MZ synthetic", 1), (b"MZ newer", 2)):
            cl = client.identify(main)
            with self.assertRaises(client.ClientError) as e:
                client.identify_added(unknown, cl)
            self.assertIn("--add-locale", str(e.exception))
            self.assertIn("unknown client version", str(e.exception))
            with self.assertRaises(client.ClientError) as e:
                client.identify_added(newer, cl)
            self.assertIn("same build", str(e.exception))
            with self.assertRaises(client.ClientError) as e:
                client.identify_added(main, cl)
            self.assertIn("main client folder", str(e.exception))

    def run_kiln(self, *args):
        import contextlib
        import io
        import json
        import kiln
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kiln.main(list(args))
        return rc, [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]

    def test_cli_dry_run_json(self):
        main = fake_client(self.root, "main", ["enUS"])
        other = fake_client(self.root, "other", ["ruRU"])
        out = os.path.join(self.root, "out")
        base = ["--game", main, "--out", out, "--steps", "text", "--json", "--dry-run",
                "--bakers", os.path.join(self.root, "no-bakers"), "--tools", os.path.join(self.root, "no-tools")]
        with KnownExe((b"MZ synthetic", 1)):
            rc, ev = self.run_kiln(*base, "--add-locale", other, "--default-locale", "ruRU")
            self.assertEqual(rc, 0)
            cev = [e for e in ev if e["event"] == "client"][0]
            self.assertEqual((cev["primary"], cev["extra"], cev["default"]), ("enUS", ["ruRU"], "ruRU"))
            self.assertEqual(cev["sources"]["ruRU"], other)
            plan = [e for e in ev if e["event"] == "plan"][0]["steps"]
            self.assertEqual({(s["name"], s["locale"]) for s in plan}, {("text", "enUS"), ("text@ruRU", "ruRU")})
            self.assertFalse(os.path.exists(out))                          # a dry run writes nothing

            rc, ev = self.run_kiln(*base, "--add-locale", other)
            self.assertEqual([e["default"] for e in ev if e["event"] == "client"], ["enUS"])

            for bad in (["--default-locale", "frFR", "--add-locale", other], ["--add-locale", main]):
                rc, ev = self.run_kiln(*base, *bad)
                self.assertEqual(rc, 2)
                self.assertEqual([e["event"] for e in ev], ["error"])     # --json: nothing but JSON on stdout

    def test_maps_only_dry_run_on_a_fresh_output(self):
        # nothing extracted yet: the map steps cannot be listed, which is not an empty selection
        main = fake_client(self.root, "main", ["enUS"])
        with KnownExe((b"MZ synthetic", 1)):
            rc, ev = self.run_kiln("--game", main, "--out", os.path.join(self.root, "out"), "--steps", "continents",
                                   "--json", "--dry-run", "--bakers", os.path.join(self.root, "no-bakers"))
        self.assertEqual(rc, 0)
        plan = [e for e in ev if e["event"] == "plan"][0]
        self.assertEqual(plan["steps"], [])
        self.assertIn("map table", plan["maps_note"])

    def test_step_events_carry_locale(self):
        import json
        from types import SimpleNamespace
        from kilnlib.pipeline import Ctx, run_step
        main = fake_client(self.root, "main", ["enUS"])
        other = fake_client(self.root, "other", ["ruRU"])
        bakers = os.path.join(self.root, "bakers")
        os.makedirs(os.path.join(bakers, "Data", "expansions", "p1"))
        with open(os.path.join(bakers, "Data", "expansions", "p1", "expansion.json"), "w") as f:
            json.dump({"build": 1}, f)
        with KnownExe((b"MZ synthetic", 1)):
            cl = client.identify(main)
            client.merge_added(cl, [client.identify_added(other, cl)])
        a = SimpleNamespace(out=os.path.join(self.root, "out"), work=None, tools=None, bakers=bakers, python=None,
                            dll_dir=None, mirror=None, jobs=1, json=True, force=False)
        ctx = Ctx(a, cl, ROOT)
        os.makedirs(ctx.logs)
        man = Manifest(ctx.out, ctx.data)
        st = {s.name: s for s in S.static_steps(bakers)}["profile"]
        copy = S.locale_copy(st, "ruRU", ctx.locale_folders("ruRU"))
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(run_step(ctx, man, st, 1, 2), "ok")
            run_step(ctx, man, copy, 2, 2)
        ev = [json.loads(ln) for ln in buf.getvalue().splitlines()]
        steps = [(e["name"], e["state"], e["locale"]) for e in ev if e["event"] == "step"]
        self.assertIn(("profile", "start", "enUS"), steps)
        self.assertIn(("profile", "ok", "enUS"), steps)
        self.assertIn(("profile@ruRU", "start", "ruRU"), steps)


EXE = ".exe" if os.name == "nt" else ""


def small_exe():
    """A program that starts on its own and exits at once (the stand-in for a converter's usage call)."""
    if os.name == "nt":
        from kilnlib import winrt
        p = os.path.join(winrt.system_dirs()[0], "hostname.exe")
        if os.path.exists(p):
            return p
    return shutil.which("true") or sys.executable


@unittest.skipUnless(os.name == "nt", "Windows loader behaviour")
class DllTest(unittest.TestCase):
    """A user's case: a converter started without its DLLs must give an exit code and a clear message,
    never a dialog. python.exe copied away from python3x.dll behaves exactly like that."""

    def setUp(self):
        from kilnlib import winrt
        self.w = winrt
        winrt.set_error_mode()
        self.tmp = tempfile.TemporaryDirectory()
        self.tools = os.path.join(self.tmp.name, "tools")
        os.makedirs(self.tools)
        self.conv = os.path.join(self.tools, "psp_terrain_conv.exe")
        shutil.copy(sys.executable, self.conv)
        self.pydll = [n for n in self.w.pe_imports(sys.executable) if n.lower().startswith("python3")][0]
        self.sysenv = dict(os.environ, PATH=os.pathsep.join(self.w.system_dirs()))
        self.sysenv.pop("KILN_DLL_DIR", None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_imports_and_missing(self):
        self.assertIn("kernel32.dll", [n.lower() for n in self.w.pe_imports(sys.executable)])
        self.assertEqual(self.w.missing_dlls(self.conv, []), [self.pydll])
        self.assertEqual(self.w.missing_dlls(self.conv, [os.path.dirname(sys.executable)]), [])
        self.assertEqual(self.w.pe_imports(__file__), [])

    def test_preflight_names_the_dll(self):
        bad = self.w.preflight([self.conv, small_exe()], self.sysenv, [], timeout=60)
        self.assertEqual(len(bad), 1)
        exe, rc, missing = bad[0]
        self.assertEqual(exe, self.conv)
        self.assertTrue(rc.startswith("0xC0000135"), rc)
        self.assertEqual(missing, [self.pydll])

    def test_dll_dir_order(self):
        from unittest import mock
        self.assertEqual(self.w.resolve_dll_dir([self.conv], "given"), ("given", "--dll-dir"))
        with mock.patch.dict(os.environ, {"KILN_DLL_DIR": os.path.dirname(sys.executable)}):
            self.assertEqual(self.w.resolve_dll_dir([self.conv]), (os.path.dirname(sys.executable), "KILN_DLL_DIR"))
        shutil.copy(os.path.join(os.path.dirname(sys.executable), self.pydll), self.tools)     # the bundle layout
        self.assertEqual(self.w.resolve_dll_dir([self.conv]), ("", "the converters' own folder"))

    def test_run_stops_before_extracting(self):
        from unittest import mock
        g = fake_client(self.tmp.name, "main", ["enUS"])
        shutil.copy(small_exe(), os.path.join(self.tools, "psp_mpq_extract.exe"))
        out = os.path.join(self.tmp.name, "out")
        import contextlib
        import io
        import json
        import kiln
        buf = io.StringIO()
        with KnownExe((b"MZ synthetic", 1)), mock.patch.dict(os.environ, self.sysenv, clear=True),                 mock.patch.object(self.w, "MSYS2_CANDIDATES", []), contextlib.redirect_stdout(buf):
            rc = kiln.main(["--game", g, "--out", out, "--tools", self.tools, "--steps", "terrain_x", "--json"])
        ev = [json.loads(ln) for ln in buf.getvalue().splitlines()]
        self.assertEqual(rc, 2)
        err = [e for e in ev if e["event"] == "error"][0]
        self.assertEqual(err["missing_dlls"], [self.pydll])
        self.assertFalse(os.path.exists(os.path.join(out, "work", "extracted")))    # nothing extracted


def wpak(entries):
    """A WPAK file (include/platform/psp/psp_pak.hpp): header, entries 64-byte aligned, TOC."""
    body, toc, off = b"", b"", 16
    for name, data in entries:
        pad = (-off) % 64
        body += b"\0" * pad
        off += pad
        toc += struct.pack("<48sIIII", name.encode(), off, len(data), 3, 0)
        body += data
        off += len(data)
    return struct.pack("<4sIII", b"WPAK", 1, len(entries), 16 + len(body)) + body + toc


class PostCheckTest(unittest.TestCase):
    def test_glue_without_zone_names(self):
        from types import SimpleNamespace
        from kilnlib.pipeline import Ctx, post_check
        st = {s.name: s for s in S.static_steps(os.path.join(ROOT, "no-bakers-here"))}
        with tempfile.TemporaryDirectory() as out:
            os.makedirs(os.path.join(out, "data"))
            a = SimpleNamespace(out=out, work=None, tools=None, bakers=None, python=None, dll_dir=None, mirror=None,
                                jobs=1, json=False)
            ctx = Ctx(a, None, ROOT)
            p = os.path.join(out, "data", "glue.pak")
            with open(p, "wb") as f:
                f.write(wpak([("scenes", b"x" * 10), ("zones", struct.pack("<I", 0))]))
            self.assertIn("no zone names", post_check(ctx, st["glue"], ["data/glue.pak"]))
            names = b"North\0"
            with open(p, "wb") as f:
                f.write(wpak([("zones", struct.pack("<III", 1, 1, 12) + names)]))
            self.assertEqual(post_check(ctx, st["glue"], ["data/glue.pak"]), "")
            self.assertEqual(post_check(ctx, st["text"], ["data/text.pak"]), "")     # no check for that step

    def test_strings_step(self):
        st = S._py("strings", "strings.pak", **S.PY_OVERRIDES["strings"])
        self.assertEqual((st.locale, st.group, st.png), ("full", "tables", False))
        self.assertIn("interface\\gluexml\\", st.prefixes)
        copy = S.locale_copy(st, "ruRU", {"X": "x", "XG": "xg", "D": "d", "W": "w", "L": "l"})
        self.assertEqual(copy.outputs, ["data/locale/ruRU/strings.pak"])
        from types import SimpleNamespace
        from kilnlib.pipeline import Ctx
        a = SimpleNamespace(out="o", work=None, tools=None, bakers="b", python="py", dll_dir=None, mirror=None,
                            jobs=1, json=False)
        argv = Ctx(a, SimpleNamespace(locale="enUS", build=1), ROOT).expand(copy.run[0], copy)
        self.assertEqual(argv[-3:], ["--locale", "ruRU", "--verify"])


class ExtractRecordTest(unittest.TestCase):
    def test_record(self):
        from kilnlib.pipeline import ExtractRecord
        with tempfile.TemporaryDirectory() as r:
            data, tree, lists = (os.path.join(r, n) for n in ("Data", "tree", "lists"))
            os.makedirs(os.path.join(data, "enUS"))
            for p in ("common.MPQ", os.path.join("enUS", "locale-enUS.MPQ")):
                with open(os.path.join(data, p), "wb") as f:
                    f.write(b"x")
            os.makedirs(os.path.join(tree, "interface"))
            rec = ExtractRecord(lists, tree, data, "enUS")
            self.assertFalse(rec.covers("interface\\"))
            rec.add(["interface\\", "dbfilesclient\\"])
            again = ExtractRecord(lists, tree, data, "enUS")
            self.assertTrue(again.covers("interface\\"))
            self.assertTrue(again.covers("interface\\glues\\"))            # inside a recorded prefix
            self.assertFalse(again.covers("dbfilesclient\\"))              # its folder is gone
            self.assertFalse(again.covers("world\\"))
            with open(os.path.join(data, "enUS", "locale-enUS.MPQ"), "ab") as f:
                f.write(b"patched")                                         # another archive: extract again
            self.assertFalse(ExtractRecord(lists, tree, data, "enUS").covers("interface\\"))


class FailGateTest(unittest.TestCase):
    def test_gate(self):
        from kilnlib.pipeline import FailGate
        st = {s.name: s for s in S.static_steps(os.path.join(ROOT, "no-bakers-here"))}
        folders = {"X": "x", "XG": "xg", "D": "d", "W": "w", "L": "l"}
        text, text_ru = st["text"], S.locale_copy(st["text"], "ruRU", folders)
        g = FailGate()
        g.failed(text, 1)
        self.assertEqual(g.blocked(text_ru), "text failed")             # the same command on another locale
        self.assertEqual(g.blocked(st["dbc"]), "")                       # independent steps go on
        for name in ("gameobjects", "transports", "gameobjects_wmo"):   # one converter failing again and again
            g.failed(st[name], 1)
        self.assertIn("psp_model_conv failed in 3 steps", g.blocked(S.map_steps("maps", mapdb.MapInfo(
            id=0, dir="M", itype=0, loading=0, wdt=True, tiles=[(1, 1)]))[1]))
        g2 = FailGate()
        g2.failed(st["dbc"], 0xC0000135)                                 # cannot start: everything stops
        self.assertIn("queue stopped", g2.blocked(st["light"]))
        self.assertIn("0xC0000135", g2.blocked(st["light"]))


FAKE_BAKER = '''import argparse, os
ap = argparse.ArgumentParser()
ap.add_argument("--extracted")
ap.add_argument("--out")
a = ap.parse_args()
os.makedirs(os.path.dirname(a.out), exist_ok=True)
with open(a.out, "w") as f:
    f.write("baked from " + os.path.basename(os.path.dirname(os.path.abspath(a.extracted))))
'''


class LaterLocaleTest(unittest.TestCase):
    """A language added to an earlier output without the main client (--out + --add-locale, no --game), and
    removed again (--remove-locale)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.tmp.name
        self.main = fake_client(r, "main", ["enUS"])
        self.ru = fake_client(r, "russian", ["ruRU"])
        self.bakers = os.path.join(r, "bakers")
        os.makedirs(os.path.join(self.bakers, "tools", "psp"))
        with open(os.path.join(self.bakers, "tools", "psp", "build_x_pak.py"), "w") as f:
            f.write(FAKE_BAKER)
        self.tools = os.path.join(r, "tools")
        os.makedirs(self.tools)
        shutil.copy(small_exe(), os.path.join(self.tools, "psp_mpq_extract" + EXE))
        self.out = os.path.join(r, "out")
        self.card = os.path.join(r, "card", "GAMEDIR")
        self.known = KnownExe((b"MZ synthetic", 1))
        self.known.__enter__()

    def tearDown(self):
        self.known.__exit__()
        self.tmp.cleanup()

    def kiln(self, *args):
        import contextlib
        import io
        import json
        import kiln
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kiln.main(["--out", self.out, "--bakers", self.bakers, "--tools", self.tools, "--json"] + list(args))
        return rc, [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]

    def read(self, *rel):
        with open(os.path.join(*rel), encoding="utf-8") as f:
            return f.read()

    def test_add_later_and_remove(self):
        rc, ev = self.kiln("--game", self.main, "--steps", "x")
        self.assertEqual(rc, 0)
        self.assertEqual(L.read(os.path.join(self.out, "data")), {"primary": "enUS", "extra": [], "default": "enUS"})
        os.rename(self.main, self.main + "_gone")                     # the main client is not needed any more

        rc, ev = self.kiln("--add-locale", self.ru, "--target", self.card)
        self.assertEqual(rc, 0, ev[-3:])
        made = [(e["name"], e["locale"]) for e in ev if e["event"] == "step" and e["state"] == "ok"]
        self.assertEqual(made, [("x@ruRU", "ruRU")])                   # only the new locale's localized step
        self.assertEqual(self.read(self.out, "data", "locale", "ruRU", "x.pak"), "baked from ruRU")
        self.assertEqual(L.read(os.path.join(self.out, "data")),
                         {"primary": "enUS", "extra": ["ruRU"], "default": "enUS"})   # adding keeps the default
        self.assertTrue(os.path.exists(os.path.join(self.card, "data", "locale", "ruRU", "x.pak")))
        self.assertTrue(os.path.exists(os.path.join(self.card, "data", "locales.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.card, "data", "x.pak")))     # only what is new
        man = self.read(self.out, "data", "kiln.manifest.json")
        self.assertIn('"x@ruRU"', man)

        rc, ev = self.kiln("--add-locale", self.ru, "--default-locale", "ruRU")   # again: nothing to re-make
        self.assertEqual(rc, 0)
        self.assertEqual([e for e in ev if e["event"] == "step" and e["state"] == "ok"], [])
        self.assertEqual(L.read(os.path.join(self.out, "data"))["default"], "ruRU")

        rc, ev = self.kiln("--remove-locale", "enUS")
        self.assertEqual((rc, ev[-1]["event"]), (2, "error"))
        self.assertIn("primary", ev[-1]["message"])
        rc, ev = self.kiln("--remove-locale", "ruRU", "--target", self.card)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, "data", "locale", "ruRU")))
        self.assertEqual(L.read(os.path.join(self.out, "data")), {"primary": "enUS", "extra": [], "default": "enUS"})
        self.assertFalse(os.path.exists(os.path.join(self.card, "data", "locale", "ruRU")))
        self.assertEqual(L.read(os.path.join(self.card, "data")), {"primary": "enUS", "extra": [], "default": "enUS"})
        self.assertNotIn('"x@ruRU"', self.read(self.out, "data", "kiln.manifest.json"))

    def test_without_an_output(self):
        rc, ev = self.kiln("--add-locale", self.ru)
        self.assertEqual(rc, 2)
        self.assertIn("convert with --game first", ev[-1]["message"])


class CheckUpdateTest(unittest.TestCase):
    """--check / --update / --repair / --target end to end on a synthetic client with the built-in profile step
    (no converter runs; the extractor only has to exist)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.tmp.name
        self.game = fake_client(r, "main", ["enUS"])
        self.bakers = os.path.join(r, "bakers")
        self.prof = os.path.join(self.bakers, "Data", "expansions", "p1")
        os.makedirs(self.prof)
        self.write(os.path.join(self.prof, "expansion.json"), '{"build": 1}')
        self.tools = os.path.join(r, "tools")
        os.makedirs(self.tools)
        shutil.copy(small_exe(), os.path.join(self.tools, "psp_mpq_extract" + EXE))   # starts; never used here
        self.out = os.path.join(r, "out")
        self.card = os.path.join(r, "card", "GAMEDIR")
        self.known = KnownExe((b"MZ synthetic", 1))
        self.known.__enter__()

    def tearDown(self):
        self.known.__exit__()
        self.tmp.cleanup()

    @staticmethod
    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def kiln(self, *args):
        import contextlib
        import io
        import json
        import kiln
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kiln.main(["--game", self.game, "--out", self.out, "--bakers", self.bakers, "--tools", self.tools,
                            "--json"] + list(args))
        ev = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
        return rc, ev

    def kinds(self, ev):
        return sorted((e["where"], e["problem"]) for e in ev if e["event"] == "check")

    def manifest(self, doc=None):
        import json
        path = os.path.join(self.out, "data", "kiln.manifest.json")
        if doc is None:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def test_modes(self):
        rc, ev = self.kiln("--check")
        self.assertEqual((rc, ev[-1]["event"]), (2, "error"))              # nothing made yet
        rc, ev = self.kiln("--steps", "profile")
        self.assertEqual(rc, 0)
        out_json = os.path.join(self.out, "data", "expansions", "p1", "expansion.json")
        man = self.manifest()
        ident = man["steps"]["profile"]["identity"]
        self.assertEqual((ident["input"]["locale"], ident["kiln"], man["hash"]), ("enUS", "0.1.0", "sha256"))
        self.assertIn("profiles", ident)
        rc, ev = self.kiln("--check")
        self.assertEqual((rc, self.kinds(ev)), (0, []))
        self.assertTrue(ev[-1]["ok"])

        # damaged output + a file nobody made: hash problem (exit 1), extra = warning only
        self.write(out_json, '{"build": 2}')
        self.write(os.path.join(self.out, "data", "stray.pak"), "x")
        rc, ev = self.kiln("--check")
        self.assertEqual((rc, self.kinds(ev)), (1, [("out", "extra"), ("out", "hash")]))
        rc, ev = self.kiln("--repair")
        self.assertEqual(rc, 0)
        self.assertEqual([e["name"] for e in ev if e["event"] == "step" and e["state"] == "ok"], ["profile"])
        rc, ev = self.kiln("--check")
        self.assertEqual((rc, self.kinds(ev)), (0, [("out", "extra")]))
        os.remove(os.path.join(self.out, "data", "stray.pak"))

        # a manifest of an older Kiln (no identity): unknown -> --update re-makes the step
        man = self.manifest()
        del man["steps"]["profile"]["identity"]
        self.manifest(man)
        rc, ev = self.kiln("--check")
        self.assertEqual((rc, self.kinds(ev)), (1, [("out", "unknown")]))
        rc, ev = self.kiln("--update")
        self.assertEqual([e["reason"] for e in ev if e["event"] == "update"],
                         ["identity unknown (recorded by an older Kiln)"])
        self.assertEqual(self.kiln("--check")[0], 0)
        rc, ev = self.kiln("--update")                                    # nothing changed: nothing re-made
        self.assertEqual((rc, [e for e in ev if e["event"] in ("update", "step")]), (0, []))

        # the baker's files changed: outdated -> --update
        self.write(os.path.join(self.prof, "opcodes.json"), "{}")
        rc, ev = self.kiln("--check")
        self.assertEqual((rc, self.kinds(ev)), (1, [("out", "outdated")]))
        rc, ev = self.kiln("--update")
        self.assertTrue([e for e in ev if e["event"] == "update" and e["reason"].startswith("changed: profiles")])
        self.assertTrue(os.path.exists(os.path.join(self.out, "data", "expansions", "p1", "opcodes.json")))

        # the copy on the memory stick
        rc, ev = self.kiln("--check", "--target", self.card)
        self.assertEqual(rc, 1)
        self.assertEqual({k for w, k in self.kinds(ev) if w == "target"}, {"missing"})
        rc, ev = self.kiln("--update", "--target", self.card)
        self.assertEqual((rc, ev[-1]["copy"]["copied"]), (0, 3))           # 2 profile files + locales.txt
        self.assertTrue(os.path.exists(os.path.join(self.card, "data", "locales.txt")))
        rc, ev = self.kiln("--update", "--target", os.path.join(self.card, "data"))   # data/ given: same folder
        self.assertEqual((rc, ev[-1]["copy"]["copied"], ev[-1]["copy"]["kept"]), (0, 0, 3))
        self.assertEqual(self.kiln("--check", "--target", self.card)[0], 0)
        self.write(os.path.join(self.card, "data", "expansions", "p1", "opcodes.json"), "{]")     # same size, other bytes
        self.write(os.path.join(self.card, "data", "movies", "intro.pmf"), "x")                  # the player's own: fine
        rc, ev = self.kiln("--check", "--target", self.card)
        self.assertEqual((rc, self.kinds(ev)), (1, [("target", "hash")]))
        rc, ev = self.kiln("--repair", "--target", self.card)
        self.assertEqual((rc, ev[-1]["copy"]["copied"]), (0, 1))
        self.assertEqual(self.kiln("--check", "--target", self.card)[0], 0)

    def test_one_mode_only(self):
        import contextlib
        import io
        import kiln
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            kiln.parse(["--game", "g", "--out", "o", "--check", "--update"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            kiln.parse(["--game", "g", "--out", "o", "--deep"])


if __name__ == "__main__":
    unittest.main()
