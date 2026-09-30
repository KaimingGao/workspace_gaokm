"""做 T 前缀重算 vs 研究枢纽 compute 路径：ŷ_τ / ŷ_hl 同源验收。"""

from __future__ import annotations

import unittest


def _bar(d: str, o: float, h: float, l: float, c: float, *, prev=None) -> dict:
    b = {"date": d, "open": o, "high": h, "low": l, "close": c}
    if prev is not None:
        b["prev_close"] = prev
    return b


def _mins(closes, *, session_open: float = 100.0, day: str = "2025-08-28"):
    out = []
    hms = ["09:35", "09:40", "09:45", "09:50", "09:55", "10:00"]
    for i, c in enumerate(closes):
        hm = hms[i] if i < len(hms) else f"10:{(i - 6) * 5 + 5:02d}"
        out.append(
            {
                "date": day,
                "datetime": f"{day} {hm}:00",
                "open": session_open if i == 0 else closes[i - 1],
                "high": max(session_open if i == 0 else closes[i - 1], c) * 1.002,
                "low": min(session_open if i == 0 else closes[i - 1], c) * 0.998,
                "close": c,
            }
        )
    return out


class TestT0HubScoreAlignment(unittest.TestCase):
    def test_rescore_prefix_matches_compute_scores_from_bars(self):
        """做 T 每根前缀 rescore 与 compute_scores_from_bars(use_minute_tau=True) 同值。"""
        from core.t0.score_policy import (
            clear_score_model_cache,
            compute_scores_from_bars,
            rescore_scores_at_fixed_prefix,
            resolve_direction_y_tau,
            scores_have_any,
        )

        clear_score_model_cache()
        code = "600000"
        hist = []
        px = 77.0
        for i in range(30):
            d = f"2025-07-{(i % 28) + 1:02d}"
            o = px
            c = px * (1.0 + (0.008 if i % 4 == 0 else -0.004))
            hist.append(_bar(d, o, max(o, c) * 1.01, min(o, c) * 0.99, c))
            px = c
        day = _bar("2025-08-28", 78.33, 79.1, 78.31, 78.25, prev=77.82)
        prefix = _mins(
            [78.33, 78.97, 78.70, 78.53, 78.61, 78.35],
            session_open=78.33,
            day="2025-08-28",
        )
        pool = {
            "pool_gaps": [0.1, -0.2, 0.3],
            "sector_gap_breadth": 0.55,
            "ref_by_code": {code: 0.12},
        }

        direct = compute_scores_from_bars(
            code,
            hist,
            day_bar=day,
            fuse_intraday=True,
            minute_bars=prefix,
            use_minute_tau=True,
            minute_tau_hm="10:00",
            pool_gaps=pool["pool_gaps"],
            sector_gap_breadth=pool["sector_gap_breadth"],
            sector_gap_median=pool["ref_by_code"][code],
        )
        rescore = rescore_scores_at_fixed_prefix(
            stock_code=code,
            minute_prefix=prefix,
            day_bar=day,
            hist_bars=hist,
            tau_pool_day=pool,
            fuse_intraday=True,
        )
        if not scores_have_any(direct) or not scores_have_any(rescore):
            self.skipTest("τ/path 模型未加载，跳过数值对齐验收")

        for key in ("y_tau_oc", "y_path", "predicted_score_tau"):
            a = direct.get(key)
            b = rescore.get(key)
            if a is None and b is None:
                continue
            self.assertIsNotNone(a, key)
            self.assertIsNotNone(b, key)
            self.assertAlmostEqual(float(a), float(b), places=6, msg=key)

        self.assertAlmostEqual(
            float(resolve_direction_y_tau(direct)),
            float(resolve_direction_y_tau(rescore)),
            places=6,
        )
        self.assertEqual(rescore.get("_score_source"), "prefix_causal")

    def test_rescore_open_fallback_rebuilds_oc_formula_from_pack(self):
        """开盘锚回退后仍按前缀小包重拆 ŷ_oc，组成表要有开盘→τ。"""
        from core.t0.score_policy import (
            clear_score_model_cache,
            rescore_scores_at_fixed_prefix,
        )

        clear_score_model_cache()
        day = _bar("2025-08-28", 78.33, 79.1, 78.31, 78.25, prev=77.82)
        prefix = _mins(
            [78.33, 78.97],
            session_open=78.33,
            day="2025-08-28",
        )
        open_snap = {
            "stock_code": "600000",
            "y_oc": 0.197,
            "predicted_score_tau": 0.197,
            "as_of_tau": "open",
            "formula_terms_tau": {
                "intercept": 0.197,
                "total": 0.197,
                "terms": [
                    {
                        "key": "theme_day",
                        "label": "主题日",
                        "beta": -0.05,
                        "z": -0.3,
                        "contrib": 0.015,
                    }
                ],
            },
            "features_tau": {"theme_day": 0.0, "sector_gap_breadth": 0.0},
        }
        sc = rescore_scores_at_fixed_prefix(
            stock_code="600000",
            minute_prefix=prefix,
            day_bar=day,
            hist_bars=[],
            fuse_intraday=True,
            open_snap=open_snap,
        )
        feats = sc.get("features_tau") or {}
        if feats.get("ret_open_to_tau") is None:
            self.skipTest("前缀未切出开盘→τ")
        ft = sc.get("formula_terms_tau") or {}
        keys = [t.get("key") for t in (ft.get("terms") or []) if isinstance(t, dict)]
        self.assertIn("ret_open_to_tau", keys)
        self.assertIn("09:40", str(sc.get("as_of_tau") or ""))

    def test_refresh_tau_oc_after_inject_when_as_of_already_clock(self):
        """盘中 as_of 已写 09:40、组成已有开盘→τ 时，注入后的小因子仍要进 ŷ_oc。"""
        from unittest.mock import patch

        from core.t0.score_policy import _refresh_tau_oc_from_feats

        item = {
            "as_of_tau": "2026-09-18T09:40:00+08:00",
            "y_oc": 2.214,
            "predicted_score_tau": 2.214,
            "features_tau": {
                "ret_open_to_tau": 2.17,
                "loc_hl": 0.69,
                "sector_gap_breadth": 0.008,
            },
            "formula_terms_tau": {
                "total": 2.214,
                "terms": [
                    {
                        "key": "ret_open_to_tau",
                        "label": "开盘→τ 收益 %",
                        "contrib": 0.026,
                    }
                ],
            },
        }
        expl = {
            "intercept": 0.149,
            "total": 2.203,
            "terms": [
                {"key": "ret_open_to_tau", "contrib": 0.026},
                {"key": "loc_hl", "contrib": 0.015},
                {"key": "sector_gap_breadth", "contrib": -0.025},
            ],
        }
        with patch(
            "core.research.tc_ridge.predict_tau_from_features", return_value=2.203
        ), patch(
            "core.research.tc_ridge.explain_tau_prediction", return_value=expl
        ):
            _refresh_tau_oc_from_feats(
                item, hm="09:40", trade_date="2026-09-18"
            )
        self.assertAlmostEqual(float(item["y_oc"]), 2.203, places=6)
        keys = {
            t.get("key")
            for t in (item.get("formula_terms_tau") or {}).get("terms") or []
            if isinstance(t, dict)
        }
        self.assertIn("loc_hl", keys)
        self.assertIn("sector_gap_breadth", keys)

    def test_inject_prefix_overwrites_stale_sector_cs(self):
        """开盘锚上的 10:00 板块中位不得留在 09:35 前缀。"""
        from unittest.mock import patch

        from core.t0.score_policy import _inject_minute_pack_from_prefix

        day = _bar("2025-08-28", 78.33, 79.1, 78.31, 78.25, prev=77.82)
        prefix = _mins([78.33], session_open=78.33, day="2025-08-28")[:1]
        item = {
            "features_tau": {
                "theme_day": 0.0,
                "sector_ret_to_tau": 2.12,
                "ret_vs_sector": -1.94,
            }
        }
        with patch(
            "core.signal.minute_tau_feats.resolve_sector_ret_to_tau", return_value=0.40
        ), patch(
            "core.signal.minute_tau_feats.resolve_sector_ret_last_30m", return_value=None
        ), patch(
            "core.signal.minute_tau_feats.resolve_sector_ret_last_45m", return_value=None
        ), patch(
            "core.signal.minute_tau_feats.resolve_sector_ret_last_60m", return_value=None
        ), patch(
            "core.signal.minute_tau_feats.resolve_sector_ret_last_75m", return_value=None
        ), patch(
            "core.signal.minute_tau_feats.resolve_sector_ret_last_90m", return_value=None
        ), patch(
            "core.t0.score_policy.current_t0_cs_universe_codes", return_value=["600000"]
        ):
            _inject_minute_pack_from_prefix(
                item, minute_prefix=prefix, day_bar=day, hm="09:35"
            )
        feats = item.get("features_tau") or {}
        self.assertAlmostEqual(float(feats.get("sector_ret_to_tau")), 0.40, places=4)
        self.assertIsNotNone(feats.get("gap_pct"))
        self.assertIsNotNone(item.get("gap_pct"))

    def test_direction_y_tau_prefers_oc_over_mapped(self):
        """表列/tip 用 OC 头；mapped predicted_score_tau 不得盖 y_tau_oc。"""
        from core.t0.score_policy import resolve_direction_y_tau, scores_from_item

        sc = scores_from_item(
            {
                "y_tau_oc": 0.23,
                "predicted_score_tau_oc": 0.23,
                "predicted_score_tau": -0.65,
                "score_rem": -0.65,
                "y_path": 0.59,
                "predicted_score_path": 0.59,
            }
        )
        self.assertAlmostEqual(float(resolve_direction_y_tau(sc)), 0.23, places=6)
        self.assertAlmostEqual(float(sc["y_hl"]), 0.59, places=6)
        self.assertAlmostEqual(float(sc["y_tau"]), 0.23, places=6)


