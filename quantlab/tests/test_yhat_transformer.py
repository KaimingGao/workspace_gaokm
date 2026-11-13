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
        self.assertFalse(hasattr(oo, "default_cli"))
        tc = get_head("tc")
        self.assertEqual(tc.status, "holdout_ready")
        self.assertEqual(tc.factor_pack, "atc_full")
        self.assertEqual(oo.factor_pack, "aoo_full")
        oo_r = get_head("oo_rank")
        self.assertEqual(oo_r.task_type, "rank")
        self.assertEqual(oo_r.status, "holdout_ready")
        self.assertEqual(oo_r.loss, "listwise")
        tc_r = get_head("tc_rank")
        self.assertEqual(tc_r.status, "holdout_ready")
        from core.research.yhat_transformer.heads import assert_trainable
        from core.research.yhat_tf_desk import build_holdout_cli

        self.assertNotIn("--atc-core", build_holdout_cli("tc"))
        self.assertIn("--atc-core", build_holdout_cli("tc", {"full_atc": False}))
        for hid in head_ids():
            assert_trainable(get_head(hid))
            cli = build_holdout_cli(hid)
            self.assertIn("run_yhat_transformer_holdout", cli)
            self.assertIn(f"--head {hid}", cli)
        labels = [h.label for h in iter_heads()]
        self.assertEqual(len(labels), 4)

    def test_align_seq_and_factor_overwrites_fac_y(self):
        from core.research.yhat_transformer.factor_panel import (
            FactorPanel,
            align_seq_and_factor,
        )
        from core.research.yhat_transformer.seq_dataset import SeqPanel

        n, L, F = 4, 3, 8
        dates = ["2024-01-02", "2024-01-03", "2024-01-02", "2024-01-03"]
        codes = ["000001", "000001", "000002", "000002"]
        seq = SeqPanel(
            X=np.zeros((n, L, F), dtype=np.float32),
            gap=np.zeros((n,), dtype=np.float32),
            y=np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float32),
            dates=dates,
            codes=codes,
            seq_len=L,
        )
        fac = FactorPanel(
            X=np.zeros((n, 2), dtype=np.float32),
            y=np.asarray([1.0, 9.0, 3.0, 8.0], dtype=np.float32),
            dates=dates,
            codes=codes,
            feature_names=["a", "b"],
        )
        sc, fc = align_seq_and_factor(seq, fac)
        self.assertEqual(len(sc.y), 4)
        np.testing.assert_allclose(fc.y, sc.y)

    def test_arm_t_slice_leaves_full_for_l(self):
        from core.research.yhat_transformer.factor_panel import (
            FactorPanel,
            slice_arm_t_panel,
        )
        from core.signal.factors.alpha_tc import (
            ALPHA_TC_FIELD_NAMES,
            ALPHA_TC_RAW_PREFIX,
        )

        names = [
            "gap_pct",
            "open_gap",
            "gap_atr",
            "yclose_loc",
            "tau_lag1",
            "tau_ma5",
        ] + [f"{ALPHA_TC_RAW_PREFIX}{k}" for k in ALPHA_TC_FIELD_NAMES]
        panel = FactorPanel(
            X=np.zeros((4, len(names)), dtype=np.float32),
            y=np.zeros((4,), dtype=np.float32),
            dates=["d"] * 4,
            codes=["c"] * 4,
            feature_names=names,
        )
        full = slice_arm_t_panel(panel, "tc", atc_core_only=False)
        self.assertIs(full, panel)
        self.assertEqual(full.X.shape[1], 230)
        core = slice_arm_t_panel(panel, "tc", atc_core_only=True)
        self.assertEqual(core.X.shape[1], 55)
        self.assertEqual(panel.X.shape[1], 230)
        self.assertFalse(np.shares_memory(core.X, panel.X))
        short = FactorPanel(
            X=np.zeros((2, 3), dtype=np.float32),
            y=np.zeros((2,), dtype=np.float32),
            dates=["d", "d"],
            codes=["c", "c"],
            feature_names=["a", "b", "c"],
        )
        with self.assertRaises(ValueError):
            slice_arm_t_panel(short, "tc", atc_core_only=True)

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
    def test_few_stock_errors_are_skipped(self):
        from unittest import mock

        from core.research.yhat_transformer import seq_dataset

        stock = {f"{i:06d}": _synth_bars(40, seed=i) for i in range(5)}
        real = seq_dataset.samples_from_bars

        def _boom(code, bars, **kwargs):
            if code == "000000":
                raise RuntimeError("bad bar")
            return real(code, bars, **kwargs)

        with mock.patch.object(seq_dataset, "samples_from_bars", side_effect=_boom):
            panel = seq_dataset.build_seq_panel(stock, head="oo", seq_len=8)
        self.assertGreater(panel.n, 0)
        self.assertNotIn("000000", panel.codes)

    def test_too_many_stock_errors_abort(self):
        from unittest import mock

        from core.research.yhat_transformer import seq_dataset

        stock = {f"{i:06d}": [] for i in range(5)}
        with mock.patch.object(
            seq_dataset, "samples_from_bars", side_effect=RuntimeError("bad")
        ):
            with self.assertRaises(RuntimeError):
                seq_dataset.build_seq_panel(stock, head="oo", seq_len=8)

    def test_factor_jobs_keep_going_after_one_failure(self):
        from core.research.yhat_transformer.factor_panel import _run_stock_jobs

        def _work(job):
            if job[0] == "0":
                raise RuntimeError("bad")
            return job[0]

        chunks = _run_stock_jobs(
            [(str(i),) for i in range(5)],
            _work,
            n_workers=1,
            progress_every=0,
            label="因子面板",
        )
        self.assertEqual(sorted(chunks), ["1", "2", "3", "4"])

    def test_factor_jobs_abort_when_most_fail(self):
        from core.research.yhat_transformer.factor_panel import _run_stock_jobs

        def _boom(job):
            raise RuntimeError(job[0])

        with self.assertRaises(RuntimeError):
            _run_stock_jobs(
                [(str(i),) for i in range(5)],
                _boom,
                n_workers=1,
                progress_every=0,
                label="因子面板",
            )

    def test_pit_and_build(self):
        from core.research.yhat_transformer.seq_dataset import (
            assert_pit_last_bar_before_decision,
            build_seq_panel,
            samples_from_bars,
        )

        bars = _synth_bars(60)
        X, gap, y, dates = samples_from_bars("000001", bars, seq_len=10, horizon_days=1)
        self.assertGreater(len(dates), 5)
        self.assertEqual(X.shape[-1], 16)
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
        self.assertEqual(panel.X.shape[-1], 16)

    def test_seq8_vs_seq16(self):
        from core.research.yhat_transformer.seq_dataset import (
            SEQ16_FEATURE_NAMES,
            SEQ32_FEATURE_NAMES,
            SEQ64_FEATURE_NAMES,
            SEQ8_FEATURE_NAMES,
            samples_from_bars,
        )

        bars = _synth_bars(40, seed=3)
        x8, _, _, _ = samples_from_bars(
            "000001", bars, seq_len=8, feat_set="seq8"
        )
        x16, _, _, d16 = samples_from_bars(
            "000001", bars, seq_len=8, feat_set="seq16"
        )
        self.assertEqual(len(SEQ8_FEATURE_NAMES), 8)
        self.assertEqual(len(SEQ16_FEATURE_NAMES), 16)
        self.assertEqual(len(SEQ32_FEATURE_NAMES), 32)
        self.assertEqual(len(SEQ64_FEATURE_NAMES), 64)
        self.assertEqual(SEQ16_FEATURE_NAMES[:8], SEQ8_FEATURE_NAMES)
        self.assertEqual(SEQ32_FEATURE_NAMES[:16], SEQ16_FEATURE_NAMES)
        self.assertEqual(SEQ64_FEATURE_NAMES[:32], SEQ32_FEATURE_NAMES)
        self.assertGreater(x8.shape[0], 0)
        self.assertEqual(x8.shape[-1], 8)
        self.assertEqual(x16.shape[-1], 16)
        np.testing.assert_allclose(x8, x16[:, :, :8], rtol=0, atol=1e-5)
        self.assertTrue(np.all(np.isfinite(x16)))
        x32, _, _, d32 = samples_from_bars(
            "000001", bars, seq_len=8, feat_set="seq32"
        )
        x64, _, _, d64 = samples_from_bars(
            "000001", bars, seq_len=8, feat_set="seq64"
        )
        self.assertEqual(x32.shape[-1], 32)
        self.assertEqual(x64.shape[-1], 64)
        self.assertGreater(x32.shape[0], 0)
        self.assertGreater(x64.shape[0], 0)
        # 嵌套前缀：更大档截断后等于更小档（同决策日）
        by16 = {d: i for i, d in enumerate(d16)}
        by32 = {d: i for i, d in enumerate(d32)}
        for i, d in enumerate(d32):
            np.testing.assert_allclose(x32[i, :, :16], x16[by16[d]], rtol=0, atol=1e-5)
        for i, d in enumerate(d64):
            np.testing.assert_allclose(x64[i, :, :32], x32[by32[d]], rtol=0, atol=1e-5)
        # 32/64 后半维不是前 16 的 lag 拷贝
        self.assertFalse(np.allclose(x32[:, :, 16:32], x32[:, :, :16]))
        self.assertFalse(np.allclose(x64[:, :, 32:48], x64[:, :, :16]))
        self.assertTrue(np.all(np.isfinite(x64)))
        # 各档样本量应一致（滚动不足时降级，不因 F 变大丢样本）
        self.assertEqual(x16.shape[0], x32.shape[0])
        self.assertEqual(x32.shape[0], x64.shape[0])


