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
    def test_glue_music_is_flat_on_disk_and_client_relative_in_pak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dbc = root / "dbfilesclient/areatable.dbc"
            dbc.parent.mkdir()
            dbc.write_bytes(struct.pack("<4sIIIIIIIB", b"WDBC", 1, 3, 12, 1, 0, 0, 0, 0))
            source = root / "sound/music/gluescreenmusic/wotlk_main_title.mp3"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"synthetic fixture; no client audio")
            pak = root / "audio.pak"
            music = root / "music"
            subprocess.run([os.environ["KILN_MUSIC_CONVERTER"], "--extracted", str(root),
                            "--out", str(pak), "--music-out", str(music), "--no-original"],
                           check=True, capture_output=True)
            output = music / "gluescreenmusic/wotlk_main_title.mp3"
            self.assertEqual(output.read_bytes(), source.read_bytes())
            self.assertFalse((music / "sound/music").exists())
            self.assertIn(b"sound/music/gluescreenmusic/wotlk_main_title.mp3", pak.read_bytes())
