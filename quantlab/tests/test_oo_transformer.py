"""ŷ_oo 序列 Transformer 研究轨：PIT 与前向烟雾。"""

from __future__ import annotations

import math
import unittest

import numpy as np


def _synth_bars(n: int = 80, seed: int = 0):
    rng = np.random.RandomState(seed)
    bars = []
    px = 10.0
    for i in range(n):
        o = px * (1.0 + rng.normal(0, 0.005))
        c = o * (1.0 + rng.normal(0, 0.01))
        h = max(o, c) * (1.0 + abs(rng.normal(0, 0.004)))
        lo = min(o, c) * (1.0 - abs(rng.normal(0, 0.004)))
        v = float(rng.randint(1_000_000, 5_000_000))
        bars.append(
            {
                "date": f"2024-{(i // 20) + 1:02d}-{(i % 20) + 1:02d}",
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": v,
            }
        )
        px = c
    # 日期改成严格递增的伪交易日
    from datetime import date, timedelta

    d0 = date(2024, 1, 2)
    for i, b in enumerate(bars):
        b["date"] = (d0 + timedelta(days=i)).isoformat()
    return bars


class TestOoSeqPanel(unittest.TestCase):
    def test_pit_last_bar_before_decision(self):
        from core.research.oo_transformer.seq_dataset import (
            assert_pit_last_bar_before_decision,
            samples_from_bars,
        )

        bars = _synth_bars(60)
        X, gap, y, dates = samples_from_bars("000001", bars, seq_len=10, horizon_days=1)
        self.assertGreater(len(dates), 5)
        self.assertEqual(X.shape[1], 10)
        self.assertEqual(X.shape[2], 8)
        self.assertTrue(np.all(np.isfinite(X)))
        self.assertTrue(np.all(np.isfinite(y)))
        # 每个样本：决策日 > 序列末日
        L = 10
        # 重新定位：dates[k] 对应某决策日；序列末日必为其前一交易日
        by_date = {str(b["date"])[:10]: j for j, b in enumerate(bars)}
        for d in dates[:5]:
            i = by_date[d]
            self.assertTrue(assert_pit_last_bar_before_decision(bars, i, L))

    def test_build_panel_multi(self):
        from core.research.oo_transformer.seq_dataset import build_oo_seq_panel

        stock = {
            "000001": _synth_bars(50, seed=1),
            "000002": _synth_bars(50, seed=2),
        }
        panel = build_oo_seq_panel(stock, seq_len=8, horizon_days=1)
        self.assertGreater(panel.n, 10)
        self.assertEqual(len(panel.codes), panel.n)
        self.assertEqual(panel.X.dtype, np.float32)


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_torch(), "torch not installed")
class TestOoSeqTransformerSmoke(unittest.TestCase):
    def test_forward_and_train_tiny(self):
        from core.research.oo_transformer.model import OoSeqTransformer, count_parameters
        from core.research.oo_transformer.seq_dataset import build_oo_seq_panel
        from core.research.oo_transformer.train import (
            predict_oo_transformer,
            train_oo_transformer,
        )

        stock = {f"{i:06d}": _synth_bars(45, seed=i) for i in range(6)}
        panel = build_oo_seq_panel(stock, seq_len=8, horizon_days=1)
        self.assertGreater(panel.n, 20)
        m = OoSeqTransformer(d_in=8, d_model=16, nhead=2, nlayers=1, dim_feedforward=32)
        self.assertGreater(count_parameters(m), 100)
        n = panel.n
        train_idx = list(range(0, int(n * 0.7)))
        test_idx = list(range(int(n * 0.7), n))
        state, report = train_oo_transformer(
            panel,
            train_idx,
            d_model=16,
            nhead=2,
            nlayers=1,
            epochs=2,
            batch_size=32,
            seed=0,
            device="cpu",
            patience=2,
        )
        self.assertTrue(report.get("success"))
        preds = predict_oo_transformer(
            state,
            panel,
            test_idx,
            d_model=16,
            nhead=2,
            nlayers=1,
            device="cpu",
        )
        self.assertEqual(len(preds), len(test_idx))
        self.assertTrue(all(math.isfinite(float(x)) for x in preds))


if __name__ == "__main__":
    unittest.main()
