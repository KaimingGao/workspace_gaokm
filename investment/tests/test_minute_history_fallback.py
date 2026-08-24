"""分钟线：远端失败时回退本地过期缓存。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestMinuteStaleFallback(unittest.TestCase):
    def test_remote_fail_uses_stale_cache(self):
        from skills.common import minute_history as mh

        stale_bars = [
            {
                "datetime": "2026-08-20 09:35:00",
                "date": "2026-08-20",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1000,
            }
        ] * 12
        stale_meta = {"data_source": "akshare:stock_zh_a_hist_min_em:5", "from_cache": True}

        with patch.object(mh, "resolve_market_code", return_value=("CN", "600519")), patch.object(
            mh, "load_minute_cache"
        ) as load_cache, patch(
            "skills.common.ak_lock.import_akshare"
        ) as import_ak, patch("core.http_retry.call_with_retry") as retry:
            # 新鲜缓存 miss；ignore_age 命中过期
            def _load(market, code, period, **kw):
                if kw.get("ignore_age"):
                    return list(stale_bars), dict(stale_meta)
                return None

            load_cache.side_effect = _load
            retry.side_effect = ConnectionError(
                "('Connection aborted.', RemoteDisconnected('Remote end closed'))"
            )
            import_ak.return_value = MagicMock()

            bars, meta = mh.fetch_a_minute_bars("600519", period="5", use_cache=True)
            self.assertEqual(len(bars), 12)
            self.assertTrue(meta.get("cache_stale"))
            self.assertIn("remote_error", meta)
            self.assertTrue(str(meta.get("data_source") or "").startswith("cache:stale"))


if __name__ == "__main__":
    unittest.main()
