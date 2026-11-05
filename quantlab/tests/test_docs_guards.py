"""文档内容守卫（合并原 P72/P73/P81/P82/P85/P92 文档断言）。"""

import os
import unittest

from tests.conftest import ROOT


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
        self.assertIn("#### 选股真源：线性回归因子系数", text)
        self.assertIn("ReturnScoreModel", text)
        self.assertIn("predicted_score", text)
        self.assertIn("线性回归", text)
        self.assertIn("factor_ols", text)

    def test_quant_md_industrial_factors(self):
        text = self._read("docs", "quant.md")
        self.assertIn("工业常见因子分类", text)
        self.assertIn("Barra", text)
        self.assertIn("候选因子数百", text)

    def test_quant_md_yhat_and_live_hub(self):
        text = self._read("docs", "quant.md")
        self.assertIn("## predicted_score（ŷ）全链路", text)
        self.assertIn("## Live 出分与旁路", text)
        self.assertIn('id="量化运维"', text)
        self.assertIn("quant-ops.md", text)

    def test_quant_ops_md(self):
        text = self._read("docs", "quant-ops.md")
        self.assertIn("# 量化运维", text)
        self.assertIn("preset", text)
        self.assertIn("cron", text)
        self.assertIn("daily_run.py", text)

    def test_evals_readme_interpret_neutral(self):
        text = self._read("evals", "README.md")
        self.assertIn("golden cases", text)
        self.assertIn("架构总览", text)
        self.assertNotIn("quant_interpret_neutral", text)


if __name__ == "__main__":
    unittest.main()
