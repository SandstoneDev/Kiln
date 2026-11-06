"""Synthetic checks for the audio step and the PSP music layout."""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from kilnlib.steps import static_steps


class MusicConverterTest(unittest.TestCase):
    def test_audio_step_only_requires_the_converter(self):
        step = next(step for step in static_steps("bakers") if step.name == "audio")
        self.assertEqual(set(step.programs()), {"@exe:psp_audio_conv"})

    @unittest.skipUnless(os.environ.get("KILN_MUSIC_CONVERTER"), "set KILN_MUSIC_CONVERTER for integration")
    def test_one_music_copy_supports_stock_and_current_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dbc = root / "dbfilesclient/areatable.dbc"
            dbc.parent.mkdir()
            dbc.write_bytes(struct.pack("<4sIIIIIIIB", b"WDBC", 1, 3, 12, 1, 0, 0, 0, 0))
            track = "sound/music/gluescreenmusic/wotlk_main_title.mp3"
            source = root / track
            source.parent.mkdir(parents=True)
            source.write_bytes(b"synthetic fixture; no client audio")
            pak = root / "audio.pak"
            music = root / "data/music"
            subprocess.run([os.environ["KILN_MUSIC_CONVERTER"], "--extracted", str(root),
                            "--out", str(pak), "--music-out", str(music), "--no-original"],
                           check=True, capture_output=True)
            output = music / track
            self.assertTrue(output.is_file(), "stock v0.1.0 must find the client-relative track")
            self.assertEqual(output.read_bytes(), source.read_bytes())
            self.assertEqual(list(music.rglob("*.mp3")), [output])
            self.assertFalse((music / "gluescreenmusic" / Path(track).name).exists())
            # Decode the real WPAK/AudioZones path table, rather than finding an
            # incidental string somewhere in the generated file.
            data = pak.read_bytes()
            magic, version, count, toc = struct.unpack_from("<4sIII", data)
            self.assertEqual((magic, version), (b"WPAK", 1))
            zones = None
            for index in range(count):
                entry = toc + index * 64
                name, offset, size = struct.unpack_from("<48sII", data, entry)
                if name.rstrip(b"\0") == b"zones":
                    zones = data[offset:offset + size]
            self.assertIsNotNone(zones)
            header = struct.unpack_from("<4s12I", zones)
            self.assertEqual((header[0], header[1], header[2]), (b"WZMU", 1, 1))
            name_offset = struct.unpack_from("<I", zones, header[7])[0]
            stored_track = zones[header[8] + name_offset:].split(b"\0", 1)[0].decode("ascii")
            self.assertEqual(stored_track, track)
            # Stock v0.1.0 concatenates this client-relative ID directly.
            self.assertTrue((root / "data/music" / stored_track).samefile(output))
            # Optional host probe calls the current production musicFilePath
            # against this same clean output; no PSP kernel or real audio used.
            lookup = os.environ.get("KILN_MUSIC_LOOKUP")
            if lookup:
                resolved = subprocess.check_output([lookup, str(root), stored_track], text=True).strip()
                self.assertTrue(Path(resolved).samefile(output))
