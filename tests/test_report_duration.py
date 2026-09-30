import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'app')]
from benchmark_gui import rendered_total_seconds

class RenderedDurationTests(unittest.TestCase):
    def test_official_units_and_minute_format(self):
        for text,expected in [('2m 35.491s',155.491),('9.224 s',9.224),('777.000 ms',.777),('16 us',.000016)]:
            with self.subTest(text=text):
                self.assertAlmostEqual(rendered_total_seconds('MEASURED PROCESSING TOTAL: '+text),expected)

if __name__=='__main__': unittest.main()
