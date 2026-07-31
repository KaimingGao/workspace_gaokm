import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP82UpgradePrincipleLink(unittest.TestCase):
    def test_quant_upgrade_links_model_policy(self):
        path = os.path.join(ROOT, "docs", "quant-upgrade.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("P81", text)
        self.assertIn("为何不用拟合模型", text)
        self.assertIn("quant.md#为何不用拟合模型", text)

    def test_quant_summary_lists_p81(self):
        path = os.path.join(ROOT, "docs", "quant-summary.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("P81", text)
        self.assertIn("P84", text)


if __name__ == "__main__":
    unittest.main()
