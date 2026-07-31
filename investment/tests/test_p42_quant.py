import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.readme_index import REPO_README_DIRS, build_readme_index
from quant.ops.package_info import build_quant_package_info


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

    def test_readme_index_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/readme-index")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("coverage_ok"))
        self.assertIn("docs", data["present"])


if __name__ == "__main__":
    unittest.main()
