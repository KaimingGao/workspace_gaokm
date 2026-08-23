"""分组报告 name_by_code：拒伪名 + 观察池补齐。"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestClusterNameEnrich(unittest.TestCase):
    def test_usable_stock_name_rejects_code_as_name(self):
        from quant.services.quant_service_factors import _usable_stock_name

        self.assertEqual(_usable_stock_name("300750", "宁德时代"), "宁德时代")
        self.assertEqual(_usable_stock_name("300750", "300750"), "")
        self.assertEqual(_usable_stock_name("300750", ""), "")
        self.assertEqual(_usable_stock_name("600519.SH", "600519"), "")

    def test_enrich_overwrites_ranking_pseudo_names(self):
        from quant.services.quant_service_factors import _enrich_cluster_name_by_code

        report = {
            "name_by_code": {},
            "clusters": [
                {
                    "group_ranking": [
                        {"stock_code": "300750", "stock_name": "300750"},
                        {"stock_code": "600519", "stock_name": "600519"},
                    ]
                }
            ],
        }
        watching = {
            "watchlist": ["300750", "600519"],
            "watchlist_names": ["宁德时代", "贵州茅台"],
        }
        with patch(
            "core.watching.store.read_watching", return_value=watching
        ), patch(
            "core.watching.store.watchlist_names_for",
            return_value=["宁德时代", "贵州茅台"],
        ):
            out = _enrich_cluster_name_by_code(report)
        self.assertEqual(out["name_by_code"]["300750"], "宁德时代")
        self.assertEqual(out["name_by_code"]["600519"], "贵州茅台")
        rows = out["clusters"][0]["group_ranking"]
        self.assertEqual(rows[0]["stock_name"], "宁德时代")
        self.assertEqual(rows[1]["stock_name"], "贵州茅台")


if __name__ == "__main__":
    unittest.main()
