import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP81ModelPolicyDocs(unittest.TestCase):
    def test_quant_md_model_policy_section(self):
        path = os.path.join(ROOT, "docs", "quant.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("#### 为何不用拟合模型", text)
        self.assertIn("固定权重线性加总", text)
        self.assertIn("何时值得引入拟合模型", text)
        self.assertIn("score_ml", text)


if __name__ == "__main__":
    unittest.main()
