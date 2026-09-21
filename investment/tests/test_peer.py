import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.peer.engine import detect_group, build_peer_compare
from skills.peer.handler import PeerHandler


class TestPeer(unittest.TestCase):
    def test_detect_baijiu(self):
        group, members = detect_group("茅台")
        self.assertEqual(group, "白酒")
        self.assertIn("五粮液", members)

    def test_detect_internet_hk(self):
        group, _ = detect_group("快手")
        self.assertEqual(group, "互联网港股")

    def test_build_mocked(self):
        def fake_query(code):
            data = {
                "茅台": {"success": True, "stock_code": "600519", "stock_name": "贵州茅台", "price": "1500", "price_raw": 1500, "change": "+1%", "change_raw": 1.0, "market": "CN"},
                "贵州茅台": {"success": True, "stock_code": "600519", "stock_name": "贵州茅台", "price": "1500", "price_raw": 1500, "change": "+1%", "change_raw": 1.0, "market": "CN"},
                "五粮液": {"success": True, "stock_code": "000858", "stock_name": "五粮液", "price": "140", "price_raw": 140, "change": "-0.5%", "change_raw": -0.5, "market": "CN"},
                "泸州老窖": {"success": True, "stock_code": "000568", "stock_name": "泸州老窖", "price": "120", "price_raw": 120, "change": "+0.2%", "change_raw": 0.2, "market": "CN"},
                "山西汾酒": {"success": True, "stock_code": "600809", "stock_name": "山西汾酒", "price": "180", "price_raw": 180, "change": "+0.8%", "change_raw": 0.8, "market": "CN"},
            }
            return data.get(code, {"success": False, "error": "x"})

        with patch("skills.peer.engine.query_quote", side_effect=fake_query), patch(
            "skills.peer.engine.resolve_symbol",
            side_effect=lambda c: {
                "茅台": "sh600519",
                "贵州茅台": "sh600519",
                "五粮液": "sz000858",
                "泸州老窖": "sz000568",
                "山西汾酒": "sh600809",
            }.get(c),
        ), patch(
            "skills.fundamentals.engine.fetch_cn_spot_row",
            side_effect=Exception("skip network"),
        ):
            result = build_peer_compare("茅台")

        self.assertTrue(result["success"])
        self.assertEqual(result["group"], "白酒")
        self.assertGreaterEqual(len(result["peers"]), 2)

    def test_handler_missing(self):
        out = PeerHandler().execute({"parameters": {}})
        self.assertIn("stock_code", out)


if __name__ == "__main__":
    unittest.main()
