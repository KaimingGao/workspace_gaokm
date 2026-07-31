import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP72DocsSync(unittest.TestCase):
    def _read(self, rel):
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_quant_upgrade_has_p69_p72(self):
        text = self._read("docs/quant-upgrade.md")
        for tag in ("P69", "P70", "P71", "P72"):
            self.assertIn(tag, text)

    def test_quant_summary_lists_p69_p72(self):
        text = self._read("docs/quant-summary.md")
        self.assertIn("P69", text)
        self.assertIn("P72", text)

    def test_evals_readme_mentions_interpret_neutral_regression(self):
        text = self._read("evals/README.md")
        self.assertIn("quant_interpret_neutral", text)
        self.assertIn("agent_regression_quant", text)

    def test_quant_md_mentions_export_preview_and_offline(self):
        text = self._read("docs/quant.md")
        self.assertIn("导出预览", text)
        self.assertIn("offline", text)


if __name__ == "__main__":
    unittest.main()