class TestHoldingsTauOpenZAlignment(unittest.TestCase):
    def test_score_stock_causal_prefix_matches_t0_at_same_clock(self):
        """持仓 score_stock 调仓因果末根（≤10:00）与做 T 同钟 rescore 同值。"""
        from core.ports.market import resolve_market_code
        from core.signal.score_stock import score_stock
        from core.store import load_daily_cache, load_minute_cache
        from core.t0.score_policy import (
            _minute_bars_until_hm,
            rescore_scores_at_fixed_prefix,
        )

        code = "000568"
        mkt, pure = resolve_market_code(code)
        daily = (load_daily_cache(mkt, pure, min_bars=20) or (None, None))[0]
        packed = load_minute_cache(
            mkt, pure, period="5", min_bars=1, ignore_age=True
        )
        mbars = packed[0] if packed else None
        if not daily or not mbars:
            self.skipTest("无本地日线/分钟仓")

        raw = score_stock(
            code, skip_fundamentals=True, skip_sentiment=True, offline_only=True
        )
        it = (raw or {}).get("signal_item") or {}
        y_hold = it.get("y_tau_oc")
        if y_hold is None:
            self.skipTest("score_stock 未出 ŷ_τ")
        ft = it.get("features_tau") if isinstance(it.get("features_tau"), dict) else {}
        fill = it.get("features_tau_fill") if isinstance(it.get("features_tau_fill"), dict) else {}
        present = set(fill.get("present") or [])
        for k in (
            "yclose_loc",
            "mom3_pct",
            "sector_gap_breadth",
            "gap_vs_sector",
        ):
            self.assertIsNotNone(ft.get(k), k)
            self.assertIn(k, present, fill)

        asof = str(it.get("as_of_tau") or "")[:10]
        asof_hm = ""
        raw_asof = str(it.get("as_of_tau") or "")
        if "T" in raw_asof:
            asof_hm = raw_asof.split("T", 1)[1][:5]
        if len(asof) < 10:
            asof = str((daily[-1] or {}).get("date") or "")[:10]
        hist = [b for b in daily if str(b.get("date") or "")[:10] < asof]
        day_bars = [b for b in daily if str(b.get("date") or "")[:10] == asof]
        if not hist or not day_bars:
            self.skipTest("日线切不出 asof 窗")
        day_bar = dict(day_bars[-1])
        if day_bar.get("prev_close") is None:
            day_bar["prev_close"] = hist[-1].get("close")
        prefix_hm = asof_hm or "10:00"
        prefix = _minute_bars_until_hm(mbars, prefix_hm)
        if len(prefix) < 1:
            self.skipTest(f"无 {prefix_hm} 前缀")
        self.assertLessEqual(prefix_hm, "10:00")
        sc = rescore_scores_at_fixed_prefix(
            stock_code=code,
            minute_prefix=prefix,
            day_bar=day_bar,
            hist_bars=hist,
            fuse_intraday=True,
        )
        y_t0 = sc.get("y_tau_oc")
        if y_t0 is None:
            self.skipTest("T0 前缀未出 ŷ_τ")
        self.assertAlmostEqual(float(y_hold), float(y_t0), places=5)


if __name__ == "__main__":
    unittest.main()