class TestListwiseGroups(unittest.TestCase):
    def test_subsample_keeps_whole_days(self):
        from collections import Counter

        from core.research.yhat_transformer.train import (
            _prepare_listwise_indices,
            _subsample_whole_groups,
        )

        dates = []
        for d in range(12):
            dates.extend([f"2024-01-{d + 1:02d}"] * 100)
        idx = list(range(len(dates)))
        kept = _subsample_whole_groups(dates, idx, max_rows=250, seed=0)
        counts = Counter(dates[i] for i in kept)
        self.assertTrue(counts)
        self.assertTrue(all(n == 100 for n in counts.values()))
        self.assertLessEqual(sum(counts.values()), 250)

        latest = _subsample_whole_groups(
            dates, idx, max_rows=250, seed=1, latest=True
        )
        last_days = sorted({dates[i] for i in latest})
        self.assertEqual(last_days[-1], "2024-01-12")

        tr, va = _prepare_listwise_indices(
            dates, idx, max_train_rows=250, seed=0
        )
        tr_counts = Counter(dates[i] for i in tr)
        va_counts = Counter(dates[i] for i in va)
        self.assertTrue(all(n == 100 for n in tr_counts.values()))
        self.assertTrue(all(n == 100 for n in va_counts.values()))
        self.assertTrue(set(tr_counts).isdisjoint(va_counts))
        self.assertIn("2024-01-12", va_counts)
        # val 不被 cap//4 抽样：完整验证日组保留
        self.assertGreater(sum(va_counts.values()), 250 // 4)


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_torch(), "torch missing")
class TestYhatModelSmoke(unittest.TestCase):
    def test_seq_factor_transformer_forward(self):
        import torch

        from core.research.yhat_transformer.model import (
            SeqFactorTransformer,
            SeqTransformer,
            count_parameters,
        )

        m = SeqTransformer(d_in=8, d_model=16, nhead=2, nlayers=1, dim_feedforward=32)
        self.assertGreater(count_parameters(m), 100)
        x = torch.randn(4, 8, 8)
        g = torch.randn(4)
        y = m(x, g)
        self.assertEqual(tuple(y.shape), (4,))
        model = SeqFactorTransformer(
            d_seq_in=8, d_factor=12, d_model=16, nhead=2, nlayers=1, factor_hidden=32
        )
        self.assertFalse(model.seq.with_head)
        self.assertFalse(hasattr(model.seq, "head"))
        out = model(x, g, torch.randn(4, 12))
        self.assertEqual(tuple(out.shape), (4,))
        with self.assertRaises(ValueError):
            SeqFactorTransformer(
                d_seq_in=8, d_factor=2, d_model=64, nhead=3, nlayers=1, factor_hidden=16
            )

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

    def test_listnet_relevance_keeps_cross_section_mass(self):
        import torch

        from core.research.yhat_transformer.train import (
            _listnet_loss,
            _listnet_relevance,
        )

        y = torch.zeros(40)
        y[0] = 10.0  # 涨停式离群
        y[1] = 1.0
        raw = torch.softmax(y, dim=0)
        rel = torch.softmax(_listnet_relevance(y), dim=0)
        self.assertGreater(float(raw[0]), 0.95)
        self.assertLess(float(rel[0]), 0.5)
        self.assertGreater(float(rel.min()), 1e-4)
        self.assertGreater(float(rel[0]), float(rel[1]))
        self.assertGreater(float(rel[1]), float(rel[2]))
        flat = _listnet_relevance(torch.ones(8) * 3.0)
        self.assertTrue(torch.allclose(flat, torch.zeros_like(flat), atol=1e-5))

        pred = torch.zeros(40, requires_grad=True)
        loss = _listnet_loss(pred, y)
        loss.backward()
        self.assertTrue(torch.isfinite(loss).item())
        self.assertIsNotNone(pred.grad)
        self.assertLess(float(pred.grad[0]), 0.0)
        self.assertGreater(float(pred.grad[2]), 0.0)


