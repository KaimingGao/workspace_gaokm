"""文档内容守卫（合并原 P72/P73/P81/P82/P85/P92 文档断言）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestDocsGuards(unittest.TestCase):
    def _read(self, *parts):
        path = os.path.join(ROOT, *parts)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_quant_md_score_and_export(self):
        text = self._read("docs", "quant.md")
        self.assertIn("#### score 的用途", text)
        self.assertIn("rank_candidates", text)
        self.assertIn("compute_buy_stance", text)
        self.assertIn("simulate_buys", text)
        self.assertIn("导出预览", text)
        self.assertIn("offline", text)

    def test_quant_md_model_policy(self):
        text = self._read("docs", "quant.md")
        self.assertIn("#### 为何不用拟合模型", text)
        self.assertIn("固定权重线性加总", text)
        self.assertIn("何时值得引入拟合模型", text)
        self.assertIn("score_ml", text)

    def test_quant_md_industrial_factors(self):
        text = self._read("docs", "quant.md")
        self.assertIn("工业常见因子分类", text)
        self.assertIn("Barra", text)
        self.assertIn("候选因子数百", text)
        self.assertIn("factor_ols_run.py", text)

    def test_quant_upgrade_links_model_policy(self):
        text = self._read("docs", "quant-upgrade.md")
        self.assertIn("为何不用拟合模型", text)
        self.assertIn("quant.md#为何不用拟合模型", text)
        self.assertIn("factor-ols", text)

    def test_evals_readme_interpret_neutral(self):
        text = self._read("evals", "README.md")
        self.assertIn("quant_interpret_neutral", text)
        self.assertIn("agent_regression_quant", text)


if __name__ == "__main__":
    unittest.main()
