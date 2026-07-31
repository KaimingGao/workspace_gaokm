"""P94：QuantService Mixin 门面 + Web routers 拆分（行为不变）。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP94QuantServiceMixins(unittest.TestCase):
    def test_facade_exposes_domain_methods(self):
        from quant.services.quant_service import QuantService
        from quant.services.quant_service_config import QuantConfigMixin
        from quant.services.quant_service_factors import QuantFactorMixin
        from quant.services.quant_service_ops import QuantOpsMixin
        from quant.services.quant_service_portfolio import QuantPortfolioMixin

        self.assertTrue(issubclass(QuantService, QuantConfigMixin))
        self.assertTrue(issubclass(QuantService, QuantFactorMixin))
        self.assertTrue(issubclass(QuantService, QuantPortfolioMixin))
        self.assertTrue(issubclass(QuantService, QuantOpsMixin))
        qs = QuantService()
        for name in (
            "config_summary",
            "run_factor_experiment",
            "run_portfolio_backtest",
            "run_t0_backtest",
            "build_daily_report",
            "load_last_daily",
        ):
            self.assertTrue(callable(getattr(qs, name)), name)

    def test_web_routers_mounted(self):
        try:
            from fastapi.testclient import TestClient
            from web.app import app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(app)
        paths = set(app.openapi().get("paths") or {})
        for path in (
            "/api/health",
            "/api/chat",
            "/api/paper",
            "/api/quant/config",
            "/api/quant/t0-backtest",
            "/api/daily/presets",
            "/api/watching",
            "/api/evals/cases",
        ):
            self.assertIn(path, paths)

        res = client.get("/api/quant/config")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))

    def test_docs_mark_upgrade_as_archive(self):
        path = os.path.join(ROOT, "docs", "quant-upgrade.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("历史归档", text)
        self.assertIn("P94", text)


if __name__ == "__main__":
    unittest.main()
