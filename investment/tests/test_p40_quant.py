import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "investment-ci.yml")
CI_QUANT_PATH = os.path.join(ROOT, "scripts", "ci_quant.sh")


class TestP40GithubCiImportAudit(unittest.TestCase):
    def test_workflow_runs_quant_import_audit(self):
        self.assertTrue(os.path.isfile(WORKFLOW_PATH), WORKFLOW_PATH)
        with open(WORKFLOW_PATH, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("check_quant_imports.sh", text)
        self.assertIn("Quant import audit", text)
        unit_idx = text.index("Unit tests")
        audit_idx = text.index("Quant import audit")
        repro_idx = text.index("Repro evals")
        self.assertLess(unit_idx, audit_idx)
        self.assertLess(audit_idx, repro_idx)

    def test_ci_quant_script_includes_import_audit(self):
        with open(CI_QUANT_PATH, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("check_quant_imports.sh", text)
        audit_idx = text.index("quant import audit")
        repro_idx = text.index("repro evals")
        self.assertLess(audit_idx, repro_idx)

    def test_eval_summary_lists_github_ci(self):
        from services.eval_service import EvalService

        cmds = EvalService().summary()["ci_commands"]
        self.assertIn("github", cmds)
        self.assertIn("import audit", cmds["github"])


if __name__ == "__main__":
    unittest.main()
