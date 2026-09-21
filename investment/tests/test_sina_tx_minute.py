"""新浪/腾讯分钟备路。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _resp_json(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


class TestSinaTxMinute(unittest.TestCase):
    def test_to_sina_tx_symbol(self):
        from adapters.market.sina_tx_minute import to_sina_tx_symbol

        self.assertEqual(to_sina_tx_symbol("600519"), "sh600519")
        self.assertEqual(to_sina_tx_symbol("000001"), "sz000001")
        self.assertEqual(to_sina_tx_symbol("300418"), "sz300418")
        self.assertEqual(to_sina_tx_symbol("688111"), "sh688111")
        self.assertEqual(to_sina_tx_symbol("430047"), "bj430047")

    def test_sina_success(self):
        from adapters.market.sina_tx_minute import fetch_sina_tx_minute_bars

        payload = [
            {
                "day": "2026-08-28 09:35:00",
                "open": "10",
                "high": "11",
                "low": "9",
                "close": "10.5",
                "volume": "100",
            }
        ]
        with patch(
            "adapters.market.sina_tx_minute.requests_get_with_retry",
            return_value=_resp_json(payload),
        ):
            bars, meta = fetch_sina_tx_minute_bars("600519", period="5", lookback_days=5)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["close"], 10.5)
        self.assertEqual(meta.get("data_source"), "sina_tx:sina")

    def test_sina_fail_tencent_backup(self):
        from adapters.market.sina_tx_minute import fetch_sina_tx_minute_bars

        tx = {
            "data": {
                "sh600519": {
                    "m5": [["2026-08-28 09:35:00", "10", "10.5", "11", "9", "100"]]
                }
            }
        }

        def _get(url, **kw):
            if "sina.com.cn" in str(url):
                raise RuntimeError("sina down")
            return _resp_json(tx)

        with patch("adapters.market.sina_tx_minute.requests_get_with_retry", side_effect=_get):
            bars, meta = fetch_sina_tx_minute_bars("600519", period="5")
        self.assertEqual(len(bars), 1)
        self.assertEqual(meta.get("data_source"), "sina_tx:tencent")
        self.assertEqual(meta.get("backfill_reason"), "sina_fail")

    def test_fetch_uses_sina_tx_when_em_and_bs_empty(self):
        from adapters.market import minute_history as mh

        bar = {
            "datetime": "2026-08-28 09:35:00",
            "date": "2026-08-28",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 100,
        }
        with patch.object(mh, "resolve_market_code", return_value=("CN", "600519")), patch.object(
            mh, "load_minute_cache", return_value=None
        ), patch.object(
            mh, "_fetch_em_minute_bars", return_value=([], {}, "em down")
        ), patch.object(
            mh,
            "_maybe_fetch_sina_tx_minute_bars",
            return_value=([bar], {"data_source": "sina_tx:sina"}),
        ), patch.object(
            mh, "_maybe_fetch_baostock_minute_bars"
        ) as bs_fetch, patch.object(mh, "_throttle_minute_remote_fetch"), patch.object(
            mh,
            "_merge_save_minute_bars",
            return_value=([bar], {"data_source": "sina_tx:sina", "bar_count": 1}),
        ):
            bars, meta = mh.fetch_a_minute_bars("600519", use_cache=False, max_age_hours=0)
        bs_fetch.assert_not_called()
        self.assertEqual(len(bars), 1)
        self.assertIn("sina_tx", meta.get("data_source") or "")
        self.assertEqual((meta.get("sina_tx") or {}).get("data_source"), "sina_tx:sina")
        self.assertEqual((meta.get("baostock") or {}).get("reason"), "sina_tx_ok")

    def test_sina_tx_runs_before_baostock(self):
        from adapters.market import minute_history as mh

        order: list[str] = []

        def _sina(*_a, **_k):
            order.append("sina_tx")
            return [], {}

        def _bs(*_a, **_k):
            order.append("baostock")
            return [], {"error": "bs down"}

        with patch.object(mh, "resolve_market_code", return_value=("CN", "600519")), patch.object(
            mh, "load_minute_cache", return_value=None
        ), patch.object(
            mh, "_fetch_em_minute_bars", return_value=([], {}, "em down")
        ), patch.object(
            mh, "_maybe_fetch_sina_tx_minute_bars", side_effect=_sina
        ), patch.object(
            mh, "_maybe_fetch_baostock_minute_bars", side_effect=_bs
        ), patch.object(mh, "_throttle_minute_remote_fetch"), patch.object(
            mh, "_load_stale_minute", return_value=None
        ):
            mh.fetch_a_minute_bars("600519", use_cache=False, max_age_hours=0)
        self.assertEqual(order, ["sina_tx", "baostock"])

    def test_sina_tx_ok_skips_baostock(self):
        from adapters.market import minute_history as mh

        bar = {
            "datetime": "2026-08-28 09:35:00",
            "date": "2026-08-28",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 100,
        }
        order: list[str] = []

        def _sina(*_a, **_k):
            order.append("sina_tx")
            return [bar], {"data_source": "sina_tx:sina"}

        def _bs(*_a, **_k):
            order.append("baostock")
            return [], {"error": "should_not_run"}

        with patch.object(mh, "resolve_market_code", return_value=("CN", "600519")), patch.object(
            mh, "load_minute_cache", return_value=None
        ), patch.object(
            mh, "_fetch_em_minute_bars", return_value=([], {}, "em down")
        ), patch.object(
            mh, "_maybe_fetch_sina_tx_minute_bars", side_effect=_sina
        ), patch.object(
            mh, "_maybe_fetch_baostock_minute_bars", side_effect=_bs
        ), patch.object(mh, "_throttle_minute_remote_fetch"), patch.object(
            mh,
            "_merge_save_minute_bars",
            return_value=([bar], {"data_source": "sina_tx:sina", "bar_count": 1}),
        ):
            bars, meta = mh.fetch_a_minute_bars("600519", use_cache=False, max_age_hours=0)
        self.assertEqual(order, ["sina_tx"])
        self.assertEqual(len(bars), 1)
        self.assertEqual((meta.get("baostock") or {}).get("skipped"), True)


if __name__ == "__main__":
    unittest.main()