class TestArmLLiveAligned(unittest.TestCase):
    def test_regression_calls_fit_oo_lgb_matrix(self):
        from types import SimpleNamespace
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _run_arm_l

        n, f = 12, 3
        dates = [f"2024-01-{(i // 3) + 2:02d}" for i in range(n)]
        fac = SimpleNamespace(
            X=np.random.RandomState(0).randn(n, f).astype(np.float64),
            y=np.linspace(-0.02, 0.02, n).astype(np.float64),
            dates=dates,
            codes=[f"{i:06d}" for i in range(n)],
            feature_names=[f"f{j}" for j in range(f)],
            n=n,
        )
        blob = {
            "feature_names": list(fac.feature_names),
            "sample_count": 8,
        }
        train_idx = list(range(8))
        test_idx = list(range(8, 12))
        with mock.patch(
            "core.research.oo_lgb.fit_oo_lgb_matrix",
            return_value=(blob, {"sample_count": 8, "success": True}),
        ) as fit_m, mock.patch(
            "core.research.oo_lgb.predict_oo_lgb_matrix",
            return_value=[0.01, -0.02, 0.0, 0.03],
        ) as pred_m:
            out = _run_arm_l(
                fac, train_idx, test_idx, head_id="oo", rank=False
            )
        self.assertTrue(out.get("success"))
        self.assertTrue(out.get("live_aligned"))
        self.assertIn("L2 cube", str(out.get("note") or ""))
        self.assertEqual(out.get("max_train_cap"), 0)
        fit_m.assert_called_once()
        pred_m.assert_called_once()
        kw = fit_m.call_args.kwargs
        self.assertTrue(kw.get("cross_section_zscore"))
        self.assertEqual(list(kw.get("row_idx") or []), train_idx)
        cbs = kw.get("callbacks") or []
        self.assertEqual(len(cbs), 1)
        self.assertTrue(callable(cbs[0]))

    def test_arml_step_callback_prints(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace

        from scripts.run_yhat_transformer_holdout import _arml_boost_log_cb

        cb = _arml_boost_log_cb(period=30)
        env = SimpleNamespace(iteration=0, end_iteration=300)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cb(env)
            env.iteration = 1
            cb(env)
            env.iteration = 29
            cb(env)
        text = buf.getvalue()
        self.assertIn("ArmL step=1/300", text)
        self.assertIn("ArmL step=30/300", text)
        self.assertNotIn("ArmL step=2/300", text)

    def test_max_train_default_unlimited(self):
        import os
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _arm_l_max_train_rows

        self.assertEqual(_arm_l_max_train_rows(), 0)
        with mock.patch.dict(os.environ, {"YHAT_ARM_L_MAX_TRAIN": "0"}):
            self.assertEqual(_arm_l_max_train_rows(), 0)
        with mock.patch.dict(os.environ, {"YHAT_ARM_L_MAX_TRAIN": "8000"}):
            self.assertEqual(_arm_l_max_train_rows(), 8000)

    def test_arm_t_uses_l2_cube_cs_z(self):
        """因子支路与 L 对齐：全面板走 cs_z_matrix_prefer_cube，不再列标准化。"""
        from types import SimpleNamespace
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _run_arm_t

        n, f, L = 20, 4, 6
        dates = [f"2024-01-{(i // 4) + 2:02d}" for i in range(n)]
        rng = np.random.RandomState(1)
        seq = SimpleNamespace(
            X=rng.randn(n, L, 8).astype(np.float32),
            gap=rng.randn(n).astype(np.float32),
            y=rng.randn(n).astype(np.float32),
            dates=dates,
            codes=[f"{i:06d}" for i in range(n)],
            n=n,
        )
        fac = SimpleNamespace(
            X=rng.randn(n, f).astype(np.float64),
            y=seq.y.copy(),
            dates=dates,
            codes=list(seq.codes),
            feature_names=[f"f{j}" for j in range(f)],
            n=n,
        )
        z_out = (fac.X * 0.5).astype(np.float64)
        args = SimpleNamespace(
            d_model=16,
            nhead=2,
            nlayers=1,
            mlp_hidden=32,
            dropout=0.0,
            epochs=1,
            batch_size=8,
            lr=1e-3,
            device="cpu",
            patience=1,
            t_sample_frac=1.0,
            loss="smooth_l1",
        )
        fake_state = {"w": np.zeros(1)}
        fake_report = {
            "success": True,
            "n_params": 10,
            "Xseq": seq.X,
            "gap": seq.gap,
            "Xf": z_out.astype(np.float32),
            "y": seq.y,
            "dates": dates,
        }
        with mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            return_value=z_out,
        ) as csz_m, mock.patch(
            "core.research.yhat_transformer.train.train_seq_factor_transformer",
            return_value=(fake_state, dict(fake_report)),
        ) as train_m, mock.patch(
            "core.research.yhat_transformer.train.predict_seq_factor_transformer",
            return_value=np.zeros(4, dtype=np.float64),
        ):
            out = _run_arm_t(
                seq, fac, list(range(16)), list(range(16, 20)), args
            )
        self.assertTrue(out.get("success"))
        self.assertIn("L2 cube CS-z", str(out.get("note") or ""))
        csz_m.assert_called_once()
        csz_kw = csz_m.call_args
        self.assertEqual(csz_kw.kwargs.get("clock"), "open")
        self.assertEqual(list(csz_kw.args[1]), fac.feature_names)
        train_m.assert_called_once()
        fac_passed = train_m.call_args.args[1]
        self.assertEqual(fac_passed.dtype, np.float32)
        np.testing.assert_allclose(fac_passed, z_out.astype(np.float32), atol=1e-5)
        # 非有限值应填 0（与 rank L 后处理一致）
        z_nan = z_out.copy()
        z_nan[0, 0] = np.nan
        with mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            return_value=z_nan,
        ), mock.patch(
            "core.research.yhat_transformer.train.train_seq_factor_transformer",
            return_value=(fake_state, dict(fake_report)),
        ) as train_nan, mock.patch(
            "core.research.yhat_transformer.train.predict_seq_factor_transformer",
            return_value=np.zeros(4, dtype=np.float64),
        ):
            fac2 = SimpleNamespace(
                X=rng.randn(n, f).astype(np.float64),
                y=seq.y.copy(),
                dates=dates,
                codes=list(seq.codes),
                feature_names=[f"f{j}" for j in range(f)],
                n=n,
            )
            _run_arm_t(seq, fac2, list(range(16)), list(range(16, 20)), args)
        fac_nan = train_nan.call_args.args[1]
        self.assertTrue(np.isfinite(fac_nan).all())
        self.assertEqual(float(fac_nan[0, 0]), 0.0)

    def test_arm_t_cs_z_materialize_required(self):
        from types import SimpleNamespace
        from unittest import mock

        from core.research.materialize.consumer import MaterializeRequired
        from scripts.run_yhat_transformer_holdout import _run_arm_t

        n, f = 8, 2
        dates = ["2024-01-02"] * 4 + ["2024-01-03"] * 4
        fac = SimpleNamespace(
            X=np.ones((n, f), dtype=np.float64),
            y=np.zeros(n, dtype=np.float64),
            dates=dates,
            codes=[f"{i:06d}" for i in range(n)],
            feature_names=["a", "b"],
            n=n,
        )
        seq = SimpleNamespace(
            X=np.zeros((n, 4, 8), dtype=np.float32),
            gap=np.zeros(n, dtype=np.float32),
            y=np.zeros(n, dtype=np.float32),
            dates=dates,
            codes=list(fac.codes),
            n=n,
        )
        args = SimpleNamespace(
            d_model=8,
            nhead=2,
            nlayers=1,
            mlp_hidden=8,
            dropout=0.0,
            epochs=1,
            batch_size=4,
            lr=1e-3,
            device="cpu",
            patience=1,
            t_sample_frac=1.0,
            loss="smooth_l1",
        )
        with mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            side_effect=MaterializeRequired({"error": "缺 L2"}),
        ), mock.patch(
            "core.research.yhat_transformer.train.train_seq_factor_transformer"
        ) as train_m:
            out = _run_arm_t(seq, fac, list(range(4)), list(range(4, 8)), args)
        self.assertFalse(out.get("success"))
        self.assertIn("MaterializeRequired", str(out.get("error") or ""))
        train_m.assert_not_called()


if __name__ == "__main__":
    unittest.main()