"""Web UI 标准：ASSET_V 单一注入。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestAssetVersionInjection(unittest.TestCase):
    def test_asset_v_constant(self):
        from web.asset_version import ASSET_V

        self.assertTrue(ASSET_V)
        self.assertNotIn("{{", ASSET_V)

    def test_templates_use_placeholder(self):
        base = os.path.join(ROOT, "web", "static", "templates")
        for name in ("chat.html", "tool.html"):
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("{{ASSET_V}}", text)
            self.assertIn("window.__ASSET_V__", text)
            self.assertNotRegex(text, r"styles\.css\?v=p\d+")

    def test_render_replaces_asset_v(self):
        from web.asset_version import ASSET_V
        from web.page_html import render_chat_html, render_tool_html

        chat = render_chat_html()
        self.assertIn(f"styles.css?v={ASSET_V}", chat)
        self.assertIn(f'window.__ASSET_V__ = "{ASSET_V}"', chat)
        self.assertNotIn("{{ASSET_V}}", chat)

        tool = render_tool_html("watching")
        self.assertIn(f"app.js?v={ASSET_V}", tool)
        self.assertNotIn("{{ASSET_V}}", tool)


if __name__ == "__main__":
    unittest.main()
