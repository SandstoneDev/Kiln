import unittest,tempfile
from pathlib import Path
from kilnlib import steps
from kilnlib.size_report import report
class CompactTests(unittest.TestCase):
    def test_content_preserved_and_commands_changed(self):
        plan=steps.static_steps("bakers")
        names=[s.name for s in plan]
        outputs=[list(s.outputs) for s in plan]
        steps.apply_compact(plan)
        self.assertEqual(names,[s.name for s in plan])
        self.assertEqual(outputs,[list(s.outputs) for s in plan])
        char=next(s for s in plan if s.name=="characters")
        self.assertIn("--tex-max",char.run[0])
        self.assertIn("20",char.run[0])
    def test_size_report_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)/"data";d.mkdir()
            (d/"characters.pak").write_bytes(b"abc")
            (d/"music").mkdir();(d/"music"/"a.mp3").write_bytes(b"12345")
            result=report(tmp)
            self.assertEqual(8,result["bytes"])
            self.assertEqual(5,result["groups"]["music"])
            self.assertEqual(b"abc",(d/"characters.pak").read_bytes())
