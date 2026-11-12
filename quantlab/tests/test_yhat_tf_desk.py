"""L4 四头 ŷ Transformer 隐藏研究台。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock


class TestYhatTfDesk(unittest.TestCase):
    def test_desk_roundtrip_no_live(self):
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ):
                from core.research.yhat_tf_desk import (
                    desk_status,
                    promote_draft_to_research,
                    record_holdout_intent,
                    save_fit_draft,
                )

                st0 = desk_status()
                self.assertTrue(st0["success"])
                self.assertEqual(st0["package"], "yhat_transformer")
                self.assertFalse(st0["live_allowed"])
                self.assertEqual(len(st0["heads"]), 4)
                self.assertIn("run_yhat_transformer_holdout", st0["scheme"]["per_head"]["oo"]["script"])
                self.assertEqual(st0["scheme"]["per_head"]["tc"]["status"], "holdout_ready")
                self.assertIn("--head tc", st0["scheme"]["per_head"]["tc"]["script"] or "")
                self.assertEqual(
                    st0["scheme"]["per_head"]["oo_rank"]["status"], "holdout_ready"
                )
                self.assertIn(
                    "--head oo_rank",
                    st0["scheme"]["per_head"]["oo_rank"]["script"] or "",
                )

                save_fit_draft("oo", {"d_model": 32, "batch_size": 16}, note="t")
                out = record_holdout_intent("oo", {"epochs": 3})
                self.assertTrue(out["success"])
                self.assertIn("run_yhat_transformer_holdout", out.get("cli") or "")
                out_r = record_holdout_intent("oo_rank", {"epochs": 3})
                self.assertTrue(out_r["success"])
                self.assertIn("--head oo_rank", out_r.get("cli") or "")

                promo = promote_draft_to_research("oo")
                self.assertTrue(promo["success"])
                self.assertFalse(promo["research"]["live_allowed"])

                with open(os.path.join(td, "oo_research.json"), encoding="utf-8") as f:
                    blob = json.load(f)
                self.assertEqual(blob["role"], "yhat_tf_research")


class TestYhatTfPageWiring(unittest.TestCase):
    def test_palette_and_render(self):
        from web.page_html import clear_html_cache, render_tool_html

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pal = os.path.join(root, "web/static/js/command_palette.js")
        with open(pal, encoding="utf-8") as f:
            pal_js = f.read()
        self.assertIn('id: "nav-yhat-tf"', pal_js)
        self.assertIn("/yhat-tf", pal_js)
        self.assertIn("前往 · 模型中心", pal_js)

        clear_html_cache()
        html = render_tool_html("yhat_tf")
        self.assertIn("模型中心", html)
        self.assertIn("<title>QuantLab · 模型中心</title>", html)
        self.assertIn('data-head-panel="oo_rank"', html)
        self.assertIn('data-head-select="oo_rank"', html)
        self.assertIn('data-head-section="tc_rank"', html)
        self.assertIn('href="#yhat-tf-section-oo-rank"', html)
        self.assertIn('id="yhat-tf-toggle-view"', html)
        self.assertIn("yhat-tf-param-group", html)
        self.assertIn("yhat-tf-result-grid", html)
        self.assertIn("yhat-tf-tech", html)
        self.assertIn("OOS 指标 · 待回传", html)
        self.assertIn("生产禁用", html)
        self.assertNotIn('data-act="promote-live"', html)
        self.assertIn("quant-page yhat-tf-page", html)
        self.assertIn("quant-path-rail", html)
        with open(
            os.path.join(root, "web/static/css/quant.css"), encoding="utf-8"
        ) as f:
            css = f.read()
        self.assertIn(
            ".quant-page.yhat-tf-page .dashboard-card.quant-pro-card > .quant-card-head--fit",
            css,
        )
        self.assertIn("display: grid", css)
        self.assertIn(
            ".quant-page.yhat-tf-page .yhat-tf-arms .quant-path-step .quant-pro-path-num",
            css,
        )
        self.assertIn(".yhat-tf-page .dialog-btn:disabled", css)
        self.assertIn(".yhat-tf-head-grid", css)
        self.assertIn(".yhat-tf-param-group", css)
        self.assertIn(".yhat-tf-result-grid", css)
        self.assertIn(".yhat-tf-head.is-compact", css)
        self.assertIn("--sys-r-tier1", css)


if __name__ == "__main__":
    unittest.main()
