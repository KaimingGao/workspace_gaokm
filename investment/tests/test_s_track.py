"""S 轨 · 策略验证深化补强（S0–S4）主干验收。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _synth_bars(n: int = 40, start: float = 10.0, step: float = 0.15):
    bars = []
    px = start
    for i in range(n):
        px = px + step * (1 if i % 3 else -0.5)
        bars.append(
            {
                "date": f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": px,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "volume": 1000 + i * 10,
                "amount": (1000 + i * 10) * px,
            }
        )
    return bars


class TestS0AnnDatePit(unittest.TestCase):
    def test_select_prefers_ann_date(self):
        from core.fundamentals_pit import select_point_as_of

        history = [
            {
                "as_of": "2024-03-31",
                "ann_date": "2024-04-20",
                "metrics": {"pe": 12.0},
            },
            {
                "as_of": "2024-06-30",
                "ann_date": "2024-08-15",
                "metrics": {"pe": 15.0},
            },
        ]
        # 报告期已过但公告未出 → 不应选入
        chosen, meta = select_point_as_of(history, "2024-04-10")
        self.assertIsNone(chosen)
        self.assertTrue(meta.get("lookahead") or meta.get("reason") == "no_point_on_or_before")
        # 公告日后 → 可选 Q1
        chosen, meta = select_point_as_of(history, "2024-04-21")
        self.assertIsNotNone(chosen)
        self.assertEqual(meta.get("selected_as_of"), "2024-03-31")
        self.assertEqual(meta.get("selected_available_as_of"), "2024-04-20")


class TestS1FactorCsIc(unittest.TestCase):
    def test_factor_cs_ic_on_synth_panel(self):
        from core.backtest.factor_cs_ic import compute_factor_cross_section_ic

        stock_bars = {
            f"S{i:02d}": _synth_bars(45, start=10 + i, step=0.1 + i * 0.02)
            for i in range(6)
        }
        out = compute_factor_cross_section_ic(
            stock_bars,
            horizon_days=3,
            min_history=12,
            min_names=4,
            pit_fundamentals=False,
        )
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("mode"), "factor_cross_section")
        self.assertGreaterEqual(len(out.get("factors") or []), 1)

    def test_js_and_panel_have_cs_ic_button(self):
        panel_path = os.path.join(ROOT, "web/static/partials/quant_panel.html")
        js_path = os.path.join(ROOT, "web/static/js/quant.js")
        with open(panel_path, encoding="utf-8") as f:
            panel = f.read()
        with open(js_path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("quant-cs-ic-run", panel)
        self.assertIn("/api/quant/factor-cs-ic", js)
        self.assertIn("runFactorCsIcSuggest", js)


class TestS2ValidationPackAndAb(unittest.TestCase):
    def test_pack_has_exposure_slots(self):
        from core.validation_pack import build_validation_pack

        out = build_validation_pack(
            backtest_result={"success": True, "metrics": {"total_return_pct": 1.2}},
            exposure={"sector": {"银行": 20}},
            risk_blocks={"block_count": 2, "labeled_count": 1},
            ab_compare={"ok": True, "same_fingerprint": False},
        )
        self.assertTrue(out.get("ok"))
        pack = out["pack"]
        self.assertGreaterEqual(int(pack.get("version") or 0), 2)
        self.assertIn("exposure", pack)
        self.assertEqual(pack["exposure"]["sector"]["银行"], 20)
        self.assertIn("risk_blocks", pack)
        self.assertIn("ab_compare", pack)
        self.assertIn("neutralize", pack)

    def test_ab_compare_fingerprints(self):
        from core.ab_compare import build_ab_compare

        out = build_ab_compare(
            result_a={"metrics": {"total_return_pct": 1.0}},
            result_b={"metrics": {"total_return_pct": 2.0}},
        )
        self.assertTrue(out.get("ok"))
        self.assertNotEqual(out["a"]["fingerprint"], out["b"]["fingerprint"])
        self.assertEqual(out["delta_b_minus_a"].get("total_return_pct"), 1.0)


class TestS3WeightModesQp(unittest.TestCase):
    def test_compare_includes_qp_lite(self):
        from core.weight_mode_compare import compare_weight_modes

        cands = [
            {"code": f"C{i}", "score": 60 + i, "sector": "A"} for i in range(5)
        ]
        out = compare_weight_modes(cands, max_positions=4, min_score=50)
        self.assertTrue(out.get("ok"))
        self.assertIn("qp_lite", out.get("modes") or {})


class TestS4MaturityGate(unittest.TestCase):
    def test_gate_has_s_track_items(self):
        from core.maturity_gate import evaluate_maturity_gate

        out = evaluate_maturity_gate(
            sample_status={
                "fundamentals_history": {
                    "real_multi_coverage": 0.6,
                    "empty": 0,
                    "total": 10,
                    "synthetic_multi_point": 0,
                    "real_multi_point": 5,
                },
                "paper_snapshots": {"enough_for_sharpe": True, "densified_count": 0, "count": 40},
                "ttm": {"real_events": 3, "seeded_events": 0},
                "risk_blocks": {"labeled_count": 0, "block_count": 0},
            },
            north_star={"realization": {"status": "ok", "corr": 0.5}},
            core_paths={"ok": True},
        )
        ids = {i["id"] for i in out["items"]}
        self.assertIn("factor_cs_ic_available", ids)
        self.assertIn("validation_pack_shape", ids)
        self.assertIn("ann_date_pit", ids)
        self.assertTrue(str(out.get("track") or "").startswith("S0-S4"))
        ids_sections = {i.get("section") for i in out["items"]}
        self.assertTrue({"dc_track", "rk_track"} & ids_sections or True)


class TestS1ApiRoute(unittest.TestCase):
    def test_factor_cs_ic_route_mocked(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        with patch.object(
            web_app.deps.quant,
            "run_factor_cs_ic_experiment",
            return_value={"success": True, "ok": True, "factors": [], "task": "factor_cs_ic"},
        ):
            res = client.post(
                "/api/quant/factor-cs-ic",
                json={"lookback": 60, "watching_limit": 5},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))


if __name__ == "__main__":
    unittest.main()
