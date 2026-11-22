"""T 臂每个 epoch 的 Holdout 行不能标成完成。"""

from __future__ import annotations

import os
import re
import unittest

from tests.conftest import ROOT

# 与 yhat_tf.js isEpochHoldoutLine 同一条。轮次 Holdout 带 rankIC=，不能当成结束行。
_EPOCH_HOLDOUT = re.compile(r"^transformer\s+ep=\d+/\d+\s+holdout\b")


class TestYhatTfArmLog(unittest.TestCase):
    def test_epoch_holdout_skipped_before_done(self):
        path = os.path.join(ROOT, "web", "static", "js", "yhat_tf.js")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        fn = src.split("export function liveArmFromLog", 1)[1].split(
            "export function armStatus", 1
        )[0]
        holdout_at = fn.find("if (isEpochHoldoutLine(s))")
        fail_at = fn.find("if (/失败/.test(s))")
        rank_at = fn.find("s.match(/rankIC=")
        ok_at = fn.find('phase = "ok"')
        self.assertGreater(holdout_at, 0)
        self.assertLess(holdout_at, fail_at)
        self.assertLess(holdout_at, rank_at)
        self.assertLess(holdout_at, ok_at)
        self.assertIn("if (preview) pack = preview;", fn)
        self.assertIn("cs_ndcg_30: nums[4]", fn)
        self.assertNotIn("cs_ndcg_30: pack?.cs_ndcg_30 ?? null", fn)
        hub = os.path.join(ROOT, "web", "static", "partials", "quant_panel.html")
        with open(hub, encoding="utf-8") as f:
            hub_html = f.read()
        self.assertEqual(hub_html.count(">NDCG@30<"), 2)
        self.assertEqual(hub_html.count(">命中<"), 2)
        self.assertIn(
            r"/^transformer\s+ep=\d+\/\d+\s+holdout\b/",
            src,
        )
        self.assertIn('setMetric(tag, "icir", fmtKpi3(pack?.cs_icir))', src)
        self.assertIn('setMetric(tag, "rankic", fmtKpi3(pack?.cs_rank_ic))', src)
        self.assertIn('setMetric(tag, "csicir", fmtKpi3(pack?.cs_icir))', src)
        self.assertNotIn("fmtIc(pack?.cs_rank_icir)", src)
        panel = os.path.join(ROOT, "web", "static", "partials", "yhat_factory_panel.html")
        with open(panel, encoding="utf-8") as f:
            html = f.read()
        self.assertIn(">Rank IC<", html)
        self.assertIn(">Top10% y<", html)
        self.assertNotIn(">RankIC<", html)
        self.assertEqual(html.count("yhat-tf-arm-metrics--five"), 6)
        self.assertEqual(html.count(">MSE<"), 6)
        self.assertNotIn(">斜率<", html)
        self.assertNotIn(">正侧实现<", html)
        self.assertEqual(hub_html.count(">MSE<"), 2)
        self.assertNotIn(">斜率<", hub_html)
        self.assertIn('setMetric(tag, "mse", fmtKpi3(pack?.mse))', src)
        self.assertEqual(html.count('data-arm-metric="T:rankic"'), 2)
        self.assertEqual(html.count('data-arm-metric="T:csicir"'), 2)
        self.assertNotIn(">TopY<", html)
        self.assertIn("function rawLogLines(raw)", src)
        self.assertIn('tag === "T"\n        ? rawLogLines(st?.log_tail)', src)
        self.assertEqual(hub_html.count("data-fit-curves"), 4)
        self.assertEqual(html.count("data-fit-curves"), 4)
        self.assertIn("paintFitCurves(root, st?.fit_curve)", src)

    def test_epoch_holdout_lines_are_not_terminal(self):
        current = (
            "transformer ep=4/6 holdout rankIC=0.0781 ICIR=0.4629 "
            "rankICIR=0.2100 sign_hit=0.5386 topY=-0.0128 n=95629"
        )
        current_fail = "transformer ep=3/6 holdout 失败：RuntimeError: boom"
        self.assertIsNotNone(_EPOCH_HOLDOUT.match(current))
        self.assertIsNotNone(_EPOCH_HOLDOUT.match(current_fail))
        for line in (
            "transformer step=22021 ep=5/6 [4041/4495] loss=1.3766",
            "transformer ep=4/6 done train_loss=1.4498",
            "transformer ep=4/6 probe rankIC=0.0781 ICIR=0.4629 sign_hit=0.5386 topY=-0.0128 n=95629",
            "transformer ep=3/6 probe 失败：RuntimeError: boom",
            "ArmL rankIC=0.0378 ICIR=0.2271 sign_hit=0.5046 topY=-0.0448 740.94s",
            "ArmT seed=42 rankIC=0.0362 ICIR=0.2292 sign_hit=0.5319 topY=0.1935 2509.98s",
            "ArmT 失败：MaterializeRequired: cube miss",
            "T_transformer   0.036    0.210    0.229    0.532    0.650    0.194",
        ):
            self.assertIsNone(_EPOCH_HOLDOUT.match(line), msg=line)


if __name__ == "__main__":
    unittest.main()
