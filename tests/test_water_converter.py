"""End-to-end converter test using synthetic maps. Set KILN_SOUND_CONVERTER to the built executable."""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('KILN_SOUND_CONVERTER'), 'set KILN_SOUND_CONVERTER to run converter integration')
class WaterConverterTest(unittest.TestCase):
    def test_all_maps_and_vertex_heights(self):
        converter = str(Path(os.environ['KILN_SOUND_CONVERTER']).resolve())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'dbfilesclient').mkdir()
            strings = b'\0TestA\0TestB\0'
            dbc = struct.pack('<4s4I', b'WDBC', 2, 2, 8, len(strings))
            dbc += struct.pack('<4I', 0, 1, 1, 7) + strings
            (root / 'dbfilesclient/map.dbc').write_bytes(dbc)
            mh2o = bytearray(3100 + 16)
            struct.pack_into('<3I', mh2o, 17 * 12, 3072, 1, 0)
            struct.pack_into('<HHff4BII', mh2o, 3072, 7, 0, -5., -2., 3, 2, 1, 1, 3096, 3100)
            mh2o[3096] = 1
            struct.pack_into('<4f', mh2o, 3100, -5., -3., -4., -2.)
            adt = struct.pack('<II', 0x4D48324F, len(mh2o)) + mh2o
            for name in ('testa', 'testb'):
                directory = root / 'world/maps' / name
                directory.mkdir(parents=True)
                (directory / (name + '_32_32.adt')).write_bytes(adt)
            out = root / 'sounds.pak'
            result = subprocess.run([converter, '--extracted', str(root), '--out', str(out),
                                     '--all-maps', '--no-entries', '--verify'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            blob = out.read_bytes()
            magic, version, count, toc = struct.unpack_from('<4s3I', blob)
            self.assertEqual(magic, b'WPAK')
            grids = {}
            for i in range(count):
                name, offset, size, kind, reserved = struct.unpack_from('<48s4I', blob, toc + i * 64)
                name = name.split(b'\0', 1)[0].decode()
                if name.startswith('g_'):
                    grids[name] = blob[offset:offset + size]
            self.assertEqual(set(grids), {'g_0_32_32', 'g_1_32_32'})
            for grid in grids.values():
                self.assertEqual(struct.unpack_from('<4sHH', grid), (b'WSRF', 2, 0))
                self.assertEqual(struct.unpack_from('<4sHH', grid, 9224), (b'WWTR', 1, 1))
                chunk, liquid, low, high = struct.unpack_from('<HHII', grid, 9232)
                self.assertEqual((chunk, liquid, low, high), (17, 7, 1 << 19, 0))
                heights = struct.unpack_from('<81f', grid, 9244)
                self.assertEqual([heights[i] for i in (21, 22, 30, 31)], [-5., -3., -4., -2.])
