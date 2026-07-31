import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.readme_index import (
    ARCHITECTURE_SECTION_ANCHOR,
    REPO_README_DIRS,
    architecture_link_line,
    build_readme_index,
    normalize_readme_dir,
    read_repo_readme,
)


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
        self.assertGreaterEqual(len(data.get("entries") or []), 30)
        self.assertEqual(data["entries"][0]["dir"], REPO_README_DIRS[0])


if __name__ == "__main__":
    unittest.main()
