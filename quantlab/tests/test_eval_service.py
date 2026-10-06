import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.eval_service import EvalService


class TestEvalService(unittest.TestCase):
    def test_list_cases(self):
        cases = EvalService().list_cases()
        self.assertGreaterEqual(len(cases), 15)
        self.assertTrue(all(c.get("has_mock") for c in cases))

    def test_run_all_mock_offline(self):
        report = EvalService().run(use_mock=True, with_agent=False)
        self.assertEqual(report["total"], len(report["cases"]))
        self.assertTrue(report["ok"], report.get("failures"))

    def test_save_and_load_last(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "last.json")
            svc = EvalService(last_run_path=path)
            report = svc.run(use_mock=True, with_agent=False, save=True)
            loaded = svc.load_last_report()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["total"], report["total"])
            self.assertIn("saved_at", loaded)


if __name__ == "__main__":
    unittest.main()
