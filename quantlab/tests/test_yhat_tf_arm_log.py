"""T 臂轮次 Holdout 探针不能标成完成。"""

from __future__ import annotations

import os
import re
import unittest

from tests.conftest import ROOT

# 与 yhat_tf.js isEpochHoldoutProbe 同一条。轮次探针带 rankIC=，不能当成结束行。
_PROBE = re.compile(r"^transformer\s+ep=\d+/\d+\s+holdout\b")


class TestYhatTfArmLog(unittest.TestCase):
    def test_epoch_probe_skipped_before_done(self):
        path = os.path.join(ROOT, "web", "static", "js", "yhat_tf.js")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        fn = src.split("export function liveArmFromLog", 1)[1].split(
            "export function armStatus", 1
        )[0]
        probe_at = fn.find("if (isEpochHoldoutProbe(s)) continue;")
        fail_at = fn.find("if (/失败/.test(s))")
        rank_at = fn.find("s.match(/rankIC=")
        self.assertGreater(probe_at, 0)
        self.assertLess(probe_at, fail_at)
        self.assertLess(probe_at, rank_at)
        self.assertIn(
            r"/^transformer\s+ep=\d+\/\d+\s+holdout\b/",
            src,
        )
        self.assertIn('setMetric(tag, "icir", fmtKpi3(pack?.cs_icir))', src)
        self.assertNotIn("fmtIc(pack?.cs_rank_icir)", src)
        panel = os.path.join(ROOT, "web", "static", "partials", "yhat_factory_panel.html")
        with open(panel, encoding="utf-8") as f:
            html = f.read()
        self.assertIn(">Rank IC<", html)
        self.assertIn(">Top10% y<", html)
        self.assertNotIn(">RankIC<", html)
        self.assertNotIn(">TopY<", html)
        self.assertIn("function rawLogLines(raw)", src)
        self.assertIn('tag === "T"\n        ? rawLogLines(st?.log_tail)', src)

    def test_probe_lines_are_not_terminal(self):
        probe = (
            "transformer ep=4/6 holdout rankIC=0.0781 ICIR=0.4629 "
            "sign_hit=0.5386 topY=-0.0128 n=95629"
        )
        probe_fail = "transformer ep=3/6 holdout 失败：RuntimeError: boom"
        self.assertIsNotNone(_PROBE.match(probe))
        self.assertIsNotNone(_PROBE.match(probe_fail))
        for line in (
            "transformer step=22021 ep=5/6 [4041/4495] loss=1.3766",
            "transformer ep=4/6 done train_loss=1.4498",
            "ArmL rankIC=0.0378 ICIR=0.2271 sign_hit=0.5046 topY=-0.0448 740.94s",
            "ArmT seed=42 rankIC=0.0362 ICIR=0.2292 sign_hit=0.5319 topY=0.1935 2509.98s",
            "ArmT 失败：MaterializeRequired: cube miss",
            "T_transformer   0.0362    0.229    0.532    0.194",
        ):
            self.assertIsNone(_PROBE.match(line), msg=line)


if __name__ == "__main__":
    unittest.main()
