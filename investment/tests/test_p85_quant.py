import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP85IndustrialFactorCatalog(unittest.TestCase):
    def test_quant_md_industrial_factor_section(self):
        path = os.path.join(ROOT, "docs", "quant.md")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("工业常见因子分类", text)
        self.assertIn("Barra", text)
        self.assertIn("候选因子数百", text)
        self.assertIn("factor_ols_run.py", text)


if __name__ == "__main__":
    unittest.main()
