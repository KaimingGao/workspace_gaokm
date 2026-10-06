import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.daily_service import DailyRunService
from services.eval_service import EvalService


class TestDailyService(unittest.TestCase):
    def test_eval_mock_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = DailyRunService(
                evals=EvalService(last_run_path=os.path.join(tmp, "last.json"))
            )
            out = svc.run(eval_mock=True)
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["steps"]), 1)
        self.assertEqual(out["steps"][0]["name"], "eval_mock")

    def test_paper_requires_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            from services.paper_service import PaperService

            svc = DailyRunService(paper=PaperService(path=os.path.join(tmp, "paper.json")))
            out = svc.run(paper_run=True, eval_mock=True)
        self.assertFalse(out["ok"])
        self.assertTrue(out["eval_ok"])
        paper_step = next(s for s in out["steps"] if s["name"] == "paper")
        self.assertEqual(paper_step.get("code"), "paper_not_initialized")


class TestDailyRunCli(unittest.TestCase):
    def test_cli_eval_mock(self):
        from research import daily_run

        code = daily_run.main(["--eval-mock"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
