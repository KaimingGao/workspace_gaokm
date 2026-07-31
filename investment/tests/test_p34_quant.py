import ast
import glob
import os
import sys
import unittest

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


class TestP34CanonicalTestImports(unittest.TestCase):
    def test_quant_tests_use_canonical_imports(self):
        offenders = []
        for path in glob.glob(os.path.join(TESTS_DIR, "test_p*_quant.py")):
            base = os.path.basename(path)
            for mod in _imports_in_file(path):
                if mod in LEGACY_MODULES:
                    offenders.append(f"{base}: {mod}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
