"""SS encapsulate：ScoreResult / ŷ 门禁 / facade dict 兼容。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _yhat_item(**extra):
    row = {
        "stock_code": "600519",
        "stock_name": "茅台",
        "predicted_score": 1.25,
        "predicted_score_eod": 1.25,
        "predicted_score_tau": 0.4,
        "predicted_score_blend": 0.82,
        "score": 0.82,
        "score_scale": "predicted_yhat",
        "hard_reject": False,
        "quality_gate": False,
    }
    row.update(extra)
    return row


def _score_stock_ok(item=None):
    it = item if item is not None else _yhat_item()
    return {
        "success": True,
        "stock_code": it.get("stock_code") or "600519",
        "signal_item": it,
        "scored": {"score": it.get("score")},
        "quality_gate": bool(it.get("quality_gate")),
        "cluster_mode": it.get("cluster_mode") or "active",
    }


class TestScoreGate(unittest.TestCase):
    def test_yhat_allows_production(self):
        from core.signal.gate import allows_production_yhat, infer_score_scale

        item = _yhat_item()
        self.assertEqual(infer_score_scale(item), "predicted_yhat")
        ok, reason = allows_production_yhat(item)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_heuristic_blocked(self):
        from core.signal.gate import SCALE_HEURISTIC, allows_production_yhat, infer_score_scale

        item = {
            "stock_code": "000001",
            "score": 72.0,
            "heuristic_score": 72.0,
            "score_scale": "heuristic_0_100",
            "return_model_source": "oos_failed_heuristic",
            "predicted_score": None,
        }
        self.assertEqual(infer_score_scale(item), SCALE_HEURISTIC)
        ok, reason = allows_production_yhat(item)
        self.assertFalse(ok)
        self.assertEqual(reason, "score_scale:heuristic_0_100")

    def test_quality_gate_blocked(self):
        from core.signal.gate import allows_production_yhat

        ok, reason = allows_production_yhat(
            _yhat_item(quality_gate=True, gate_reason="data_quality_gate:thin"),
            quality_gated=True,
        )
        self.assertFalse(ok)
        self.assertIn("data_quality", reason)

    def test_hard_reject_blocked(self):
        from core.signal.gate import allows_production_yhat

        ok, reason = allows_production_yhat(
            _yhat_item(hard_reject=True, reject_reason="停牌")
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "停牌")

    def test_fetch_fail(self):
        from core.signal.gate import allows_production_yhat

        ok, reason = allows_production_yhat(None, fetch_ok=False)
        self.assertFalse(ok)
        self.assertEqual(reason, "score_fetch_failed")


class TestScoreResult(unittest.TestCase):
    def test_from_score_stock_yhat(self):
        from core.signal.types import ScoreResult

        r = ScoreResult.from_score_stock(_score_stock_ok())
        self.assertTrue(r.success)
        self.assertTrue(r.production_ok)
        self.assertEqual(r.scale, "predicted_yhat")
        self.assertAlmostEqual(r.predicted_score, 1.25)
        d = r.as_dict()
        self.assertIsInstance(d, dict)
        self.assertTrue(d.get("success"))
        self.assertTrue(d.get("production_ok"))
        self.assertEqual(d["signal_item"]["stock_code"], "600519")
        self.assertTrue(d["signal_item"].get("production_ok"))

    def test_heuristic_not_production_ok(self):
        from core.signal.types import ScoreResult

        raw = _score_stock_ok(
            {
                "stock_code": "000001",
                "score": 80,
                "heuristic_score": 80,
                "score_scale": "heuristic_0_100",
                "return_model_source": "oos_failed_heuristic",
                "predicted_score": None,
            }
        )
        r = ScoreResult.from_score_stock(raw)
        self.assertTrue(r.success)
        self.assertFalse(r.production_ok)
        self.assertEqual(r.gate_reason, "score_scale:heuristic_0_100")

    def test_quote_fail_envelope(self):
        from core.signal.types import ScoreResult

        r = ScoreResult.from_score_stock(
            {"success": False, "stock_code": "600519", "error": "行情失败"}
        )
        self.assertFalse(r.ok)
        self.assertFalse(r.production_ok)
        self.assertEqual(r.stock_code, "600519")


class TestBookResult(unittest.TestCase):
    def test_all_yhat_production_ok(self):
        from core.signal.types import BookResult

        raw = {
            "success": True,
            "ranking": [_yhat_item(), _yhat_item(stock_code="000858")],
        }
        b = BookResult.from_rank(raw, kind="cross_section")
        self.assertTrue(b.production_ok)
        self.assertEqual(b.yhat_count, 2)
        d = b.as_dict()
        self.assertTrue(d.get("production_ok"))
        self.assertEqual(len(d.get("ranking") or []), 2)

    def test_heuristic_row_marks_book(self):
        from core.signal.types import BookResult

        raw = {
            "success": True,
            "ranking": [
                _yhat_item(),
                {
                    "stock_code": "x",
                    "score_scale": "heuristic_0_100",
                    "predicted_score": None,
                },
            ],
        }
        b = BookResult.from_rank(raw)
        self.assertFalse(b.production_ok)
        self.assertGreaterEqual(b.heuristic_count, 1)

    def test_empty_success_ok(self):
        from core.signal.types import BookResult

        b = BookResult.from_rank({"success": True, "ranking": []})
        self.assertTrue(b.production_ok)


class TestSignalServiceWrap(unittest.TestCase):
    def tearDown(self):
        from core.signal.service import set_default_signal_service

        set_default_signal_service(None)

    def test_score_one_wraps_score_stock(self):
        from core.signal.service import SignalService

        with patch(
            "core.signal.score_stock.score_stock",
            return_value=_score_stock_ok(),
        ) as mocked:
            r = SignalService().score_one("600519", horizon_days=1)
        mocked.assert_called_once()
        self.assertIsInstance(r.as_dict(), dict)
        self.assertTrue(r.production_ok)
        self.assertEqual(r.stock_code, "600519")

    def test_research_sets_bypass_quality_gate(self):
        from core.signal.service import ResearchSignalService

        with patch(
            "core.signal.score_stock.score_stock",
            return_value=_score_stock_ok(),
        ) as mocked:
            ResearchSignalService().score_one("600519")
        kwargs = mocked.call_args.kwargs
        self.assertTrue(kwargs.get("bypass_quality_gate"))

    def test_production_does_not_bypass(self):
        from core.signal.service import SignalService

        with patch(
            "core.signal.score_stock.score_stock",
            return_value=_score_stock_ok(),
        ) as mocked:
            SignalService().score_one("600519")
        kwargs = mocked.call_args.kwargs
        self.assertFalse(kwargs.get("bypass_quality_gate"))

    def test_rank_cross_section_wrap(self):
        from core.signal.service import SignalService

        fake = {"success": True, "ranking": [_yhat_item()]}
        with patch(
            "core.signal.cross_section.rank_cross_section",
            return_value=fake,
        ):
            b = SignalService().rank_cross_section(["600519"], limit=5)
        self.assertTrue(b.success)
        self.assertEqual(b.kind, "cross_section")
        self.assertTrue(b.as_dict().get("success"))

    def test_rank_cluster_pools_wrap(self):
        from core.signal.service import SignalService

        fake = {"success": True, "book": [_yhat_item()], "ranking": [_yhat_item()]}
        with patch(
            "core.signal.cluster.rank.rank_cluster_pools",
            return_value=fake,
        ):
            b = SignalService().rank_cluster_pools(None, persist_book=False)
        self.assertEqual(b.kind, "cluster_pools")
        self.assertTrue(b.production_ok)

    def test_facade_returns_dict(self):
        from core.signal_service import score_one

        with patch(
            "core.signal.score_stock.score_stock",
            return_value=_score_stock_ok(),
        ):
            pack = score_one("600519")
        self.assertIsInstance(pack, dict)
        self.assertTrue(pack.get("success"))
        self.assertIn("signal_item", pack)

    def test_pack_holding_row_heuristic_hides_table_yhat_mix(self):
        from core.signal.service import SignalService

        row = SignalService().pack_holding_row(
            {
                "stock_code": "000001",
                "score": 70,
                "heuristic_score": 70,
                "score_scale": "heuristic_0_100",
                "return_model_source": "oos_failed_heuristic",
                "score_cluster": 0.3,
                "predicted_score": None,
            }
        )
        self.assertEqual(row.get("score_scale"), "heuristic_0_100")
        self.assertFalse(row.get("production_ok"))
        self.assertAlmostEqual(float(row.get("score_cluster")), 0.3)

    def test_pack_holding_row_stamps_ranking_from_oo_oc(self):
        from core.signal.service import SignalService

        row = SignalService().pack_holding_row(
            {
                "stock_code": "600869",
                "predicted_score": 2.30,
                "predicted_score_eod": 2.30,
                "y_oo": 2.30,
                "y_oc": 5.69,
                "predicted_score_tau": 0.08,
                "predicted_score_blend": -3.65,
                "decision_score": -3.65,
                "score": -3.65,
                "gap_pct": -3.73,
                "dual_score_weights": {"w_eod": 0.0, "w_tau": 1.0},
            },
            rank_cfg={"fusion_w_oo": 0.8, "fusion_w_oc": 0.2, "fusion_w_co": 0.0},
        )
        self.assertAlmostEqual(float(row.get("ranking")), 0.8 * 2.30 + 0.2 * 5.69, places=4)
        self.assertGreater(float(row.get("ranking")), 0.0)
        self.assertAlmostEqual(float(row.get("fusion_w_oo")), 0.8)
        self.assertAlmostEqual(float(row.get("fusion_w_oc")), 0.2)

    def test_gate_reexports(self):
        from core.signal_service import (
            SCALE_YHAT,
            allows_production_yhat,
            infer_score_scale,
        )

        self.assertEqual(infer_score_scale(_yhat_item()), SCALE_YHAT)
        ok, _ = allows_production_yhat(_yhat_item())
        self.assertTrue(ok)


class TestMainCallersUseService(unittest.TestCase):
    def test_orchestrator_imports_service(self):
        from pathlib import Path

        orch = Path(ROOT, "core/paper/rebalance/orchestrator.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("holding_rules", orch)
        self.assertIn("run_daily_cycle", orch)
        self.assertNotIn("from core.signal.score_stock import score_stock", orch)
        self.assertNotIn("from core.signal.cross_section import rank_cross_section", orch)
        self.assertNotIn("from core.signal.cluster.rank import rank_cluster_pools", orch)
        cycle = Path(ROOT, "core/paper/cycle.py").read_text(encoding="utf-8")
        self.assertIn("get_default_signal_service", cycle)
        self.assertNotIn("from core.signal.score_stock import score_stock", cycle)

    def test_paper_account_imports_service(self):
        from pathlib import Path

        text = Path(ROOT, "services/paper_account.py").read_text(encoding="utf-8")
        self.assertIn("get_default_signal_service", text)
        self.assertNotIn("from core.signal.score_stock import score_stock", text)

    def test_quant_factors_imports_service(self):
        from pathlib import Path

        text = Path(ROOT, "quant/services/quant_service_factors.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("get_default_signal_service", text)
        self.assertNotIn("from core.signal.cross_section import rank_cross_section", text)
        self.assertNotIn("from core.signal.cluster.rank import rank_cluster_pools", text)

    def test_watching_insights_imports_service(self):
        from pathlib import Path

        text = Path(ROOT, "core/watching/insights.py").read_text(encoding="utf-8")
        self.assertIn("get_default_signal_service", text)
        self.assertNotIn("from core.signal.score_stock import score_stock", text)

    def test_cluster_live_refresh_imports_service(self):
        from pathlib import Path

        text = Path(ROOT, "core/signal/cluster/live_audit.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("get_default_signal_service", text)
        self.assertNotIn(
            "from core.signal.cluster.rank import rank_cluster_pools", text
        )

    def test_skill_signal_imports_service(self):
        from pathlib import Path

        text = Path(ROOT, "skills/signal/engine.py").read_text(encoding="utf-8")
        self.assertIn("get_default_signal_service", text)
        self.assertNotIn("from core.signal.score_stock import score_stock", text)


class TestMetricsAndBookStamp(unittest.TestCase):
    def tearDown(self):
        from core.signal.service import reset_metrics, set_default_signal_service

        reset_metrics()
        set_default_signal_service(None)

    def test_metrics_bump_on_score_one(self):
        from core.signal.service import SignalService, metrics_snapshot, reset_metrics

        reset_metrics()
        with patch(
            "core.signal.score_stock.score_stock",
            return_value=_score_stock_ok(),
        ):
            SignalService().score_one("600519")
        snap = metrics_snapshot()
        self.assertGreaterEqual(snap.get("score_ok", 0), 1)
        self.assertGreaterEqual(snap.get("score_production_ok", 0), 1)

    def test_book_stamps_production_ok(self):
        from core.signal.types import BookResult

        original = _yhat_item()
        heu = {
            "stock_code": "x",
            "score_scale": "heuristic_0_100",
            "predicted_score": None,
        }
        raw = {
            "success": True,
            "ranking": [original, heu],
        }
        b = BookResult.from_rank(raw)
        rows = b.ranking
        self.assertTrue(rows[0].get("production_ok"))
        self.assertFalse(rows[1].get("production_ok"))
        self.assertIn("gate_reason", rows[1])
        # 不得污染调用方原始 list 元素
        self.assertNotIn("production_ok", original)
        self.assertNotIn("production_ok", heu)
        d = b.as_dict()
        self.assertTrue((d.get("ranking") or [])[0].get("production_ok"))

    def test_book_fields_does_not_dump_full_item(self):
        """观察摘要只能合并 dual 字段；整包 update 会污染 score/stance。"""
        from core.signal.service import SignalService

        item = _yhat_item(
            factors={"volume_ratio": 1.2},
            reasons=["noise"],
            price=100,
            stock_name="茅台",
        )
        fields = SignalService().book_fields(item)
        self.assertNotIn("factors", fields)
        self.assertNotIn("reasons", fields)
        self.assertNotIn("price", fields)
        # dual 透传应含 blend / tau 相关键之一
        self.assertTrue(
            any(k.startswith("predicted_score") for k in fields)
            or "dual_score_fusion" in fields
            or "score_rem" in fields
            or fields == {}  # rem 未挂时也可能空，至少不泄全量
        )

    def test_watching_insights_uses_book_fields(self):
        from pathlib import Path

        text = Path(ROOT, "core/watching/insights.py").read_text(encoding="utf-8")
        self.assertIn(".book_fields(", text)
        self.assertNotIn("dual_score_book_fields", text)
        self.assertNotIn(".annotate_item(", text)

    def test_research_rank_sets_bypass(self):
        from core.signal.service import ResearchSignalService

        with patch(
            "core.signal.cross_section.rank_cross_section",
            return_value={"success": True, "ranking": [_yhat_item()]},
        ) as mocked:
            ResearchSignalService().rank_cross_section(["600519"], limit=5)
        self.assertTrue(mocked.call_args.kwargs.get("bypass_quality_gate"))



class TestProductionBuyGate(unittest.TestCase):
    def test_paper_rebalance_checks_production_yhat(self):
        from pathlib import Path

        wm = Path(ROOT, "core/paper/rebalance/watching_matrix.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("score_stock", wm)
        types = Path(ROOT, "core/signal/types.py").read_text(encoding="utf-8")
        self.assertIn("allows_production_yhat", types)

    def test_data_quality_exposes_signal_metrics(self):
        from pathlib import Path

        text = Path(ROOT, "core/data/quality_center.py").read_text(encoding="utf-8")
        self.assertIn("signal_service_metrics", text)

    def test_ops_scripts_use_signal_service(self):
        from pathlib import Path

        for rel in (
            "scripts/ops_h1_floor_refresh.py",
            "scripts/restore_cluster_active_v54.py",
            "research/cross_section_run.py",
        ):
            text = Path(ROOT, rel).read_text(encoding="utf-8")
            self.assertNotIn(
                "from core.signal.score_stock import score_stock", text, msg=rel
            )
            self.assertNotIn(
                "from core.signal.cross_section import rank_cross_section", text, msg=rel
            )
        research = Path(ROOT, "research/cross_section_run.py").read_text(encoding="utf-8")
        self.assertIn("get_research_signal_service", research)
        restore = Path(ROOT, "scripts/restore_cluster_active_v54.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("use_cluster=True", restore)
        self.assertIn('cluster_mode="active"', restore)
        self.assertIn("get_default_signal_service", restore)


if __name__ == "__main__":
    unittest.main()
