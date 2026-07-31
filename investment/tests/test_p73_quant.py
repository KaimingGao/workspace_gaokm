import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP73ScoreUsageDocs(unittest.TestCase):
    def test_quant_md_score_section(self):
        path = os.path.join(ROOT, "docs", "quant.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("#### score 的用途", text)
        self.assertIn("rank_candidates", text)
        self.assertIn("compute_buy_stance", text)
        self.assertIn("simulate_buys", text)


if __name__ == "__main__":
    unittest.main()
