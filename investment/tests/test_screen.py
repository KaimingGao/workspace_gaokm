import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.screen.engine import StockScreener, filter_stocks


def _sample_rows():
    return [
        {"代码": "600036", "名称": "招商银行", "最新价": 35.0, "涨跌幅": 1.2, "市盈率-动态": 6.5, "市净率": 0.8},
        {"代码": "601398", "名称": "工商银行", "最新价": 5.5, "涨跌幅": 0.5, "市盈率-动态": 5.0, "市净率": 0.6},
        {"代码": "600519", "名称": "贵州茅台", "最新价": 1600.0, "涨跌幅": -1.0, "市盈率-动态": 25.0, "市净率": 8.0},
        {"代码": "000001", "名称": "平安银行", "最新价": 11.0, "涨跌幅": 2.0, "市盈率-动态": 18.0, "市净率": 0.9},
        {"代码": "300750", "名称": "宁德时代", "最新价": 180.0, "涨跌幅": 3.0, "市盈率-动态": 22.0, "市净率": 4.0},
        {"代码": "000002", "名称": "ST示例", "最新价": 2.0, "涨跌幅": 5.0, "市盈率-动态": 3.0, "市净率": 0.5},
    ]


class TestScreenFilter(unittest.TestCase):
    def test_bank_pe_filter(self):
        items = filter_stocks(_sample_rows(), sector="银行", pe_max=15, limit=10)
        codes = {i["stock_code"] for i in items}
        self.assertIn("600036", codes)
        self.assertIn("601398", codes)
        self.assertNotIn("000001", codes)  # PE 18 > 15
        self.assertNotIn("600519", codes)

    def test_exclude_st(self):
        items = filter_stocks(_sample_rows(), change_min=0, limit=20)
        names = [i["stock_name"] for i in items]
        self.assertTrue(all("ST" not in n for n in names))

    def test_empty_when_too_strict(self):
        items = filter_stocks(_sample_rows(), sector="银行", pe_max=1, limit=10)
        self.assertEqual(items, [])

    def test_screener_requires_filter(self):
        screener = StockScreener()
        result = screener.screen({})
        self.assertFalse(result["success"])

    def test_screener_with_fixture(self):
        screener = StockScreener()
        with patch("skills.screen.engine.fetch_a_spot", return_value=_sample_rows()):
            result = screener.screen({"sector": "银行", "pe_max": 15, "limit": 5})
        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["count"], 1)
        self.assertIn("不保证收益", result.get("note", ""))

    def test_fetch_a_spot_uses_memory_cache(self):
        from skills.screen import engine as screen_engine

        screen_engine.clear_spot_cache()
        rows = _sample_rows()
        with patch(
            "skills.screen.engine._fetch_a_spot_live",
            side_effect=[rows, RuntimeError("should not call")],
        ) as mock_live:
            first = screen_engine.fetch_a_spot()
            second = screen_engine.fetch_a_spot()
        self.assertEqual(len(first), len(rows))
        self.assertEqual(len(second), len(rows))
        self.assertEqual(mock_live.call_count, 1)
        screen_engine.clear_spot_cache()

    def test_fetch_a_spot_disk_fallback(self):
        import tempfile
        from skills.screen import engine as screen_engine

        screen_engine.clear_spot_cache()
        rows = _sample_rows()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spot_a_em.json")
            with patch("skills.screen.engine._spot_disk_path", return_value=path), patch(
                "skills.screen.engine._fetch_a_spot_live",
                side_effect=RuntimeError("Connection aborted"),
            ):
                # 无磁盘时失败
                with self.assertRaises(RuntimeError):
                    screen_engine.fetch_a_spot(force=True)
                # 写入磁盘后再回退
                screen_engine._save_disk_spot(rows)
                screen_engine.clear_spot_cache()
                got = screen_engine.fetch_a_spot()
                self.assertEqual(len(got), len(rows))
                self.assertEqual(screen_engine.fetch_a_spot.last_source, "disk_cache")
        screen_engine.clear_spot_cache()


if __name__ == "__main__":
    unittest.main()
