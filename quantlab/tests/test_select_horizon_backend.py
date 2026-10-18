"""ŷ_τ30…90 双 backend：Holdout AUC 择优。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class SelectHorizonBackendTests(unittest.TestCase):
    def test_select_by_auc(self):
        from core.research.horizon_prob import select_horizon_backend

        self.assertEqual(
            select_horizon_backend(
                {"auc": 0.62, "brier": 0.25, "success": True},
                {"auc": 0.55, "brier": 0.22, "success": True},
            ),
            "tree",
        )
        self.assertEqual(
            select_horizon_backend(
                {"auc": 0.55, "brier": 0.20, "success": True},
                {"auc": 0.62, "brier": 0.25, "success": True},
            ),
            "ridge",
        )

    def test_tie_auc_uses_brier_then_ridge(self):
        from core.research.horizon_prob import select_horizon_backend

        self.assertEqual(
            select_horizon_backend(
                {"auc": 0.55, "brier": 0.20, "success": True},
                {"auc": 0.55, "brier": 0.24, "success": True},
            ),
            "tree",
        )
        self.assertEqual(
            select_horizon_backend(
                {"auc": 0.55, "brier": 0.24, "success": True},
                {"auc": 0.55, "brier": 0.20, "success": True},
            ),
            "ridge",
        )
        # 打平 → ridge
        self.assertEqual(
            select_horizon_backend(
                {"auc": 0.55, "brier": 0.22, "success": True},
                {"auc": 0.55, "brier": 0.22, "success": True},
            ),
            "ridge",
        )

    def test_missing_sides(self):
        from core.research.horizon_prob import select_horizon_backend

        self.assertEqual(
            select_horizon_backend(None, {"auc": 0.7, "success": True}),
            "ridge",
        )
        self.assertEqual(
            select_horizon_backend({"auc": 0.7, "success": True}, None),
            "tree",
        )
        self.assertEqual(
            select_horizon_backend({"success": False}, {"auc": 0.6, "success": True}),
            "ridge",
        )
        self.assertEqual(select_horizon_backend(None, None), "ridge")

    def test_promote_gate_uses_winner_oos(self):
        from core.research.horizon_prob import horizon_promote_gate

        report = {
            "recommended_backend": "tree",
            "oos": {
                "auc": 0.60,
                "acc_at_50": 0.58,
                "n_test": 100,
                "ridge": {"auc": 0.51, "acc_at_50": 0.50, "n_test": 100},
                "tree": {"auc": 0.60, "acc_at_50": 0.58, "n_test": 100},
            },
        }
        gate = horizon_promote_gate(report)
        self.assertTrue(gate.get("ok"), gate)
        self.assertEqual(gate.get("backend"), "tree")
        self.assertAlmostEqual(float(gate["auc"]), 0.60)

        # 胜出侧未过闸
        report_bad = {
            "recommended_backend": "tree",
            "oos": {
                "ridge": {"auc": 0.60, "acc_at_50": 0.58, "n_test": 100},
                "tree": {"auc": 0.50, "acc_at_50": 0.50, "n_test": 100},
            },
        }
        gate_bad = horizon_promote_gate(report_bad)
        self.assertFalse(gate_bad.get("ok"), gate_bad)

    def test_resolve_oos_skips_empty_nested_side(self):
        """胜出侧嵌套为空 dict 时回退扁平 / 另一侧，避免晋级闸误报缺 OOS。"""
        from core.research.horizon_prob import (
            horizon_promote_gate,
            resolve_horizon_oos_for_gate,
        )

        report = {
            "recommended_backend": "tree",
            "oos": {
                "auc": 0.61,
                "acc_at_50": 0.57,
                "n_test": 120,
                "tree": {},
                "ridge": {"auc": 0.55, "acc_at_50": 0.54, "n_test": 120},
            },
        }
        side = resolve_horizon_oos_for_gate(report)
        self.assertAlmostEqual(float(side["auc"]), 0.61)
        gate = horizon_promote_gate(report)
        self.assertTrue(gate.get("ok"), gate)
        self.assertAlmostEqual(float(gate["auc"]), 0.61)


class HorizonDualMergeTests(unittest.TestCase):
    def test_backend_pack_and_doc_backend(self):
        from core.research.horizon_ridge import _backend_pack_from_report, _doc_backend

        report = {
            "backends": {
                "ridge": {"return_model": {"coefficients": {"a": 1.0}}},
                "tree": {"return_model": {"feature_names": ["a"], "booster": {}}},
            },
            "return_model": {"coefficients": {"a": 1.0}},
        }
        self.assertIsNotNone(_backend_pack_from_report(report, "tree"))
        self.assertEqual(
            _doc_backend({"backend": "tree", "return_model": report["backends"]["tree"]["return_model"]}),
            "tree",
        )
        self.assertEqual(_doc_backend({"return_model": {"coefficients": {"a": 1}}}), "ridge")

    def test_persist_research_accepts_tree_winner(self):
        """dual-fit Tree 胜出时 research persist 不再误报 return_model missing。"""
        import tempfile
        from unittest.mock import patch

        from core.research.horizon_ridge import persist_ridge_model

        tree_rm = {
            "feature_names": ["gap_pct"],
            "booster": {"model": "stub"},
            "backend": "lightgbm",
        }
        report = {
            "success": True,
            "recommended_backend": "tree",
            "backends": {
                "ridge": {
                    "return_model": {"coefficients": {"gap_pct": 0.1}, "backend": "ridge"}
                },
                "tree": {"return_model": tree_rm, "return_model_research": tree_rm},
            },
            "return_model": tree_rm,
            "oos": {
                "ridge": {"auc": 0.55, "acc_at_50": 0.54, "n_test": 80, "success": True},
                "tree": {"auc": 0.62, "acc_at_50": 0.60, "n_test": 80, "success": True},
            },
            "schema": "t30_ridge_v4",
        }
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live), patch(
                "core.research.horizon_ridge.ridge_model_path",
                return_value=os.path.join(live, "t30_ridge_model.json"),
            ):
                out = persist_ridge_model(
                    "t30", report, note="tree winner", force=True, role="research"
                )
        self.assertTrue(out.get("success"), out)
        self.assertEqual(out.get("backend"), "tree")
        self.assertEqual(
            (out.get("return_model") or {}).get("feature_names"), ["gap_pct"]
        )


def _has_lightgbm() -> bool:
    try:
        import lightgbm  # noqa: F401

        return True
    except ImportError:
        return False


def _bar(dkey: str, hm: str, close: float, open_px: float | None = None):
    o = open_px if open_px is not None else close
    return {
        "date": dkey,
        "time": hm,
        "open": round(o, 4),
        "high": round(max(o, close), 4),
        "low": round(min(o, close), 4),
        "close": round(close, 4),
        "volume": 1000,
    }


def _times_am_pm():
    out = []
    for total in range(9 * 60 + 30, 11 * 60 + 30 + 1, 5):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
    for total in range(13 * 60, 15 * 60 + 1, 5):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
    return out


def _stock(code: str, start: float, n_days: int = 40):
    from datetime import date, timedelta

    d0 = date(2025, 6, 2)
    daily = []
    minutes = []
    px = start
    times = _times_am_pm()
    i = 0
    day = d0
    while len(daily) < n_days:
        if day.weekday() < 5:
            dkey = day.isoformat()
            bars_m = []
            for j, hm in enumerate(times):
                c = px + (0.04 if j % 2 else 0.0) + j * 0.002 + (i % 3) * 0.01
                bars_m.append(_bar(dkey, hm, c, open_px=px))
            minutes.extend(bars_m)
            close = bars_m[-1]["close"]
            daily.append(
                {
                    "date": dkey,
                    "open": px,
                    "high": max(px, close) * 1.01,
                    "low": min(px, close) * 0.99,
                    "close": close,
                    "volume": 1e6,
                    "prev_close": px * 0.995,
                }
            )
            px = close
            i += 1
        day = day + timedelta(days=1)
    return {"code": code, "bars": daily, "minute_bars": minutes}


class HorizonDualFitTests(unittest.TestCase):
    def test_dual_falls_back_ridge_when_tree_fails(self):
        from unittest.mock import patch

        from core.research.horizon_ridge import fit_horizon_dual_report

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        with patch(
            "core.research.horizon_tree.fit_horizon_tree_report",
            return_value={"success": False, "error": "mock tree down"},
        ):
            report = fit_horizon_dual_report(
                "t30",
                stock_bars,
                ridge_lambda=1.0,
                holdout_trading_days=8,
                tau_grid=["09:30", "09:50", "10:30", "11:15"],
            )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("recommended_backend"), "ridge")
        self.assertFalse(report.get("tree_success"))
        self.assertIn("ridge", report.get("backends") or {})
        self.assertNotIn("tree", report.get("backends") or {})

    def test_dual_keeps_tree_gain_when_ridge_wins(self):
        from unittest.mock import patch

        from core.research.horizon_ridge import fit_horizon_dual_report

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        tree_fi = [{"key": "gap_pct", "gain": 1.0, "share": 1.0}]
        tree_rm = {"feature_names": ["gap_pct"], "booster": {}, "backend": "lightgbm"}
        with patch(
            "core.research.horizon_tree.fit_horizon_tree_report",
            return_value={
                "success": True,
                "oos": {"auc": 0.51, "brier": 0.30, "n_test": 40, "success": True},
                "tree_return_model": tree_rm,
                "feature_importance": tree_fi,
                "timing": {"tree_s": 0.1},
            },
        ), patch(
            "core.research.horizon_ridge.select_horizon_backend",
            return_value="ridge",
        ):
            report = fit_horizon_dual_report(
                "t30",
                stock_bars,
                ridge_lambda=1.0,
                holdout_trading_days=8,
                tau_grid=["09:30", "09:50", "10:30", "11:15"],
            )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("recommended_backend"), "ridge")
        self.assertEqual(report.get("feature_importance"), tree_fi)
        tree_pack = (report.get("backends") or {}).get("tree") or {}
        self.assertEqual(
            (tree_pack.get("return_model") or {}).get("feature_importance"),
            tree_fi,
        )

    def test_stream_panel_attach_breadth_false(self):
        """逐票 attach_breadth=False 再 finalize，与整池建板样本数一致。"""
        from core.research.horizon_ridge import enrich_horizon_raw_panels
        from core.research.tc_ridge_compact import build_tau_panels_from_bars

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        grid = ["09:30", "09:50", "10:30", "11:15"]
        raw_acc = []
        for it in stock_bars:
            chunk = build_tau_panels_from_bars(
                [it],
                min_history=12,
                gap_trigger_pct=2.0,
                tau_hm="10:30",
                tau_grid=grid,
                include_alpha_th=True,
                attach_breadth=False,
                label_heads=("t30",),
            )
            raw_acc.extend(chunk)
            self.assertIsNone(it.get("minute_bars"))
        bulk = build_tau_panels_from_bars(
            [
                _stock("A", 10.0),
                _stock("B", 12.0),
                _stock("C", 8.0),
                _stock("D", 15.0),
            ],
            min_history=12,
            gap_trigger_pct=2.0,
            tau_hm="10:30",
            tau_grid=grid,
            include_alpha_th=True,
            attach_breadth=True,
            label_heads=("t30",),
        )
        streamed = enrich_horizon_raw_panels(
            "t30", raw_acc, gap_trigger_pct=2.0, already_breadth=False
        )
        bulk_en = enrich_horizon_raw_panels(
            "t30", bulk, gap_trigger_pct=2.0, already_breadth=True
        )
        self.assertGreater(len(streamed), 0)
        n_stream = sum(len(p.get("ys") or []) for p in streamed)
        n_bulk = sum(len(p.get("ys") or []) for p in bulk_en)
        # relabel 后样本行数应一致（同一批票）
        self.assertEqual(n_stream, n_bulk)

    def test_label_heads_t30_skips_other_price_keys(self):
        from core.research.tau_panel import (
            collect_tau_intraday_panel,
            normalize_label_heads,
        )

        self.assertEqual(normalize_label_heads(("t30",)), ("t30",))
        self.assertEqual(len(normalize_label_heads(None)), 5)
        it = _stock("A", 10.0, n_days=30)
        _xs, ys, _dates, metas = collect_tau_intraday_panel(
            it["bars"],
            it["minute_bars"],
            tau_hm="10:30",
            tau_grid=["09:30", "10:00", "10:30"],
            min_history=12,
            stock_code="A",
            include_alpha_th=True,
            label_heads=("t30",),
        )
        self.assertGreater(len(ys), 0)
        self.assertTrue(any(m.get("price_tau30") is not None for m in metas))
        self.assertFalse(any("price_tau90" in m for m in metas))
        self.assertFalse(any("price_tau45" in m for m in metas))

    def test_parallel_workers_match_serial_for_label_heads(self):
        from core.research.tc_ridge_compact import build_tau_panels_from_bars

        def _fresh():
            return [
                _stock("A", 10.0),
                _stock("B", 12.0),
                _stock("C", 8.0),
                _stock("D", 15.0),
                _stock("E", 11.0),
                _stock("F", 9.0),
            ]

        grid = ["09:30", "09:50", "10:30", "11:00"]
        kw = dict(
            min_history=12,
            gap_trigger_pct=2.0,
            tau_hm="10:30",
            tau_grid=grid,
            include_alpha_th=True,
            attach_breadth=False,
            label_heads=("t30",),
        )
        serial = build_tau_panels_from_bars(_fresh(), workers=1, **kw)
        parallel = build_tau_panels_from_bars(_fresh(), workers=4, **kw)
        n_s = sum(len(p.get("ys") or []) for p in serial)
        n_p = sum(len(p.get("ys") or []) for p in parallel)
        self.assertEqual(n_s, n_p)
        self.assertGreater(n_s, 0)
        # 顺序按输入票序稳定
        self.assertEqual(
            [p.get("code") for p in serial],
            [p.get("code") for p in parallel],
        )

    def test_dual_shares_enriched_panels_and_drops_minutes(self):
        """双训只建一次面板；Tree 吃 enriched_panels，stock_bars 上不再挂分钟线。"""
        from unittest.mock import patch

        from core.research.horizon_ridge import fit_horizon_dual_report

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        seen = {"tree_has_panels": False, "tree_n_minute": -1, "ridge_has_panels": False}

        def _fake_ridge(head, bars, **kwargs):
            seen["ridge_has_panels"] = kwargs.get("enriched_panels") is not None
            seen["ridge_n_minute"] = sum(
                1 for it in bars if isinstance(it, dict) and it.get("minute_bars")
            )
            return {
                "success": True,
                "schema": "t30_ridge_v4",
                "oos": {"auc": 0.54, "brier": 0.25, "n_test": 40, "success": True},
                "return_model": {"coefficients": {"gap_pct": 0.1}, "backend": "ridge"},
                "return_model_research": {
                    "coefficients": {"gap_pct": 0.1},
                    "backend": "ridge",
                },
                "feature_pack": "alpha_τh",
                "n_alpha_th_features": 43,
            }

        def _fake_tree(head, bars, **kwargs):
            panels = kwargs.get("enriched_panels")
            seen["tree_has_panels"] = panels is not None and len(list(panels or [])) > 0
            seen["tree_n_minute"] = sum(
                1 for it in bars if isinstance(it, dict) and it.get("minute_bars")
            )
            return {
                "success": True,
                "oos": {"auc": 0.55, "brier": 0.24, "n_test": 40, "success": True},
                "tree_return_model": {
                    "feature_names": ["gap_pct"],
                    "booster": {},
                    "backend": "lightgbm",
                },
                "feature_importance": [
                    {"key": "gap_pct", "gain": 1.0, "share": 1.0}
                ],
            }

        with patch(
            "core.research.horizon_ridge.fit_horizon_ridge_report",
            side_effect=_fake_ridge,
        ), patch(
            "core.research.horizon_tree.fit_horizon_tree_report",
            side_effect=_fake_tree,
        ):
            report = fit_horizon_dual_report(
                "t30",
                stock_bars,
                ridge_lambda=1.0,
                holdout_trading_days=8,
                tau_grid=["09:30", "09:50", "10:30", "11:15"],
            )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertTrue(seen["ridge_has_panels"])
        self.assertTrue(seen["tree_has_panels"])
        self.assertEqual(seen["ridge_n_minute"], 0)
        self.assertEqual(seen["tree_n_minute"], 0)
        self.assertTrue(
            all(not (isinstance(it, dict) and it.get("minute_bars")) for it in stock_bars)
        )

    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_fit_dual_report_has_backends(self):
        from core.research.horizon_ridge import fit_horizon_dual_report

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        report = fit_horizon_dual_report(
            "t30",
            stock_bars,
            ridge_lambda=1.0,
            holdout_trading_days=8,
            n_estimators=20,
            tau_grid=["09:30", "09:50", "10:30", "11:15"],
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertIn(report.get("recommended_backend"), ("ridge", "tree"))
        backends = report.get("backends") or {}
        self.assertIn("ridge", backends)
        self.assertTrue(report.get("tree_success"), report.get("tree_error"))
        self.assertIn("tree", backends)
        oos = report.get("oos") or {}
        self.assertIn("ridge", oos)
        self.assertIn("tree", oos)
        self.assertIsInstance((backends["ridge"] or {}).get("return_model"), dict)
        self.assertIsInstance((backends["tree"] or {}).get("return_model"), dict)


if __name__ == "__main__":
    unittest.main()
