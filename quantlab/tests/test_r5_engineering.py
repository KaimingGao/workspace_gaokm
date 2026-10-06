"""R5 · 工程稳态契约：虚拟表文件 · core evals 黄金路径。"""

from __future__ import annotations

import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestVirtualTableContract(unittest.TestCase):
    def test_static_files_exist(self):
        for rel in (
            "web/static/js/virtual_table.js",
            "web/static/js/watching_table_island.js",
            "web/static/js/holdings_table_island.js",
            "web/static/js/paper/holdings_island.js",
        ):
            path = os.path.join(ROOT, rel)
            self.assertTrue(os.path.isfile(path), rel)

    def test_budget_constant_documented(self):
        path = os.path.join(ROOT, "web/static/js/virtual_table.js")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("VIRTUAL_TABLE_ROW_BUDGET", text)
        self.assertIn("500", text)
        self.assertIn("点击排序", text)
        self.assertIn("col.title", text)
        self.assertIn('options.fit === "host"', text)
        self.assertIn("minmax(0, 1fr)", text)

    def test_residual_pairs_trade_with_day_change(self):
        path = os.path.join(ROOT, "web/static/js/quant/watching_quotes_ui.js")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("trade − 涨跌", text)
        self.assertIn("ŷ_trade", text)


class TestCoreGoldenPaths(unittest.TestCase):
    def test_run_all(self):
        from evals.core_golden_paths import run_all_core_paths

        out = run_all_core_paths()
        self.assertTrue(out["ok"], out.get("failures"))
        self.assertEqual(out["count"], 3)


if __name__ == "__main__":
    unittest.main()
