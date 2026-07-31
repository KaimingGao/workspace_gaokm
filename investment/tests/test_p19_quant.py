import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.config import read_signal_config_file
from quant.ops.daily_presets import list_daily_presets, resolve_daily_preset
from quant.services.quant_service import QuantService


class TestSignalConfigRead(unittest.TestCase):
    def test_read_signal_config_file(self):
        out = read_signal_config_file(reload=True)
        self.assertTrue(out["success"])
        self.assertIn("weights", out["config"])
        self.assertTrue(out["readonly"])

    def test_quant_service_read(self):
        out = QuantService().read_signal_config_file()
        self.assertTrue(out["success"])
        self.assertIn("stance_thresholds", out["config"])


class TestQuantPaperPreset(unittest.TestCase):
    def test_quant_paper_preset(self):
        names = {p["name"] for p in list_daily_presets()}
        self.assertIn("quant_paper", names)
        out = resolve_daily_preset("quant_paper")
        flags = out["flags"]
        self.assertTrue(flags["quant_report"])
        self.assertTrue(flags["paper_rebalance"])
        self.assertTrue(flags["export_quant_report"])


class TestSignalConfigApi(unittest.TestCase):
    def test_api_signal_config(self):
        from fastapi.testclient import TestClient

        import web.app as web_app

        client = TestClient(web_app.app)
        res = client.get("/api/signal/config/file")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("readonly"))
        self.assertIn("config", data)


class TestQuantPaperCli(unittest.TestCase):
    def test_cli_preset_quant_paper_resolves(self):
        from quant.ops.daily_presets import resolve_daily_preset

        out = resolve_daily_preset("quant_paper")
        self.assertEqual(out["preset"], "quant_paper")
        self.assertTrue(out["flags"]["paper_rebalance"])


if __name__ == "__main__":
    unittest.main()
