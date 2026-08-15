"""ŷ 校准层：Isotonic PAV + tip/复盘对照（不进决策）。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class TestIsotonicPav(unittest.TestCase):
    def test_monotone_and_apply(self):
        from core.signal.score_calibration import apply_isotonic, isotonic_pav

        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        # 故意非单调的条件均值噪声：整体仍应被拉成非降
        ys = [0.0, 0.8, 0.5, 1.5, 2.0]
        kx, ky = isotonic_pav(xs, ys)
        self.assertGreaterEqual(len(kx), 2)
        for i in range(1, len(ky)):
            self.assertLessEqual(ky[i - 1] - 1e-9, ky[i])
        self.assertAlmostEqual(apply_isotonic(0.0, kx, ky), ky[0], places=5)
        self.assertAlmostEqual(apply_isotonic(4.0, kx, ky), ky[-1], places=5)
        mid = apply_isotonic(1.5, kx, ky)
        self.assertTrue(ky[0] - 1e-6 <= mid <= ky[-1] + 1e-6)

    def test_apply_calibration_no_heads_passthrough(self):
        from core.signal.score_calibration import apply_calibration

        self.assertEqual(
            apply_calibration(1.25, head="eod", model_doc={"enabled": False, "heads": {}}),
            1.25,
        )

    def test_apply_calibration_force(self):
        from core.signal.score_calibration import apply_calibration

        doc = {
            "enabled": False,
            "heads": {
                "eod": {
                    "knots_x": [0.0, 2.0],
                    "knots_y": [0.0, 1.0],
                }
            },
        }
        # ŷ=2 → g=1；有 knots 即映射（enabled 标志忽略）
        self.assertAlmostEqual(
            apply_calibration(2.0, head="eod", model_doc=doc),
            1.0,
            places=5,
        )
        self.assertAlmostEqual(
            apply_calibration(2.0, head="eod", model_doc=doc, force=True),
            1.0,
            places=5,
        )

    def test_fit_synthetic_pairs(self):
        from core.signal import score_calibration as sc

        # 构造：realized ≈ 0.4 * yhat（幅度偏大）
        pairs = []
        for d in range(20):
            day = f"2026-01-{d + 1:02d}"
            for i in range(5):
                y = -1.0 + 0.2 * i + 0.01 * d
                pairs.append(
                    {
                        "as_of": day,
                        "code": f"{i:06d}",
                        "yhat": y,
                        "realized": 0.4 * y,
                        "head": "eod",
                    }
                )

        def _fake_collect(*, lookback_dates=90, head="eod"):
            return [dict(p, head=head) for p in pairs]

        orig = sc.collect_calibration_pairs
        sc.collect_calibration_pairs = _fake_collect  # type: ignore
        try:
            report = sc.fit_score_calibration_report(
                lookback_dates=90, train_frac=0.7, heads=("eod",)
            )
        finally:
            sc.collect_calibration_pairs = orig  # type: ignore
        self.assertTrue(report.get("success"), report.get("error"))
        eod = (report.get("heads") or {}).get("eod") or {}
        self.assertTrue(eod.get("success"))
        self.assertGreaterEqual(len(eod.get("knots_x") or []), 2)
        ho = eod.get("holdout_metrics") or {}
        # 校准后 MAE 应不差于原始（允许数值贴边）
        if ho.get("mae_raw") is not None and ho.get("mae_cal") is not None:
            self.assertLessEqual(float(ho["mae_cal"]), float(ho["mae_raw"]) + 0.05)

    def test_rank_key_stays_raw_when_calibration_enabled(self):
        """方案 A：排序键始终 raw；*_cal 由 attach 写入供 tip。"""
        from core.signal.dual_score import rank_key_for_item
        from core.signal import score_calibration as sc

        item = {
            "predicted_score": 2.0,
            "predicted_score_eod": 2.0,
            "predicted_score_eod_rem": 1.5,
            "predicted_score_tau": 0.5,
            "predicted_score_blend": 1.0,
        }
        doc = {
            "enabled": True,
            "heads": {
                "eod": {"knots_x": [0.0, 3.0], "knots_y": [0.0, 1.5]},
                "tau": {"knots_x": [0.0, 2.0], "knots_y": [0.0, 1.0]},
            },
        }
        orig_load = sc.load_calibration_model
        orig_en = sc.calibration_enabled
        sc.load_calibration_model = lambda: doc  # type: ignore
        sc.calibration_enabled = lambda model_doc=None: True  # type: ignore
        try:
            key = rank_key_for_item(item)
            sc.attach_calibrated_scores(item, model_doc=doc, force=True)
        finally:
            sc.load_calibration_model = orig_load  # type: ignore
            sc.calibration_enabled = orig_en  # type: ignore
        self.assertAlmostEqual(float(key), 1.0, places=5)
        self.assertIn("predicted_score_blend_cal", item)

    def test_attach_marks_out_of_domain(self):
        """ŷ 低于 knots 左端时打 OOR，仍写端点 g。"""
        from core.signal import score_calibration as sc

        item = {
            "predicted_score_eod": -1.0,
            "predicted_score_eod_rem": -1.2,
            "predicted_score_tau": 0.1,
            "predicted_score_blend": -0.5,
        }
        doc = {
            "enabled": False,
            "heads": {
                "eod": {"knots_x": [0.0, 3.0], "knots_y": [0.0, 1.5]},
            },
        }
        sc.attach_calibrated_scores(item, model_doc=doc, force=True)
        self.assertTrue(item.get("score_calibration_eod_oor"))
        self.assertTrue(item.get("score_calibration_eod_rem_oor"))
        self.assertNotIn("score_calibration_tau_oor", item)
        self.assertAlmostEqual(item["predicted_score_cal"], 0.0, places=5)

    def test_blend_cal_keeps_raw_when_eod_rem_oor(self):
        """EOD_rem 域外时 blend 对照用 raw rem，避免多票撞同一端点。"""
        from core.signal import score_calibration as sc

        doc = {
            "enabled": True,
            "heads": {
                "eod": {"knots_x": [0.5, 3.0], "knots_y": [-2.0, 1.0]},
                "tau": {"knots_x": [0.0, 1.0], "knots_y": [-0.3, -0.1]},
            },
        }
        a = {
            "predicted_score_eod": 0.2,
            "predicted_score_eod_rem": -0.5,
            "predicted_score_tau": 0.2,
            "predicted_score_blend": -0.15,
        }
        b = {
            "predicted_score_eod": 0.1,
            "predicted_score_eod_rem": -1.5,
            "predicted_score_tau": 0.2,
            "predicted_score_blend": -0.65,
        }
        sc.attach_calibrated_scores(a, model_doc=doc, force=True)
        sc.attach_calibrated_scores(b, model_doc=doc, force=True)
        self.assertTrue(a.get("score_calibration_eod_rem_oor"))
        self.assertTrue(b.get("score_calibration_eod_rem_oor"))
        # 端点 g(rem) 相同，但 blend_cal 应随 raw rem 区分
        self.assertAlmostEqual(a["predicted_score_eod_rem_cal"], -2.0, places=5)
        self.assertAlmostEqual(b["predicted_score_eod_rem_cal"], -2.0, places=5)
        self.assertNotAlmostEqual(
            float(a["predicted_score_blend_cal"]),
            float(b["predicted_score_blend_cal"]),
            places=5,
        )
        self.assertEqual(a.get("score_calibration_partial"), "eod_rem_oor_raw")

    def test_attach_skips_identity_cal_when_head_missing(self):
        """缺 τ 头时不写恒等 predicted_score_tau_cal，避免校准列假对照。"""
        from core.signal import score_calibration as sc

        item = {
            "predicted_score_eod": 2.0,
            "predicted_score_eod_rem": 1.5,
            "predicted_score_tau": 0.5,
            "predicted_score_blend": 1.0,
            "predicted_score_tau_cal": 0.5,  # 陈旧恒等，应被清掉
        }
        doc = {
            "enabled": False,
            "heads": {
                "eod": {"knots_x": [0.0, 3.0], "knots_y": [0.0, 1.5]},
            },
        }
        sc.attach_calibrated_scores(item, model_doc=doc, force=True)
        self.assertIn("predicted_score_cal", item)
        self.assertIn("predicted_score_eod_rem_cal", item)
        self.assertIn("predicted_score_blend_cal", item)
        self.assertNotIn("predicted_score_tau_cal", item)
        self.assertFalse(item.get("score_calibration_applied"))
        self.assertTrue(item.get("score_calibration_enabled"))

    def test_attach_surfaces_soft_note(self):
        from core.signal import score_calibration as sc

        item = {
            "predicted_score_eod": 2.0,
            "predicted_score_eod_rem": 1.5,
            "predicted_score_tau": 0.5,
            "predicted_score_blend": 1.0,
        }
        doc = {
            "enabled": True,
            "dropped_heads_note": "eod 校准 g(门槛)偏低",
            "heads": {
                "eod": {"knots_x": [0.0, 3.0], "knots_y": [0.0, 1.5]},
            },
        }
        sc.attach_calibrated_scores(item, model_doc=doc, force=True)
        self.assertIn("偏低", item.get("score_calibration_note") or "")
        self.assertEqual(item.get("score_calibration_partial"), "tau_raw")
        self.assertIn("predicted_score_blend_cal", item)
        self.assertNotIn("predicted_score_tau_cal", item)

    def test_reconcile_migrates_enabled_flag(self):
        import json
        import tempfile
        from pathlib import Path
        from core.signal import score_calibration as sc

        with tempfile.TemporaryDirectory() as td:
            live_path = str(Path(td) / "score_calibration.json")
            doc = {
                "success": True,
                "enabled": False,
                "heads": {
                    "eod": {"knots_x": [0.0, 2.0], "knots_y": [0.0, 1.0]},
                },
            }
            Path(live_path).write_text(json.dumps(doc), encoding="utf-8")
            orig_model = sc.calibration_model_path
            orig_sync = sc.sync_enable_calibration_flag
            sc.calibration_model_path = lambda: live_path  # type: ignore
            sc.sync_enable_calibration_flag = lambda enable: False  # type: ignore
            try:
                out = sc.reconcile_calibration_switch()
            finally:
                sc.calibration_model_path = orig_model  # type: ignore
                sc.sync_enable_calibration_flag = orig_sync  # type: ignore
            self.assertTrue(out.get("enabled"))
            self.assertTrue(out.get("enabled_migrated"))
            saved = json.loads(Path(live_path).read_text(encoding="utf-8"))
            self.assertTrue(saved.get("enabled"))

    def test_tau_gate_uses_raw_not_calibrated(self):
        """方案 A：τ 闸比 raw；校准抬高也不能放行低于 floor 的 ŷ_τ。"""
        from core.signal.dual_score import buy_passes_tau_gate
        from core.signal import score_calibration as sc

        item = {"predicted_score_tau": 0.2}
        doc = {
            "enabled": True,
            "heads": {
                "tau": {"knots_x": [0.0, 1.0], "knots_y": [0.0, 4.0]},
            },
        }
        orig_load = sc.load_calibration_model
        orig_en = sc.calibration_enabled
        sc.load_calibration_model = lambda: doc  # type: ignore
        sc.calibration_enabled = lambda model_doc=None: True  # type: ignore
        try:
            ok, reason = buy_passes_tau_gate(
                item, config={"dual_score": {"min_predicted_score_tau": 0.5}}
            )
        finally:
            sc.load_calibration_model = orig_load  # type: ignore
            sc.calibration_enabled = orig_en  # type: ignore
        self.assertFalse(ok, reason)
        self.assertIn("ŷ_τ", reason or "")

    def test_persist_rewrites_live_from_existing(self):
        import tempfile
        from pathlib import Path
        from core.signal import score_calibration as sc

        live = {
            "success": True,
            "enabled": True,
            "lookback_dates": 90,
            "heads": {
                "eod": {
                    "method": "isotonic_pav",
                    "knots_x": [0.0, 2.0],
                    "knots_y": [0.0, 1.0],
                    "n": 10,
                    "holdout_metrics": {"mae_raw": 1.0, "mae_cal": 0.8},
                }
            },
        }
        with tempfile.TemporaryDirectory() as td:
            live_path = str(Path(td) / "score_calibration.json")
            orig_model = sc.calibration_model_path
            orig_load = sc.load_calibration_model
            orig_last = sc.load_calibration_last_report
            sc.calibration_model_path = lambda: live_path  # type: ignore
            sc.load_calibration_model = lambda: live  # type: ignore
            sc.load_calibration_last_report = lambda: None  # type: ignore
            try:
                out = sc.persist_score_calibration(note="test rewrite")
            finally:
                sc.calibration_model_path = orig_model  # type: ignore
                sc.load_calibration_model = orig_load  # type: ignore
                sc.load_calibration_last_report = orig_last  # type: ignore
            self.assertTrue(out.get("success"), out)
            self.assertTrue(out.get("enabled"))
            self.assertTrue(Path(live_path).is_file())

    def test_calibration_enabled_ignores_enabled_flag(self):
        """有 knots 即对照；enabled=False 仍 True。"""
        from core.signal.score_calibration import calibration_enabled

        doc = {
            "enabled": False,
            "heads": {
                "eod": {
                    "knots_x": [0.0, 2.0],
                    "knots_y": [0.0, 1.0],
                }
            },
        }
        self.assertTrue(calibration_enabled(model_doc=doc))
        self.assertFalse(
            calibration_enabled(model_doc={"enabled": True, "heads": {}})
        )

    def test_tau_gate_safe_rejects_all_negative_map(self):
        import tempfile
        from pathlib import Path
        from core.signal.score_calibration import (
            persist_score_calibration,
            tau_calibration_gate_safe,
        )

        heads = {
            "tau": {
                "knots_x": [-0.5, 1.0],
                "knots_y": [-0.4, -0.07],
            }
        }
        ok, err = tau_calibration_gate_safe(heads, floor=0.0)
        self.assertFalse(ok)
        self.assertIn("挡住全部入选", err)

        report = {
            "success": True,
            "lookback_dates": 90,
            "heads": {
                "tau": {
                    "success": True,
                    "method": "isotonic_pav",
                    "knots_x": [-0.5, 1.0],
                    "knots_y": [-0.4, -0.07],
                    "n": 10,
                }
            },
        }
        with tempfile.TemporaryDirectory() as td:
            live_path = str(Path(td) / "score_calibration.json")
            from core.signal import score_calibration as sc

            orig_model = sc.calibration_model_path
            sc.calibration_model_path = lambda: live_path  # type: ignore
            try:
                out = persist_score_calibration(report, enable=True, note="tip ok")
            finally:
                sc.calibration_model_path = orig_model  # type: ignore
            self.assertTrue(out.get("success"), out)
            self.assertTrue(out.get("enabled"))
            self.assertIn("挡住全部入选", out.get("dropped_heads_note") or "")

    def test_eod_floor_safe_blocks_all_negative_promote(self):
        import tempfile
        from pathlib import Path
        from core.signal.score_calibration import (
            eod_calibration_floor_safe,
            persist_score_calibration,
        )

        heads = {
            "eod": {
                "knots_x": [0.0, 2.0],
                "knots_y": [-0.4, -0.01],
            }
        }
        ok, err = eod_calibration_floor_safe(heads, floor=0.35)
        self.assertFalse(ok)
        self.assertIn("挡住全部入选", err)

        report = {
            "success": True,
            "lookback_dates": 90,
            "heads": {
                "eod": {
                    "success": True,
                    "method": "isotonic_pav",
                    "knots_x": [0.0, 2.0],
                    "knots_y": [-0.4, -0.01],
                    "n": 10,
                }
            },
        }
        with tempfile.TemporaryDirectory() as td:
            live_path = str(Path(td) / "score_calibration.json")
            from core.signal import score_calibration as sc

            orig_model = sc.calibration_model_path
            orig_eod = sc._resolve_eod_floor
            sc.calibration_model_path = lambda: live_path  # type: ignore
            sc._resolve_eod_floor = lambda: 0.35  # type: ignore
            try:
                out = persist_score_calibration(report, enable=True, note="tip ok eod")
            finally:
                sc.calibration_model_path = orig_model  # type: ignore
                sc._resolve_eod_floor = orig_eod  # type: ignore
            self.assertTrue(out.get("success"), out)
            self.assertTrue(out.get("enabled"))
            self.assertIn("挡住全部入选", out.get("dropped_heads_note") or "")

    def test_collect_skips_heuristic_score_scale(self):
        from core.signal import score_calibration as sc

        fake_rows = [
            {
                "as_of": "2026-08-12",
                "code": "600519",
                "yhat": 80.0,  # heuristic 0–100，不得入模
                "yhat_eod": None,
            },
            {
                "as_of": "2026-08-12",
                "code": "000001",
                "yhat_eod": 1.25,
            },
            {
                "as_of": "2026-08-12",
                "code": "601318",
                "yhat_eod": 90.0,  # 异常大，过滤
            },
        ]

        def _fake_dates(limit=90):
            return ["2026-08-12"]

        def _fake_ledger(d):
            return {"success": True, "rows": fake_rows}

        def _fake_outcomes(d):
            return {
                "success": True,
                "by_code": {
                    "600519": {"realized_h": 0.5},
                    "000001": {"realized_h": 0.2},
                    "601318": {"realized_h": 0.1},
                },
            }

        import core.score_ledger as led

        orig_dates = led.list_ledger_dates
        orig_led = led.load_ledger
        orig_oc = led.load_outcomes
        led.list_ledger_dates = _fake_dates  # type: ignore
        led.load_ledger = _fake_ledger  # type: ignore
        led.load_outcomes = _fake_outcomes  # type: ignore
        try:
            pairs = sc.collect_calibration_pairs(lookback_dates=10, head="eod")
        finally:
            led.list_ledger_dates = orig_dates  # type: ignore
            led.load_ledger = orig_led  # type: ignore
            led.load_outcomes = orig_oc  # type: ignore
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["code"], "000001")
        self.assertAlmostEqual(pairs[0]["yhat"], 1.25)

    def test_calibration_enabled_true_even_when_map_unsafe(self):
        """有有效 knots → tip 开；门槛安全不挡。"""
        from core.signal.score_calibration import calibration_enabled

        doc = {
            "enabled": False,
            "heads": {
                "eod": {
                    "knots_x": [0.0, 2.0],
                    "knots_y": [-0.4, -0.01],
                }
            },
        }
        from core.signal import score_calibration as sc

        orig = sc._resolve_eod_floor
        sc._resolve_eod_floor = lambda: 0.35  # type: ignore
        try:
            self.assertTrue(calibration_enabled(model_doc=doc))
            ok, _ = sc.eod_calibration_floor_safe(doc["heads"], floor=0.35)
            self.assertFalse(ok)
        finally:
            sc._resolve_eod_floor = orig  # type: ignore

    def test_disable_unsafe_does_not_mutate(self):
        from core.signal.score_calibration import disable_unsafe_calibration

        out = disable_unsafe_calibration()
        self.assertTrue(out.get("success"))
        self.assertFalse(out.get("changed"))

    def test_eod_floor_safe_rejects_when_g_at_floor_below(self):
        """max g 过门槛但 g(门槛) 仍低 → 软警告（若进决策 Top-K 易 0 笔）。"""
        from core.signal.score_calibration import eod_calibration_floor_safe

        heads = {
            "eod": {
                # 仅极端高 ŷ 才到正 g；门槛 0.35 处仍为负
                "knots_x": [0.0, 0.35, 1.0, 3.0],
                "knots_y": [-2.0, -2.0, -0.5, 1.5],
            }
        }
        ok, err = eod_calibration_floor_safe(heads, floor=0.35)
        self.assertFalse(ok)
        self.assertIn("门槛", err)

    def test_eod_floor_safe_ok_when_g_at_floor_clears(self):
        from core.signal.score_calibration import eod_calibration_floor_safe

        heads = {
            "eod": {
                "knots_x": [0.0, 0.35, 1.0],
                "knots_y": [0.0, 0.4, 1.0],
            }
        }
        ok, err = eod_calibration_floor_safe(heads, floor=0.35)
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_sync_enable_calibration_flag(self):
        import json
        import tempfile
        from pathlib import Path

        from core.signal.score_calibration import sync_enable_calibration_flag

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "signal_config.json"
            cfg.write_text(
                json.dumps({"scoring": {"enable_calibration": False}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"INVESTMENT_SIGNAL_CONFIG": str(cfg)}):
                changed = sync_enable_calibration_flag(True)
                self.assertTrue(changed)
                raw = json.loads(cfg.read_text(encoding="utf-8"))
                self.assertTrue(raw["scoring"]["enable_calibration"])
                self.assertFalse(sync_enable_calibration_flag(True))


if __name__ == "__main__":
    unittest.main()
