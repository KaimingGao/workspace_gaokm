"""FH5+：分组面板 PIT 财务探针与 lookahead_flags。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestClusterPanelsPit(unittest.TestCase):
    def test_pit_on_sets_pit_as_of_flags(self):
        from quant.research.cluster_panels import build_cluster_ols_panels

        bars = [{"date": f"2024-01-{i:02d}", "close": 10.0 + i} for i in range(1, 40)]
        resolved = {
            "ok": True,
            "metrics": {"pe": 12.0, "pb": 1.5},
            "fundamentals_pit": True,
            "mode": "as_of",
            "as_of": "2024-01-20",
        }
        with patch(
            "core.data.facade.get_quote",
            return_value={"success": True, "stock_code": "600519", "stock_name": "茅台"},
        ), patch(
            "core.data.facade.bars_and_source_research", return_value=(bars, "akshare")
        ), patch(
            "core.ports.market.resolve_market_code", return_value=("cn", "600519")
        ), patch(
            "core.ports.market.default_benchmark", return_value="000300"
        ), patch(
            "core.ports.market.fetch_index_bars", return_value=([], "empty")
        ), patch(
            "core.fundamentals_pit.resolve_fundamentals_for_score",
            return_value=resolved,
        ), patch(
            "core.signal.config.load_signal_config",
            return_value={"fundamentals": {"pit_mode": "as_of"}},
        ):
            out = build_cluster_ols_panels(
                ["600519"], lookback=40, pit_fundamentals=True
            )
        flags = out["lookahead_flags"]
        self.assertTrue(flags["pit_fundamentals"])
        self.assertEqual(flags["fundamentals"], "pit_as_of")
        panel = out["panels"][0]
        self.assertEqual(panel.get("fundamentals_mode"), "pit_as_of")
        self.assertIsNotNone(panel.get("fundamentals"))
        self.assertEqual(
            (flags.get("pit_summary") or {}).get("resolved_ok"), 1
        )

    def test_pit_off_marks_none(self):
        from quant.research.cluster_panels import build_cluster_ols_panels

        bars = [{"date": f"2024-01-{i:02d}", "close": 10.0} for i in range(1, 30)]
        with patch(
            "core.data.facade.get_quote",
            return_value={"success": True, "stock_code": "000001"},
        ), patch(
            "core.data.facade.bars_and_source_research", return_value=(bars, "akshare")
        ), patch(
            "core.ports.market.resolve_market_code", return_value=("cn", "000001")
        ), patch(
            "core.ports.market.default_benchmark", return_value="000300"
        ), patch(
            "core.ports.market.fetch_index_bars", return_value=([], "empty")
        ), patch(
            "core.signal.config.load_signal_config", return_value={}
        ):
            out = build_cluster_ols_panels(
                ["000001"], lookback=30, pit_fundamentals=False
            )
        self.assertEqual(out["lookahead_flags"]["fundamentals"], "none")
        self.assertIsNone(out["panels"][0].get("fundamentals"))

    def test_default_bars_use_offline_ok(self):
        from quant.research.cluster_panels import _load_bars_for_cluster

        bars = [{"date": f"2024-01-{i:02d}", "close": 10.0} for i in range(1, 50)]
        calls = []

        def fake_bars(code, **kwargs):
            calls.append(dict(kwargs))
            return bars, "cache"

        with patch("core.data.facade.bars_and_source_research", side_effect=fake_bars):
            out_bars, src, remote = _load_bars_for_cluster(
                "600519", lookback=40, refresh_bars=False
            )
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].get("offline_ok"))
        self.assertFalse(remote)
        self.assertEqual(len(out_bars), 49)
        self.assertEqual(src, "cache")

    def test_refresh_bars_reuses_fresh_cache(self):
        from quant.research.cluster_panels import _load_bars_for_cluster

        bars = [{"date": f"2024-01-{i:02d}", "close": 10.0} for i in range(1, 50)]
        calls = []

        def fake_bars(code, **kwargs):
            calls.append(dict(kwargs))
            return bars, "cache"

        with patch("core.data.facade.bars_and_source_research", side_effect=fake_bars):
            out_bars, src, remote = _load_bars_for_cluster(
                "600519", lookback=40, refresh_bars=True
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("cache_max_age_hours"), 36)
        self.assertFalse(calls[0].get("offline_ok"))
        self.assertFalse(remote)
        self.assertEqual(src, "cache")
        self.assertEqual(len(out_bars), 49)

    def test_refresh_bars_fetches_when_thin(self):
        from quant.research.cluster_panels import _load_bars_for_cluster

        thin = [{"date": "2024-01-01", "close": 10.0}]
        full = [{"date": f"2024-01-{i:02d}", "close": 10.0} for i in range(1, 50)]
        calls = []

        def fake_bars(code, **kwargs):
            calls.append(dict(kwargs))
            if kwargs.get("cache_max_age_hours") == 0:
                return full, "akshare"
            return thin, "cache:stale"

        with patch("core.data.facade.bars_and_source_research", side_effect=fake_bars):
            out_bars, src, remote = _load_bars_for_cluster(
                "600519", lookback=40, refresh_bars=True
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].get("cache_max_age_hours"), 0)
        self.assertTrue(calls[1].get("incremental"))
        self.assertTrue(remote)
        self.assertEqual(src, "akshare")
        self.assertEqual(len(out_bars), 49)

    def test_force_latest_skips_36h_reuse(self):
        from quant.research.cluster_panels import _load_bars_for_cluster

        bars = [{"date": f"2024-01-{i:02d}", "close": 10.0} for i in range(1, 50)]
        calls = []

        def fake_bars(code, **kwargs):
            calls.append(dict(kwargs))
            return bars, "akshare_cn_daily:qfq"

        with patch("core.data.facade.bars_and_source_research", side_effect=fake_bars):
            out_bars, src, remote = _load_bars_for_cluster(
                "600519",
                lookback=40,
                refresh_bars=True,
                force_latest_bars=True,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("cache_max_age_hours"), 0)
        self.assertTrue(calls[0].get("incremental"))
        self.assertTrue(remote)
        self.assertIn("akshare", src)
        self.assertEqual(len(out_bars), 49)

    def test_build_panels_force_latest_stats(self):
        from quant.research.cluster_panels import build_cluster_ols_panels

        bars = [{"date": f"2024-01-{i:02d}", "close": 10.0} for i in range(1, 50)]
        with patch(
            "core.data.facade.bars_and_source_research",
            return_value=(bars, "akshare_cn_daily:qfq"),
        ), patch(
            "core.ports.market.resolve_market_code", return_value=("cn", "000001")
        ), patch(
            "core.ports.market.default_benchmark", return_value="000300"
        ), patch(
            "core.ports.market.fetch_index_bars", return_value=([], "empty")
        ), patch(
            "core.signal.config.load_signal_config", return_value={}
        ):
            out = build_cluster_ols_panels(
                ["000001"],
                lookback=30,
                pit_fundamentals=False,
                refresh_bars=False,
                force_latest_bars=True,
            )
        br = out.get("bars_refresh") or {}
        self.assertTrue(br.get("requested"))
        self.assertTrue(br.get("force_latest"))
        self.assertEqual(br.get("remote_count"), 1)
        self.assertIn("强制增量", str(br.get("note") or ""))

    def test_build_panels_bars_refresh_stats(self):
        from quant.research.cluster_panels import build_cluster_ols_panels

        bars = [{"date": f"2024-01-{i:02d}", "close": 10.0} for i in range(1, 50)]
        with patch(
            "core.data.facade.bars_and_source_research", return_value=(bars, "cache")
        ), patch(
            "core.ports.market.resolve_market_code", return_value=("cn", "000001")
        ), patch(
            "core.ports.market.default_benchmark", return_value="000300"
        ), patch(
            "core.ports.market.fetch_index_bars", return_value=([], "empty")
        ), patch(
            "core.signal.config.load_signal_config", return_value={}
        ):
            out = build_cluster_ols_panels(
                ["000001"], lookback=30, pit_fundamentals=False, refresh_bars=True
            )
        br = out.get("bars_refresh") or {}
        self.assertTrue(br.get("requested"))
        self.assertEqual(br.get("remote_count"), 0)
        self.assertEqual(br.get("cache_count"), 1)

    def test_cache_first_skips_remote_index(self):
        """缓存优先不得同步打 fetch_index_bars（否则易卡在 0/N 被 90s 回收）。"""
        from quant.research.cluster_panels import build_cluster_ols_panels

        bars = [{"date": f"2024-01-{i:02d}", "close": 10.0} for i in range(1, 50)]
        msgs = []

        def _prog(msg, cur=0, tot=0):
            msgs.append(str(msg))

        with patch(
            "core.data.facade.bars_and_source_research", return_value=(bars, "cache")
        ), patch(
            "core.ports.market.resolve_market_code", return_value=("CN", "000001")
        ), patch(
            "core.ports.market.default_benchmark", return_value="hs300"
        ), patch(
            "core.ports.market.fetch_index_bars",
            side_effect=AssertionError("cache-first must not call remote index"),
        ), patch(
            "core.signal.config.load_signal_config", return_value={}
        ):
            out = build_cluster_ols_panels(
                ["000001"],
                lookback=30,
                pit_fundamentals=False,
                refresh_bars=False,
                progress_cb=_prog,
            )
        self.assertEqual(len(out.get("panels") or []), 1)
        self.assertTrue(any("缓存优先" in m for m in msgs))

    def test_refresh_index_timeout_continues(self):
        import time

        from quant.research.cluster_panels import _load_index_bars_once

        def _hang(*_a, **_k):
            time.sleep(30)
            return [], "empty"

        with patch(
            "core.data.facade.bars_and_source_research", return_value=([], "empty")
        ), patch(
            "core.ports.market.fetch_index_bars", side_effect=_hang
        ):
            t0 = time.time()
            bars = _load_index_bars_once(
                "hs300",
                limit=40,
                refresh_bars=True,
                timeout_sec=2.5,
            )
            elapsed = time.time() - t0
        self.assertEqual(bars, [])
        self.assertLess(elapsed, 8.0)

class TestClusterLiveAuditSplit(unittest.TestCase):
    def test_pick_audit_codes_round_robin(self):
        from core.signal.cluster_live_audit import pick_audit_codes

        cmap = {
            "a": {"cluster_label": "G1"},
            "b": {"cluster_label": "G1"},
            "c": {"cluster_label": "G2"},
        }
        codes = pick_audit_codes(cmap, [{"stock_code": "a"}], 2, offset=0)
        self.assertEqual(len(codes), 2)
        self.assertIn("a", codes)


if __name__ == "__main__":
    unittest.main()
