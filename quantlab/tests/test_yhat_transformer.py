"""yhat_transformer：HeadSpec + PIT + 前向烟雾。"""

from __future__ import annotations

import unittest

import numpy as np


class TestHeadSpec(unittest.TestCase):
    def test_four_heads_registered(self):
        from core.research.yhat_transformer.heads import get_head, head_ids, iter_heads

        self.assertEqual(head_ids(), ["oo", "tc", "oo_rank", "tc_rank"])
        oo = get_head("oo")
        self.assertEqual(oo.task_type, "regression")
        self.assertEqual(oo.status, "holdout_ready")
        self.assertIn("run_yhat_transformer_holdout", oo.default_cli or "")
        tc = get_head("tc")
        self.assertEqual(tc.status, "holdout_ready")
        self.assertEqual(tc.factor_pack, "atc_core")
        oo_r = get_head("oo_rank")
        self.assertEqual(oo_r.task_type, "rank")
        self.assertEqual(oo_r.status, "holdout_ready")
        self.assertEqual(oo_r.loss, "listwise")
        self.assertIn("--head oo_rank", oo_r.default_cli or "")
        tc_r = get_head("tc_rank")
        self.assertEqual(tc_r.status, "holdout_ready")
        self.assertIn("--head tc_rank", tc_r.default_cli or "")
        from core.research.yhat_transformer.heads import assert_trainable

        for hid in head_ids():
            assert_trainable(get_head(hid))
        labels = [h.label for h in iter_heads()]
        self.assertEqual(len(labels), 4)

    def test_tc_open_seq_label(self):
        from core.research.yhat_transformer.seq_dataset import build_seq_panel

        stock = {
            "000001": _synth_bars(50, seed=1),
            "000002": _synth_bars(50, seed=2),
        }
        panel = build_seq_panel(stock, head="tc", seq_len=8, horizon_days=1)
        self.assertGreater(panel.n, 10)
        # close/open 通常 |y| 小于极端 oo 隔夜；但仍应有限
        self.assertTrue(np.all(np.isfinite(panel.y)))

    def test_rank_heads_reuse_regression_labels(self):
        from core.research.yhat_transformer.seq_dataset import build_seq_panel

        stock = {
            "000001": _synth_bars(50, seed=1),
            "000002": _synth_bars(50, seed=2),
        }
        oo = build_seq_panel(stock, head="oo", seq_len=8, horizon_days=1)
        oo_r = build_seq_panel(stock, head="oo_rank", seq_len=8, horizon_days=1)
        self.assertEqual(oo.n, oo_r.n)
        np.testing.assert_allclose(oo.y, oo_r.y, rtol=0, atol=1e-5)
        tc = build_seq_panel(stock, head="tc", seq_len=8, horizon_days=1)
        tc_r = build_seq_panel(stock, head="tc_rank", seq_len=8, horizon_days=1)
        self.assertEqual(tc.n, tc_r.n)
        np.testing.assert_allclose(tc.y, tc_r.y, rtol=0, atol=1e-5)

def _synth_bars(n: int = 80, seed: int = 0):
    rng = np.random.RandomState(seed)
    bars = []
    px = 10.0
    from datetime import date, timedelta

    d0 = date(2024, 1, 2)
    for i in range(n):
        o = px * (1.0 + rng.normal(0, 0.005))
        c = o * (1.0 + rng.normal(0, 0.01))
        h = max(o, c) * (1.0 + abs(rng.normal(0, 0.004)))
        lo = min(o, c) * (1.0 - abs(rng.normal(0, 0.004)))
        v = float(rng.randint(1_000_000, 5_000_000))
        bars.append(
            {
                "date": (d0 + timedelta(days=i)).isoformat(),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": v,
            }
        )
        px = c
    return bars


class TestYhatSeqPanel(unittest.TestCase):
    def test_pit_and_build(self):
        from core.research.yhat_transformer.seq_dataset import (
            assert_pit_last_bar_before_decision,
            build_seq_panel,
            samples_from_bars,
        )

        bars = _synth_bars(60)
        X, gap, y, dates = samples_from_bars("000001", bars, seq_len=10, horizon_days=1)
        self.assertGreater(len(dates), 5)
        by_date = {str(b["date"])[:10]: j for j, b in enumerate(bars)}
        for d in dates[:5]:
            i = by_date[d]
            self.assertTrue(assert_pit_last_bar_before_decision(bars, i, 10))
        panel = build_seq_panel(
            {"000001": _synth_bars(50, 1), "000002": _synth_bars(50, 2)},
            head="oo",
            seq_len=8,
            horizon_days=1,
        )
        self.assertGreater(panel.n, 10)


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_torch(), "torch missing")
class TestYhatModelSmoke(unittest.TestCase):
    def test_fusion_forward(self):
        import torch

        from core.research.yhat_transformer.model import (
            SeqFactorFusion,
            SeqTransformer,
            count_parameters,
        )

        m = SeqTransformer(d_in=8, d_model=16, nhead=2, nlayers=1, dim_feedforward=32)
        self.assertGreater(count_parameters(m), 100)
        x = torch.randn(4, 8, 8)
        g = torch.randn(4)
        y = m(x, g)
        self.assertEqual(tuple(y.shape), (4,))
        fus = SeqFactorFusion(
            d_seq_in=8, d_factor=12, d_model=16, nhead=2, nlayers=1, factor_hidden=32
        )
        out = fus(x, g, torch.randn(4, 12))
        self.assertEqual(tuple(out.shape), (4,))

    def test_listwise_train_smoke(self):
        from core.research.yhat_transformer.seq_dataset import build_seq_panel
        from core.research.yhat_transformer.train import (
            predict_yhat_transformer,
            train_yhat_transformer,
        )

        stock = {f"{i:06d}": _synth_bars(40, seed=i) for i in range(1, 12)}
        panel = build_seq_panel(stock, head="oo_rank", seq_len=6, horizon_days=1)
        self.assertGreater(panel.n, 30)
        idx = list(range(panel.n))
        state, report = train_yhat_transformer(
            panel,
            idx,
            d_model=16,
            nhead=2,
            nlayers=1,
            epochs=2,
            batch_size=64,
            seed=0,
            patience=2,
            loss="listwise",
        )
        self.assertTrue(report.get("success"))
        self.assertEqual(report.get("loss"), "listwise")
        preds = predict_yhat_transformer(
            state, panel, idx[:20], d_model=16, nhead=2, nlayers=1
        )
        self.assertEqual(preds.shape[0], 20)
        self.assertTrue(np.all(np.isfinite(preds)))


if __name__ == "__main__":
    unittest.main()
