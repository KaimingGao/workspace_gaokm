"""quant 包 / shim / README / CI 守卫（合并原 P28 / P31·package / P34–P35 / P39–P44）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import importlib
import ast
import glob
from unittest.mock import patch
from agent.routing import infer_quant_task, prepare_tool_params
from quant.skill.engine import AVAILABLE_TASKS, QuantEngine
from quant.ops.shim_audit import audit_quant_shim_imports, main as shim_audit_main
from quant.ops.package_info import REMOVED_SHIM_PATHS, build_quant_package_info
from quant.ops.shim_audit import audit_quant_shim_imports
from core.readme_index import REPO_README_DIRS, build_readme_index
from quant.ops.package_info import build_quant_package_info
from core.readme_index import ARCHITECTURE_SECTION_ANCHOR, REPO_README_DIRS, architecture_link_line, build_readme_index, normalize_readme_dir, read_repo_readme
from evals.readme_check import check_readme_coverage
from services.eval_service import EvalService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TESTS_DIR = os.path.join(ROOT, "tests")
LEGACY_MODULES = (
    "advisor",
    "services.quant_service",
    "services.quant_report_export",
    "services.quant_report_index",
    "services.quant_interpret",
    "services.signal_config_preview",
    "services.portfolio_quant_bridge",
    "services.daily_presets",
    "services.daily_health",
    "services.eval_routing_map",
    "research.factor_report",
    "research.paper_vs_backtest",
    "research.paper_vs_portfolio",
    "skills.quant.engine",
    "skills.quant.handler",
)
def _imports_in_file(path: str):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
REPO_ROOT = os.path.dirname(ROOT)
WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "investment-ci.yml")
CI_QUANT_PATH = os.path.join(ROOT, "scripts", "ci_quant.sh")

# --- test_p28_quant.py::TestP28QuantPackage ---
class TestP28QuantPackage(unittest.TestCase):
    def test_quant_package_exports(self):
        import quant

        self.assertTrue(hasattr(quant, "QuantService"))
    def test_skills_quant_init_reexports(self):
        from skills.quant import QuantEngine
        from quant.skill.engine import QuantEngine as CanonicalEngine

        self.assertIs(QuantEngine, CanonicalEngine)
    def test_skill_registry(self):
        from quant.skill.engine import AVAILABLE_TASKS
        from agent.registry import SKILL_SPECS

        self.assertIn("portfolio_bridge", AVAILABLE_TASKS)
        quant_spec = next(spec for name, spec in SKILL_SPECS if name == "quant")
        self.assertEqual(quant_spec, "quant.skill.handler.QuantHandler")

# --- test_p31_quant.py::TestP32QuantPackageInfo ---
class TestP32QuantPackageInfo(unittest.TestCase):
    def test_build_quant_package_info(self):
        from quant.ops.package_info import build_quant_package_info

        out = build_quant_package_info()
        self.assertTrue(out["success"])
        self.assertEqual(out["package"], "quant")
        self.assertIn("services", out["modules"])
        self.assertIn("eval_routing_map", out["modules"]["ops"])
        self.assertTrue(out.get("shims_removed"))
        self.assertEqual(out["shim_paths"], [])
        self.assertIn("services/quant_service.py", out["removed_shim_paths"])
    def test_quant_service_wrapper(self):
        from quant.services.quant_service import QuantService

        out = QuantService().build_package_info()
        self.assertGreaterEqual(out["module_count"], 10)
    def test_quant_package_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/quant/package")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertIn("skill", body["subpackages"])

# --- test_p31_quant.py::TestP33CanonicalCliImports ---
class TestP33CanonicalCliImports(unittest.TestCase):
    def test_preset_check_imports_quant_ops(self):
        import evals.preset_check as mod

        self.assertIs(
            mod.resolve_daily_preset,
            importlib.import_module("quant.ops.daily_presets").resolve_daily_preset,
        )
    def test_daily_run_imports_quant_presets(self):
        import research.daily_run as mod
        from quant.ops.daily_presets import DAILY_PRESETS as canonical

        self.assertIs(mod.DAILY_PRESETS, canonical)

# --- test_p34_quant.py::TestP34CanonicalTestImports ---
class TestP34CanonicalTestImports(unittest.TestCase):
    def test_quant_tests_use_canonical_imports(self):
        offenders = []
        for path in glob.glob(os.path.join(TESTS_DIR, "test_*.py")):
            base = os.path.basename(path)
            for mod in _imports_in_file(path):
                if mod in LEGACY_MODULES:
                    offenders.append(f"{base}: {mod}")
        self.assertEqual(offenders, [])

# --- test_p35_quant.py::TestP35PackageInfoRouting ---
class TestP35PackageInfoRouting(unittest.TestCase):
    def test_infer_package_info(self):
        self.assertEqual(infer_quant_task("quant 包结构有哪些模块"), "package_info")
        self.assertEqual(infer_quant_task("quant/ 目录树"), "package_info")
    def test_prepare_package_info(self):
        params = prepare_tool_params("quant", {}, "quant 包结构有哪些模块")
        self.assertEqual(params.get("task"), "package_info")
    def test_available_tasks_include_package_info(self):
        self.assertIn("package_info", AVAILABLE_TASKS)

# --- test_p35_quant.py::TestP35PackageInfoTask ---
class TestP35PackageInfoTask(unittest.TestCase):
    @patch("quant.services.quant_service.QuantService.build_package_info")
    def test_package_info_task(self, mock_info):
        mock_info.return_value = {
            "success": True,
            "package": "quant",
            "module_count": 12,
            "subpackages": ["services", "ops", "research", "skill"],
        }
        out = QuantEngine().run({"task": "package_info"})
        self.assertTrue(out["success"])
        self.assertEqual(out["task"], "package_info")
        self.assertEqual(out["module_count"], 12)

# --- test_p39_quant.py::TestP39ShimImportAudit ---
class TestP39ShimImportAudit(unittest.TestCase):
    def test_audit_passes_on_repo(self):
        out = audit_quant_shim_imports()
        self.assertTrue(out["success"])
        self.assertTrue(out["ok"], out.get("offenders"))
        self.assertGreater(out["scanned_files"], 50)
    def test_audit_cli_ok(self):
        self.assertEqual(shim_audit_main([]), 0)

# --- test_p40_quant.py::TestP40GithubCiImportAudit ---
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

# --- test_p41_quant.py::TestP41ShimsRemoved ---
class TestP41ShimsRemoved(unittest.TestCase):
    def test_removed_shim_files_absent(self):
        still_present = []
        for rel in REMOVED_SHIM_PATHS:
            path = os.path.join(ROOT, rel.replace("/", os.sep)).rstrip(os.sep)
            if os.path.exists(path):
                still_present.append(rel)
        self.assertEqual(still_present, [])
    def test_package_info_marks_shims_removed(self):
        out = build_quant_package_info()
        self.assertTrue(out.get("shims_removed"))
        self.assertEqual(out["shim_paths"], [])
        self.assertGreaterEqual(len(out["removed_shim_paths"]), 10)
    def test_skills_quant_tool_config_remains(self):
        path = os.path.join(ROOT, "skills", "quant", "tool_config.json")
        self.assertTrue(os.path.isfile(path))

# --- test_p42_quant.py::TestP42ReadmeIndex ---
class TestP42ReadmeIndex(unittest.TestCase):
    def test_all_expected_dirs_have_readme(self):
        out = build_readme_index()
        self.assertTrue(out["coverage_ok"], out.get("missing"))
        self.assertEqual(out["present_count"], len(REPO_README_DIRS))
        self.assertEqual(out["missing_count"], 0)
    def test_package_info_includes_readme_index(self):
        out = build_quant_package_info()
        idx = out.get("readme_index") or {}
        self.assertTrue(idx.get("coverage_ok"))
        self.assertEqual(idx.get("present_count"), len(REPO_README_DIRS))

# --- test_p43_quant.py::TestP43ReadmeContentApi ---
class TestP43ReadmeContentApi(unittest.TestCase):
    def test_read_repo_readme_agent(self):
        out = read_repo_readme("agent")
        self.assertTrue(out["success"])
        self.assertTrue(out["content"].startswith("# agent"))
        self.assertEqual(out["path"], "agent/README.md")
        self.assertTrue(out["doc_links"])
    def test_read_repo_readme_rejects_unknown_dir(self):
        with self.assertRaises(ValueError):
            normalize_readme_dir("not-a-real-dir")
        with self.assertRaises(ValueError):
            normalize_readme_dir("../agent")
        with self.assertRaises(ValueError):
            normalize_readme_dir("advisor")
    def test_readme_index_has_entries(self):
        out = build_readme_index()
        self.assertEqual(len(out["entries"]), len(REPO_README_DIRS))
        self.assertTrue(all(e.get("api_url") for e in out["entries"]))
    def test_every_readme_links_architecture_index(self):
        missing = []
        marker = f"architecture.md#{ARCHITECTURE_SECTION_ANCHOR}"
        for rel in REPO_README_DIRS:
            out = read_repo_readme(rel)
            if marker not in out.get("content", ""):
                missing.append(rel)
        self.assertEqual(missing, [])
    def test_architecture_doc_has_readme_table(self):
        path = os.path.join(ROOT, "docs", "architecture.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## 子目录 README 索引", text)
        self.assertIn("`agent/`", text)
        self.assertIn("../quant/README.md", text)

# --- test_p43_quant.py::TestP43ReadmeWebApi ---
class TestP43ReadmeWebApi(unittest.TestCase):
    def test_readme_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        ok = client.get("/api/readme?dir=quant")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("# quant", ok.json()["content"])

        bad = client.get("/api/readme?dir=../../etc")
        self.assertEqual(bad.status_code, 404)
    def test_readme_index_entries_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/readme-index")
        data = res.json()
        self.assertGreaterEqual(len(data.get("entries") or []), 10)
        self.assertEqual(data["entries"][0]["dir"], REPO_README_DIRS[0])

# --- test_p44_quant.py::TestP44ReadmeCheck ---
class TestP44ReadmeCheck(unittest.TestCase):
    def test_check_readme_coverage_ok(self):
        out = check_readme_coverage()
        self.assertTrue(out["ok"], out.get("failures"))
        self.assertEqual(out["present_count"], out["total_dirs"])
        self.assertGreaterEqual(len(out.get("entries") or []), 10)
    def test_run_readme_check_cli(self):
        import evals.run_readme_check as mod

        self.assertEqual(mod.main(), 0)
    def test_eval_service_summary_includes_readme(self):
        summary = EvalService().summary()
        self.assertIn("readme", summary)
        self.assertTrue(summary["readme"].get("ok"))
    def test_eval_service_run_with_presets_includes_readme(self):
        report = EvalService().run(use_mock=True, with_presets=True, save=False)
        self.assertIn("readme", report)
        self.assertTrue(report["readme"].get("ok"))

# --- test_p44_quant.py::TestP44ReadmeEvalsApi ---
class TestP44ReadmeEvalsApi(unittest.TestCase):
    def test_evals_readme_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/evals/readme")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertGreaterEqual(data["present_count"], 10)
    def test_evals_summary_readme_field(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/evals/summary")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["readme"]["ok"])


if __name__ == "__main__":
    unittest.main()
