"""BaoStock 分钟线备用源。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestBaostockMinute(unittest.TestCase):
    def test_to_baostock_symbol(self):
        from skills.common.baostock_minute import to_baostock_symbol

        self.assertEqual(to_baostock_symbol("600519"), "sh.600519")
        self.assertEqual(to_baostock_symbol("000001"), "sz.000001")

    def test_parse_datetime(self):
        from skills.common.baostock_minute import _parse_baostock_datetime

        self.assertEqual(
            _parse_baostock_datetime("2024-08-20", "093500000"),
            "2024-08-20 09:35:00",
        )

    def test_em_fail_bs_fallback_merges(self):
        from skills.common import minute_history as mh

        em_bar = {
            "datetime": "2026-08-26 09:35:00",
            "date": "2026-08-26",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 100,
        }
        bs_bar = {
            "datetime": "2024-06-01 09:35:00",
            "date": "2024-06-01",
            "open": 9,
            "high": 10,
            "low": 8,
            "close": 9.5,
            "volume": 200,
        }
        with patch.object(mh, "resolve_market_code", return_value=("CN", "600519")), patch.object(
            mh, "load_minute_cache", return_value=None
        ), patch.object(
            mh, "_fetch_em_minute_bars", return_value=([], {}, "em down")
        ), patch.object(
            mh, "_maybe_fetch_baostock_minute_bars",
            return_value=([bs_bar], {"data_source": "baostock:test"}),
        ), patch.object(
            mh, "_merge_save_minute_bars",
            return_value=([em_bar, bs_bar], {"data_source": "baostock:test", "bar_count": 2}),
        ):
            bars, meta = mh.fetch_a_minute_bars("600519", use_cache=False, max_age_hours=0)
        self.assertEqual(len(bars), 2)
        self.assertIn("baostock", meta.get("data_source") or "")

    def test_fetch_baostock_mock(self):
        from skills.common.baostock_minute import fetch_baostock_minute_bars

        row = ["2024-08-20", "093500000", "10", "10.5", "9.8", "10.2", "1000", "10000"]
        field_names = ["date", "time", "open", "high", "low", "close", "volume", "amount"]

        class _Rs:
            error_code = "0"
            error_msg = ""
            fields = field_names
            _pending = True

            def next(self):
                if self._pending:
                    self._pending = False
                    return True
                return False

            def get_row_data(self):
                return list(row)

        mock_bs = MagicMock()
        mock_bs.login.return_value = MagicMock(error_code="0", error_msg="")
        mock_bs.query_history_k_data_plus.return_value = _Rs()

        with patch("skills.common.baostock_minute._BS_LOGGED_IN", True), patch(
            "baostock.login", mock_bs.login
        ), patch("baostock.query_history_k_data_plus", mock_bs.query_history_k_data_plus):
            bars, meta = fetch_baostock_minute_bars(
                "600519", period="5", start_date="2024-01-01", adjust="qfq", timeout_sec=0
            )
        self.assertEqual(len(bars), 1)
        self.assertTrue(meta.get("ok"))
        self.assertIn("baostock", meta.get("data_source") or "")

    def test_fetch_baostock_timeout_kills_subprocess(self):
        from skills.common.baostock_minute import fetch_baostock_minute_bars

        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        mock_queue = MagicMock()
        mock_queue.empty.return_value = True
        mock_ctx = MagicMock()
        mock_ctx.Queue.return_value = mock_queue
        mock_ctx.Process.return_value = mock_proc

        with patch("multiprocessing.get_context", return_value=mock_ctx):
            bars, meta = fetch_baostock_minute_bars("600276", period="5", timeout_sec=12.0)
        self.assertEqual(bars, [])
        self.assertIn("timeout", str(meta.get("error") or ""))
        mock_proc.kill.assert_called_once()

    def test_minute_baostock_timeout_policy(self):
        from core.data.policy import minute_baostock_timeout_sec

        with patch.dict(os.environ, {"INVESTMENT_MINUTE_BS_TIMEOUT_SEC": "0"}, clear=False):
            self.assertEqual(minute_baostock_timeout_sec(), 0.0)


    def test_skip_em_uses_baostock_only(self):
        from skills.common import minute_history as mh

        bs_bar = {
            "datetime": "2024-06-01 09:35:00",
            "date": "2024-06-01",
            "open": 9,
            "high": 10,
            "low": 8,
            "close": 9.5,
            "volume": 200,
        }
        with patch.object(mh, "resolve_market_code", return_value=("CN", "600519")), patch.object(
            mh, "load_minute_cache", return_value=None
        ), patch.object(
            mh, "_fetch_em_minute_bars"
        ) as em_fetch, patch(
            "skills.common.baostock_minute.fetch_baostock_minute_bars",
            return_value=([bs_bar], {"data_source": "baostock:test", "ok": True}),
        ), patch(
            "skills.common.baostock_minute.baostock_enabled", return_value=True
        ), patch.object(mh, "_throttle_minute_remote_fetch"), patch.object(
            mh, "_merge_save_minute_bars",
            return_value=([bs_bar], {"data_source": "baostock:test", "bar_count": 1}),
        ):
            bars, meta = mh.fetch_a_minute_bars("600519", use_cache=False, skip_em=True)
        em_fetch.assert_not_called()
        self.assertEqual(len(bars), 1)
        self.assertTrue(meta.get("skip_em"))


if __name__ == "__main__":
    unittest.main()
