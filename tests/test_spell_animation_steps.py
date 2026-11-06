import unittest
from pathlib import Path
from kilnlib.steps import static_steps

class SpellStepTest(unittest.TestCase):
    def test_spell_probe_and_bake_include_nonclass_spells(self):
        step=next(s for s in static_steps(str(Path(__file__).resolve().parents[1]/'bakers')) if s.name=='spellfx')
        self.assertIn('--all',step.run[0])
        self.assertIn('--all',step.probes[0].cmd)
