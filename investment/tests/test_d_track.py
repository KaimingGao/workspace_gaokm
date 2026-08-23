"""D 轨 · 数据层深化补强（D0–D4）主干验收。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestD0AnnIngest(unittest.TestCase):
    def test_merge_writes_ann_date(self):
        from core.fundamentals_pit import merge_history_point, select_point_as_of

        hist = merge_history_point(
            [],
            as_of="2024-03-31",
            metrics={"pe": 10},
            ann_date="2024-04-20",
        )
        self.assertEqual(hist[0].get("ann_date"), "2024-04-20")
        self.assertEqual(hist[0].get("available_as_of"), "2024-04-20")
        chosen, meta = select_point_as_of(hist, "2024-04-10")
        self.assertIsNone(chosen)
        chosen, meta = select_point_as_of(hist, "2024-04-21")
        self.assertIsNotNone(chosen)

    def test_pick_financial_row_ann(self):
        from skills.fundamentals.engine import _pick_financial_row

        row = {
            "日期": "2024-06-30",
            "公告日期": "2024-08-15",
            "净资产收益率(%)": 12.0,
            "净利润增长率(%)": 5.0,
        }
        parsed = _pick_financial_row(row)
        self.assertEqual(parsed.get("ann_date"), "2024-08-15")
        self.assertNotIn("ann_missing", parsed)


class TestD1SourceAuditDefault(unittest.TestCase):
    def test_attach_source_audit_shape(self):
        from core.data.consistency import attach_source_audit

        with patch(
            "core.data.consistency.audit_code_sources",
            return_value={"ok": True, "status": "ok", "codes": ["600519"]},
        ):
            out = attach_source_audit({"success": True}, codes=["600519"])
        self.assertIn("source_audit", out)
        self.assertEqual(out["source_audit"]["status"], "ok")


class TestD2Adjust(unittest.TestCase):
    def test_normalize_adjust(self):
        from core.data.facade import normalize_adjust_policy

        self.assertEqual(normalize_adjust_policy("raw"), "raw")
        self.assertEqual(normalize_adjust_policy("none"), "raw")
        self.assertEqual(normalize_adjust_policy("hfq"), "hfq")
        self.assertEqual(normalize_adjust_policy(None), "qfq")

    def test_get_bars_passes_adjust(self):
        from core.data import facade as ds

        with patch(
            "core.ports.market.fetch_daily_bars",
            return_value=([], "empty"),
        ) as m:
            pack = ds.get_bars("600519", limit=5, adjust="raw", use_cache=False)
        self.assertEqual(pack.get("adjust_policy"), "raw")
        kwargs = m.call_args.kwargs if m.call_args else {}
        self.assertEqual(kwargs.get("adjust"), "raw")


class TestD3Calendar(unittest.TestCase):
    def test_weekend_not_trading(self):
        from core.market.calendar import filter_trading_dates, is_trading_day

        self.assertFalse(is_trading_day("2024-01-06"))  # Sat
        self.assertTrue(is_trading_day("2024-01-08", holidays=set()))  # Mon
        out = filter_trading_dates(["2024-01-06", "2024-01-08", "2024-01-09"])
        self.assertEqual(out, ["2024-01-08", "2024-01-09"])


class TestD4DataQuality(unittest.TestCase):
    def test_build_report(self):
        from core.data.quality_center import build_data_quality_report

        with patch(
            "core.data.coverage.build_data_coverage",
            return_value={"ok": True, "mapped": 1},
        ), patch(
            "core.sample_ops.fundamentals_history_coverage",
            return_value={"real_multi_coverage": 0.6, "real_multi_point": 3},
        ), patch(
            "core.sample_ops.sample_status",
            return_value={"discipline": {"warnings": []}},
        ), patch(
            "core.data.consistency.audit_code_sources",
            return_value={"ok": True, "status": "ok", "fallback_codes": [], "thin_codes": []},
        ):
            out = build_data_quality_report(codes=["600519"], include_source_audit=True)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("kind"), "data_quality_center")
        self.assertIn("bars_coverage", out)
        self.assertIn("calendar", out)

    def test_api_route(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        with patch.object(
            web_app.deps.platform,
            "get_data_quality",
            return_value={"ok": True, "status": "ok", "kind": "data_quality_center"},
        ):
            res = client.get("/api/ops/data-quality")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("ok"))

    def test_panel_has_button(self):
        path = os.path.join(ROOT, "web/static/partials/platform_panel.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("data-quality-refresh", html)
        self.assertIn("data-quality-fold", html)


if __name__ == "__main__":
    unittest.main()
