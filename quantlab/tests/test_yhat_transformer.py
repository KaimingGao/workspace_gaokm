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
        self.assertEqual(oo_r.loss, "lambdarank")
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

    def test_open_tau_drops_minute_columns(self):
        from core.signal.factors.alpha_tc import (
            filter_open_tau_names,
            open_atc_full_names,
            open_tau_excluded_names,
            select_atc_core_names,
        )
        from core.signal.factors.z_tc import Z_TC_FIT_FEATURES, Z_TC_MINUTE_KEYS

        full = open_atc_full_names()
        opened = filter_open_tau_names(full)
        self.assertEqual(len(full), 247)
        self.assertEqual(len(opened), 218)
        self.assertEqual(len(select_atc_core_names(full)), 76)
        self.assertEqual(len(select_atc_core_names(opened)), 61)
        self.assertEqual(len(open_tau_excluded_names()), 29)
        for key in Z_TC_MINUTE_KEYS:
            self.assertIn(key, Z_TC_FIT_FEATURES)
            self.assertNotIn(key, opened)
        self.assertIn("z_τc_gap_pct", opened)
        self.assertIn("z_τc_tau_lag1", opened)
        self.assertIn("z_τc_tau_ma5", opened)
        self.assertNotIn("raw_atc_LOC_HL", opened)
        self.assertIn("raw_atc_GAP", opened)


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

    def test_reorder_panel_by_codes_matches_item_order(self):
        from core.research.yhat_transformer.factor_panel import (
            FactorPanel,
            _cache_panel_matches_pool,
            reorder_panel_by_codes,
        )

        # 完成序：B 在前；入池序 A→B 时应重排。
        panel = FactorPanel(
            X=np.asarray([[1.0], [2.0], [3.0], [4.0]], dtype=np.float32),
            y=np.asarray([10.0, 20.0, 30.0, 40.0], dtype=np.float32),
            dates=["2024-01-02", "2024-01-01", "2024-01-02", "2024-01-01"],
            codes=["B", "B", "A", "A"],
            feature_names=["f0"],
        )
        out = reorder_panel_by_codes(panel, ["A", "B"])
        self.assertEqual(out.codes, ["A", "A", "B", "B"])
        self.assertEqual(out.dates, ["2024-01-01", "2024-01-02", "2024-01-01", "2024-01-02"])
        np.testing.assert_allclose(out.y, [40.0, 30.0, 20.0, 10.0])
        np.testing.assert_allclose(out.X[:, 0], [4.0, 3.0, 2.0, 1.0])
        # 入池外残留票：校验失败；重排后丢掉。
        orphan = FactorPanel(
            X=np.asarray([[1.0], [2.0], [3.0]], dtype=np.float32),
            y=np.asarray([1.0, 2.0, 3.0], dtype=np.float32),
            dates=["2024-01-01", "2024-01-01", "2024-01-01"],
            codes=["A", "B", "Z"],
            feature_names=["f0"],
        )
        self.assertFalse(_cache_panel_matches_pool(orphan, ["A", "B"]))
        trimmed = reorder_panel_by_codes(orphan, ["A", "B"])
        self.assertEqual(trimmed.codes, ["A", "B"])
        self.assertTrue(_cache_panel_matches_pool(trimmed, ["A", "B"]))
        none = reorder_panel_by_codes(orphan, ["MISSING"])
        self.assertEqual(none.n, 0)
        self.assertEqual(none.codes, [])

    def test_build_oo_factor_panel_uses_hub_assemble(self):
        from unittest import mock

        from core.research.yhat_transformer.factor_panel import build_oo_factor_panel

        x = np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64)
        y = np.asarray([0.1, 0.2], dtype=np.float64)
        with mock.patch(
            "core.research.oo_model_compact.assemble_oo_panels",
            return_value=(x, y, ["a", "b"], ["2024-01-02", "2024-01-03"], ["000001", "000002"]),
        ) as assem:
            panel = build_oo_factor_panel(
                {"000002": [{"date": "2024-01-01"}], "000001": [{"date": "2024-01-01"}]},
                horizon_days=1,
                use_cache=False,
            )
        assem.assert_called_once()
        kwargs = assem.call_args.kwargs
        self.assertTrue(kwargs.get("return_codes"))
        self.assertFalse(kwargs.get("aoo_core_only"))
        self.assertEqual(panel.n, 2)
        self.assertEqual(panel.codes, ["000001", "000002"])
        self.assertEqual(panel.feature_names, ["a", "b"])
        self.assertEqual(panel.X.dtype, np.float32)


    def test_factor_cache_key_tracks_prices(self):
        from core.research.yhat_transformer.factor_panel import _cache_key

        bars = _synth_bars(6, seed=2)
        base = _cache_key([("000001", bars)], horizon_days=1, kind="oo")
        revised = [dict(b) for b in bars]
        revised[2] = dict(revised[2])
        revised[2]["close"] = float(revised[2]["close"]) + 1.0
        self.assertNotEqual(
            _cache_key([("000001", revised)], horizon_days=1, kind="oo"),
            base,
        )
        self.assertNotEqual(
            _cache_key([("000001", bars)], horizon_days=0, kind="tc_compact"),
            _cache_key([("000001", bars)], horizon_days=0, kind="oo"),
        )

    def test_build_tc_factor_panel_uses_hub_compact(self):
        from unittest import mock

        from core.research.yhat_transformer.factor_panel import build_tc_factor_panel

        bars = {"000001": [{"date": "2024-01-02", "close": 1.0}], "000002": [{"date": "2024-01-03", "close": 2.0}]}
        x = np.asarray([[0.1, 1.0], [0.2, 2.0]], dtype=np.float64)
        metas = [
            {"stock_code": "000002", "date": "2024-01-03"},
            {"stock_code": "000001", "date": "2024-01-02"},
        ]

        def _collect(items, names, **kwargs):
            for it in items:
                it.pop("bars", None)
            self.assertEqual(kwargs.get("cache_head"), "tc")
            self.assertEqual(kwargs.get("tau_hm"), "open")
            self.assertTrue(kwargs.get("include_alpha_tc"))
            self.assertIn("z_τc_gap_pct", list(names))
            return (
                x,
                ["z_τc_gap_pct", "raw_atc_a"],
                [0.2, 0.1],
                ["2024-01-03", "2024-01-02"],
                metas,
                2,
            )

        with mock.patch(
            "core.research.panel_matrix.collect_tau_compact",
            side_effect=_collect,
        ) as col:
            panel = build_tc_factor_panel(bars, use_cache=False)
        col.assert_called_once()
        self.assertEqual(panel.codes, ["000001", "000002"])
        self.assertEqual(panel.dates, ["2024-01-02", "2024-01-03"])
        self.assertEqual(panel.X.dtype, np.float32)
        self.assertEqual(bars["000001"][0]["close"], 1.0)
        self.assertEqual(len(bars["000001"]), 1)

    def test_build_tc_factor_panel_rejects_misaligned_compact(self):
        from unittest import mock

        from core.research.yhat_transformer.factor_panel import build_tc_factor_panel

        x = np.ones((2, 1), dtype=np.float32)

        def _collect(items, names, **kwargs):
            return (x, ["z_τc_gap_pct"], [0.1, 0.2], ["2024-01-02"], [{"stock_code": "000001"}], 1)

        with mock.patch("core.research.panel_matrix.collect_tau_compact", side_effect=_collect):
            panel = build_tc_factor_panel(
                {"000001": [{"date": "2024-01-02", "close": 1.0}]},
                use_cache=False,
            )
        self.assertEqual(panel.n, 0)

    def test_cache_load_rejects_misaligned_rows(self):
        import os
        import tempfile

        from core.research.yhat_transformer.factor_panel import _try_load_cache

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "bad.npz")
            np.savez_compressed(
                path,
                X=np.ones((2, 1), dtype=np.float32),
                y=np.ones((2,), dtype=np.float32),
                dates=np.asarray(["2024-01-02"], dtype=object),
                codes=np.asarray(["000001", "000002"], dtype=object),
                feature_names=np.asarray(["z_τc_gap_pct"], dtype=object),
            )
            self.assertIsNone(_try_load_cache(path))

    def test_price_seq_names_are_retired(self):
        from core.research.yhat_transformer.seq_dataset import resolve_seq_feat_set

        self.assertEqual(resolve_seq_feat_set("core"), "seq_core")
        self.assertEqual(resolve_seq_feat_set("full"), "seq_full")
        for legacy in ("seq8", "seq16", "seq32", "seq64"):
            with self.assertRaises(ValueError):
                resolve_seq_feat_set(legacy)

    def test_factor_seq_window_matches_factor_width(self):
        from core.research.yhat_transformer.factor_panel import (
            FactorPanel,
            sequence_from_factor_panel,
        )

        bars = _synth_bars(12, seed=1)
        dates = [b["date"] for b in bars]
        width = 3
        raw = np.arange(len(dates) * width, dtype=np.float32).reshape(len(dates), width)
        raw[2, 1] = np.nan
        fac = FactorPanel(
            X=raw,
            y=np.arange(len(dates), dtype=np.float32),
            dates=list(dates),
            codes=["000001"] * len(dates),
            feature_names=["c0", "c1", "c2"],
        )
        seq = sequence_from_factor_panel(fac, {"000001": bars}, seq_len=5)
        self.assertEqual(seq.X.shape, (len(dates) - 4, 5, width + 1))
        self.assertEqual(seq.seq_len, 5)
        self.assertEqual(seq.feature_names, ("c0", "c1", "c2", "age_td"))
        self.assertEqual(seq.dates[0], dates[4])
        self.assertTrue(np.isnan(seq.X[0, 2, 1]))
        np.testing.assert_allclose(seq.X[0, :, :width], raw[:5], equal_nan=True)
        self.assertAlmostEqual(float(seq.X[0, -1, -1]), 0.0, places=5)
        self.assertAlmostEqual(float(seq.X[0, -2, -1]), 1.0 / 4.0, places=5)
        self.assertAlmostEqual(float(seq.X[0, 0, -1]), 1.0, places=5)
        np.testing.assert_allclose(seq.X[0, -1, :width], raw[4], equal_nan=True)
        expanded = int(np.prod(seq.X.shape)) * 4
        self.assertLess(seq.X.nbytes, expanded)
        sub = seq.subset([0])
        self.assertIs(sub.X.values, seq.X.values)
        self.assertEqual(sub.X.shape[0], 1)

    def test_window_preprocess_matches_dense(self):
        from core.research.yhat_transformer.factor_panel import (
            FactorPanel,
            sequence_from_factor_panel,
        )
        from core.research.yhat_transformer.seq_dataset import FactorWindowView
        from core.research.yhat_transformer.train import (
            _clip_inplace_rows,
            _impute_seq_median,
        )

        bars = _synth_bars(12, seed=3)
        dates = [b["date"] for b in bars]
        width = 3
        raw = np.arange(len(dates) * width, dtype=np.float32).reshape(len(dates), width)
        raw[2, 1] = np.nan
        raw[4, 0] = 80.0
        fac = FactorPanel(
            X=raw,
            y=np.arange(len(dates), dtype=np.float32),
            dates=list(dates),
            codes=["000001"] * len(dates),
            feature_names=["c0", "c1", "c2"],
        )
        seq = sequence_from_factor_panel(fac, {"000001": bars}, seq_len=5)
        self.assertIsInstance(seq.X, FactorWindowView)
        rows = list(range(seq.n))
        dense = np.array(seq.X, dtype=np.float32, copy=True)
        gap = np.array(seq.gap, dtype=np.float32, copy=True)
        gap_dense = gap.copy()
        med_w = _impute_seq_median(seq.X, rows, seed=1)
        med_d = _impute_seq_median(dense, rows, seed=1)
        self.assertEqual(med_w, med_d)
        np.testing.assert_allclose(
            seq.X.gather(np.arange(seq.n)), dense, equal_nan=True
        )
        clip_w = _clip_inplace_rows(seq.X, gap, rows, seed=1)
        clip_d = _clip_inplace_rows(dense, gap_dense, rows, seed=1)
        self.assertEqual(clip_w["x_lo"], clip_d["x_lo"])
        self.assertEqual(clip_w["x_hi"], clip_d["x_hi"])
        np.testing.assert_allclose(seq.X.gather(np.arange(seq.n)), dense)
        np.testing.assert_allclose(gap, gap_dense)
        huge = FactorWindowView(
            np.zeros((4, 2), dtype=np.float32),
            np.zeros((80_000, 20), dtype=np.int32),
            np.zeros((80_000, 20), dtype=np.float32),
            ("a", "b", "age_td"),
        )
        with self.assertRaises(MemoryError):
            np.asarray(huge)



class TestRankDayGroups(unittest.TestCase):
    def test_subsample_keeps_whole_days(self):
        from collections import Counter

        from core.research.yhat_transformer.train import _subsample_whole_groups

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

    def test_subsample_stops_instead_of_taking_thinner_days(self):
        from unittest import mock

        from core.research.yhat_transformer.train import _subsample_whole_groups

        dates = ["2024-01-01"] * 40 + ["2024-01-02"] * 40 + ["2024-01-03"] * 10
        idx = list(range(len(dates)))

        class _Rng:
            def shuffle(self, order):
                order[:] = [0, 1, 2]

        with mock.patch(
            "core.research.yhat_transformer.train.np.random.RandomState",
            return_value=_Rng(),
        ):
            kept = _subsample_whole_groups(dates, idx, max_rows=50, seed=0)
        days = {dates[i] for i in kept}
        self.assertEqual(days, {"2024-01-01"})
        self.assertEqual(len(kept), 40)


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_torch(), "torch missing")
class TestYhatModelSmoke(unittest.TestCase):
    def test_arm_t_model_forward(self):
        import torch

        from core.research.yhat_transformer.model import (
            ArmTModel,
            SeqTransformer,
            count_parameters,
        )

        m = SeqTransformer(d_in=8, d_model=16, nhead=2, nlayers=1, dim_feedforward=32)
        self.assertGreater(count_parameters(m), 100)
        x = torch.randn(4, 8, 8)
        self.assertEqual(tuple(m.encode(x).shape), (4, 16))
        model = ArmTModel(
            d_seq_in=8, d_model=16, nhead=2, nlayers=1
        )
        g = torch.randn(4)
        out = model(x, g)
        self.assertEqual(tuple(out.shape), (4,))
        with self.assertRaises(ValueError):
            ArmTModel(d_seq_in=8, d_model=64, nhead=3, nlayers=1)

    def test_sequence_order_changes_output(self):
        import torch

        from core.research.yhat_transformer.model import ArmTModel

        torch.manual_seed(0)
        x = torch.randn(2, 6, 4)
        g = torch.randn(2)
        model = ArmTModel(
            d_seq_in=4, d_model=16, nhead=2, nlayers=1
        ).eval()
        y = model(x, g)
        order = [1, 3, 0, 2, 4, 5]
        self.assertFalse(torch.allclose(y, model(x[:, order, :], g), atol=1e-4))

    def test_arm_t_holdout_each_epoch_does_not_pick_weights(self):
        from core.research.yhat_transformer.seq_dataset import SeqPanel
        from core.research.yhat_transformer.train import train_arm_t

        rng = np.random.RandomState(0)
        dates: list = []
        codes: list = []
        for d in range(11):
            for c in range(6):
                dates.append(f"2024-01-{d + 1:02d}")
                codes.append(f"{c:06d}")
        n = len(dates)
        panel = SeqPanel(
            X=rng.randn(n, 4, 4).astype(np.float32),
            gap=rng.randn(n).astype(np.float32),
            y=rng.randn(n).astype(np.float32) * 2.0,
            dates=dates,
            codes=codes,
            seq_len=4,
            feature_names=("a", "b", "c", "d"),
        )
        train_idx = list(range(42))
        holdout_idx = list(range(42, 48))
        _state, report = train_arm_t(
            panel,
            train_idx,
            d_model=8,
            nhead=2,
            nlayers=1,
            epochs=2,
            batch_size=16,
            seed=0,
            holdout_idx=holdout_idx,
        )
        rows = report.get("holdout_by_epoch") or []
        self.assertEqual([r["epoch"] for r in rows], [1, 2])
        self.assertTrue(all(int(r["n"]) == len(holdout_idx) for r in rows))
        self.assertTrue(all(r.get("cs_top_mean_y") is not None for r in rows))
        self.assertNotIn("probe_by_epoch", report)
        self.assertNotIn("n_val", report)
        self.assertNotIn("best_val_loss", report)
        self.assertEqual(report.get("n_train"), len(train_idx))
        self.assertNotIn("arch", report)
        self.assertNotIn("false_open_weight", report)
        self.assertNotIn("missed_up_weight", report)
        self.assertTrue(all("cs_icir" in r and "cs_rank_icir" in r for r in rows))

    def test_configure_torch_threads_default_four(self):
        import os
        from unittest import mock

        import torch

        from core.research.yhat_transformer.train import _configure_torch_threads

        prev = torch.get_num_threads()
        try:
            with mock.patch.dict(os.environ, {"TORCH_NUM_THREADS": "4"}):
                self.assertEqual(_configure_torch_threads(), 4)
                self.assertEqual(torch.get_num_threads(), 4)
            with mock.patch.dict(os.environ, {"TORCH_NUM_THREADS": "1"}):
                self.assertEqual(_configure_torch_threads(), 1)
                self.assertEqual(torch.get_num_threads(), 1)
            with mock.patch.dict(os.environ, {"TORCH_NUM_THREADS": "nope"}):
                self.assertEqual(_configure_torch_threads(), 4)
                self.assertEqual(torch.get_num_threads(), 4)
        finally:
            torch.set_num_threads(max(1, int(prev)))

    def test_lambdarank_pairwise_pushes_inverted_head(self):
        import torch

        from core.research.yhat_transformer.train import (
            _lambdarank_group_loss,
            _resolve_loss,
        )

        self.assertEqual(_resolve_loss("lambdarank"), "lambdarank")
        self.assertEqual(_resolve_loss("mse"), "mse")
        self.assertEqual(_resolve_loss("l2"), "mse")
        self.assertEqual(_resolve_loss("huber"), "mse")
        self.assertEqual(_resolve_loss("smooth_l1"), "mse")
        y = torch.tensor([0.0, 1.0, 3.0, 8.0])
        aligned = torch.tensor([0.0, 1.0, 2.0, 3.0], requires_grad=True)
        flipped = torch.tensor([3.0, 2.0, 1.0, 0.0], requires_grad=True)
        loss_ok = _lambdarank_group_loss(aligned, y)
        loss_bad = _lambdarank_group_loss(flipped, y)
        self.assertLess(float(loss_ok.detach()), float(loss_bad.detach()))
        shifted = _lambdarank_group_loss(flipped.detach() + 5.0, y)
        self.assertTrue(torch.allclose(shifted, loss_bad.detach(), atol=1e-5))
        loss_bad.backward()
        self.assertIsNotNone(flipped.grad)
        # y 最高的名字当前分数最低，梯度应为负，下降才会把它抬上去。
        self.assertLess(float(flipped.grad[-1]), 0.0)
        self.assertGreater(float(flipped.grad[0]), 0.0)

        # 截断只留下当前第 1 名和其余名字的对。把第 1 名换成别的人，损失必须变。
        wide_y = torch.arange(6, dtype=torch.float32)
        top = torch.tensor([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        swapped = top.clone()
        swapped[0], swapped[5] = top[5], top[0]
        full = _lambdarank_group_loss(swapped, wide_y, truncation=100)
        head = _lambdarank_group_loss(swapped, wide_y, truncation=1)
        self.assertGreater(float(full), 0.0)
        self.assertGreater(float(head), 0.0)
        self.assertFalse(torch.allclose(full, head, atol=1e-5))

        # 反方向的大间隔保持梯度，不小于贴在一起的逆序。
        y3 = torch.tensor([0.0, 1.0, 2.0])
        close = torch.tensor([1.0, 0.0, 2.0], requires_grad=True)
        far = torch.tensor([50.0, -50.0, 2.0], requires_grad=True)
        _lambdarank_group_loss(close, y3).backward()
        _lambdarank_group_loss(far, y3).backward()
        self.assertGreaterEqual(
            float(far.grad[0].abs()), float(close.grad[0].abs()) * 0.5
        )

    def test_lambdarank_large_cross_section_keeps_gradient(self):
        """A 股一日 ~1500 只时，loss 和分数梯度不能被 pair 平均打成 0。"""
        import torch

        from core.research.yhat_transformer.train import _lambdarank_group_loss

        n = 1500
        y = torch.arange(n, dtype=torch.float32)
        g = torch.Generator().manual_seed(0)
        pred = (torch.randn(n, generator=g) * 0.1).requires_grad_(True)
        loss = _lambdarank_group_loss(pred, y)
        self.assertGreater(float(loss.detach()), 1e-2)
        loss.backward()
        self.assertGreater(float(pred.grad.abs().mean()), 1e-4)
        # 高名次目前分数偏低时，梯度应把它往上推。
        worst = int(torch.argmin(pred.detach()).item())
        if float(y[worst]) > float(y.median()):
            self.assertLess(float(pred.grad[worst]), 0.0)

    def test_regression_loss_is_mse(self):
        """回归头是逐点 MSE。"""
        import torch

        from core.research.yhat_transformer.train import _regression_batch_loss

        y = torch.tensor([-0.8, 0.5, 0.4, -0.3, 12.0])
        pred = torch.tensor([0.4, 0.3, -0.2, -0.5, 0.0], requires_grad=True)
        mixed = _regression_batch_loss(pred, y)
        plain = torch.nn.functional.mse_loss(pred, y)
        self.assertTrue(torch.allclose(mixed, plain))
        empty = torch.empty(0)
        self.assertEqual(float(_regression_batch_loss(empty, empty)), 0.0)

    def test_bias_and_layernorm_skip_weight_decay(self):
        """bias 与 LayerNorm.weight 不衰减，其余参数衰减 5e-3。"""
        import torch

        from core.research.yhat_transformer.model import ArmTModel
        from core.research.yhat_transformer.train import _WEIGHT_DECAY, _adamw

        model = ArmTModel(
            d_seq_in=4,
            d_model=8,
            nhead=2,
            nlayers=1,
            dropout=0.0,
        )
        self.assertEqual(model.seq.encoder.layers[0].linear1.out_features, 64)
        opt = _adamw(model, 1e-3)
        decay_groups = [g for g in opt.param_groups if g["weight_decay"] > 0]
        free_groups = [g for g in opt.param_groups if g["weight_decay"] == 0]
        self.assertEqual(len(decay_groups), 1)
        self.assertEqual(decay_groups[0]["weight_decay"], _WEIGHT_DECAY)
        decay = {id(p) for p in decay_groups[0]["params"]}
        free = {id(p) for g in free_groups for p in g["params"]}
        self.assertEqual(_WEIGHT_DECAY, 5e-3)
        for name, param in model.named_parameters():
            leaf = name.rsplit(".", 1)[-1]
            owner = dict(model.named_modules()).get(name.rsplit(".", 1)[0], model)
            skip = leaf == "bias" or (
                leaf == "weight" and isinstance(owner, torch.nn.LayerNorm)
            )
            self.assertEqual(id(param) in free, skip, msg=name)
            self.assertEqual(id(param) in decay, not skip, msg=name)

    def test_rank_loss_is_order_only(self):
        """排序损失只看序。整段平移不改变损失。"""
        import torch

        from core.research.yhat_transformer.train import _rank_batch_loss

        dates = ["2024-01-02"] * 4
        y = torch.tensor([0.0, 1.0, 2.0, 3.0])
        sunk = torch.tensor([-5.0, -4.0, -3.0, -2.0])
        order_only = _rank_batch_loss(sunk, y, dates)
        shifted = _rank_batch_loss(sunk + 7.0, y, dates)
        self.assertTrue(torch.allclose(order_only, shifted, atol=1e-5))

    def test_mean_daily_ndcg_perfect_order(self):
        from core.research.yhat_transformer.metrics import mean_daily_ndcg

        metas = [{"date": "2024-01-02", "code": f"{i:06d}"} for i in range(4)]
        metas += [{"date": "2024-01-03", "code": f"{i:06d}"} for i in range(4)]
        ys = [0.0, 1.0, 2.0, 3.0, 0.0, 1.0, 2.0, 3.0]
        perfect = mean_daily_ndcg(ys, ys, metas, k=100)
        flipped = mean_daily_ndcg([3, 2, 1, 0, 3, 2, 1, 0], ys, metas, k=100)
        self.assertAlmostEqual(float(perfect), 1.0, places=6)
        self.assertLess(float(flipped), float(perfect))


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
        self.assertIsNone(out.get("theme_boost"))

    def test_tc_uses_theme_weights_and_hub_columns(self):
        from types import SimpleNamespace
        from unittest import mock

        from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS
        from scripts.run_yhat_transformer_holdout import _run_arm_l

        minute = Z_TC_MINUTE_KEYS[0]
        n = 12
        # 截面列由 collect_tau_compact 写入，拟合只过滤，不再补广度。
        names = [
            "z_τc_gap_pct",
            minute,
            "raw_atc_x",
            "open_gap",
            "z_τc_theme_day",
            "z_τc_sector_gap_breadth",
            "z_τc_gap_vs_sector",
        ]
        x = np.ones((n, len(names)), dtype=np.float64)
        x[:, 0] = 0.1
        x[:6, 0] = 3.0
        x[:, 2] = np.nan
        x[:6, 2] = 0.2
        x[:, 4] = 0.0
        x[:6, 4] = 1.0
        x[:, 5] = 0.25
        x[:, 6] = 0.1
        dates = ["2024-01-02"] * 6 + ["2024-01-03"] * 6
        fac = SimpleNamespace(
            X=x,
            y=np.linspace(-0.02, 0.02, n),
            dates=dates,
            codes=[f"{i % 6:06d}" for i in range(n)],
            feature_names=names,
            n=n,
        )
        seen = {}

        def _fit(x_fit, y, feat, **kw):
            seen["feat"] = list(feat)
            w = kw.get("sample_weights")
            seen["w"] = [] if w is None else [float(v) for v in np.asarray(w)]
            return (
                {"feature_names": list(feat), "sample_count": 8},
                {"sample_count": 8, "success": True},
            )

        with mock.patch(
            "core.research.tc_lgb.fit_tc_lgb_matrix", side_effect=_fit
        ), mock.patch(
            "core.research.tc_lgb.predict_tc_lgb_matrix",
            return_value=[0.01, -0.02, 0.0, 0.03, 0.01, -0.01],
        ):
            out = _run_arm_l(
                fac, list(range(6)), list(range(6, 12)), head_id="tc", rank=False
            )
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("theme_boost"), 1.5)
        for key in (
            "z_τc_gap_pct",
            "z_τc_theme_day",
            "z_τc_sector_gap_breadth",
            "z_τc_gap_vs_sector",
            "raw_atc_x",
        ):
            self.assertIn(key, seen["feat"])
        self.assertNotIn(minute, seen["feat"])
        self.assertNotIn("open_gap", seen["feat"])
        self.assertEqual(seen["w"][0], 1.5)
        self.assertEqual(seen["w"][6], 1.0)

    def test_tc_hub_columns_rejects_width_mismatch(self):
        from scripts.run_yhat_transformer_holdout import (
            _tc_hub_columns,
            _theme_weights_from_panel,
        )

        x = np.ones((3, 1), dtype=np.float32)
        out, names = _tc_hub_columns(x, ["z_τc_gap_pct", "raw_atc_x"], [0])
        self.assertEqual(names, [])
        self.assertEqual(int(out.shape[1]), 0)
        self.assertIsNone(_theme_weights_from_panel(x, ["z_τc_theme_day", "z_τc_gap_pct"]))

    def test_tc_stops_before_lgb_when_memory_guard_trips(self):
        from types import SimpleNamespace
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _run_arm_l

        n = 12
        names = ["gap_pct", "raw_atc_x"]
        x = np.ones((n, len(names)), dtype=np.float32)
        x[:6, 0] = 3.0
        fac = SimpleNamespace(
            X=x,
            y=np.linspace(-0.02, 0.02, n),
            dates=["2024-01-02"] * 6 + ["2024-01-03"] * 6,
            codes=[f"{i % 6:06d}" for i in range(n)],
            feature_names=names,
            n=n,
        )
        with mock.patch(
            "core.research.horizon_l1.confirm_fit_memory_stop",
            return_value="内存保护：进程已用约 6.0 GB",
        ), mock.patch(
            "core.research.tc_lgb.fit_tc_lgb_matrix",
        ) as fit_m:
            out = _run_arm_l(
                fac, list(range(6)), list(range(6, 12)), head_id="tc", rank=False
            )
        self.assertFalse(out.get("success"))
        self.assertIn("内存保护", str(out.get("error") or ""))
        fit_m.assert_not_called()
        self.assertEqual(int(fac.X.shape[1]), 0)

    def test_theme_weights_skip_dict_per_row(self):
        from scripts.run_yhat_transformer_holdout import _theme_weights_from_panel

        col = np.array([[1.0], [0.0], [np.nan]], dtype=np.float32)
        weights = _theme_weights_from_panel(col, ["z_τc_theme_day"])
        self.assertEqual(list(weights), [1.5, 1.0, 1.0])
        self.assertIsNone(
            _theme_weights_from_panel(np.zeros((4, 1), dtype=np.float32), ["theme_day"])
        )
        self.assertIsNone(_theme_weights_from_panel(col, ["gap_pct"]))

    def test_raw_atc_finite_scans_train_rows_only(self):
        from core.research.panel_matrix import raw_atc_finite

        x = np.array(
            [
                [1.0, np.nan, 0.0],
                [np.nan, np.nan, 0.0],
                [np.nan, 2.0, 0.0],
            ],
            dtype=np.float32,
        )
        names = ["raw_atc_a", "raw_atc_b", "gap_pct"]
        self.assertEqual(raw_atc_finite(x, names, [0, 1]), ["raw_atc_a"])
        self.assertEqual(raw_atc_finite(x, names, [2]), ["raw_atc_b"])
        self.assertEqual(raw_atc_finite(x, names), ["raw_atc_a", "raw_atc_b"])

    def test_rank_calls_fit_lambdarank(self):
        from types import SimpleNamespace
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _run_arm_l

        n = 16
        dates = ["2024-01-02"] * 8 + ["2024-01-03"] * 8
        fac = SimpleNamespace(
            X=np.random.RandomState(0).randn(n, 3),
            y=np.linspace(-1, 1, n),
            dates=dates,
            codes=[f"{i:06d}" for i in range(n)],
            feature_names=["a", "b", "c"],
            n=n,
        )
        with mock.patch(
            "core.research.oo_rank_lambdarank.fit_lambdarank",
            return_value={"success": True, "n_groups": 1, "sample_count": 8},
        ) as fit_m, mock.patch(
            "core.research.oo_rank_lambdarank._predict_oo_rank_matrix",
            return_value=[0.1] * 8,
        ):
            out = _run_arm_l(
                fac,
                list(range(8)),
                list(range(8, 16)),
                head_id="oo_rank",
                rank=True,
            )
        self.assertTrue(out.get("success"))
        self.assertIn("fit_lambdarank", str(out.get("note") or ""))
        self.assertEqual(out.get("n_groups"), 1)
        fit_m.assert_called_once()
        days = fit_m.call_args.args[0]
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0].get("clock"), "open")
        kw = fit_m.call_args.kwargs
        self.assertEqual(kw.get("feature_names"), ["a", "b", "c"])
        self.assertEqual(kw.get("n_estimators"), 120)
        self.assertEqual(kw.get("max_depth"), 5)
        self.assertEqual(kw.get("num_leaves"), 32)
        self.assertEqual(kw.get("subsample"), 0.85)
        self.assertEqual(kw.get("colsample_bytree"), 0.9)
        self.assertEqual(kw.get("l2"), 1.0)
        self.assertEqual(kw.get("truncation_level"), 30)

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

    def test_holdout_zero_is_full_sample_train(self):
        from scripts.run_yhat_transformer_holdout import _holdout_is_full_sample

        self.assertTrue(_holdout_is_full_sample(0))
        self.assertTrue(_holdout_is_full_sample("0"))
        self.assertFalse(_holdout_is_full_sample(5))
        self.assertFalse(_holdout_is_full_sample(None))

    def test_arm_r_full_sample_trains_without_eval_window(self):
        """执行套 Holdout=0：评估窗为空仍要训练并落权重，不能报 empty holdout。"""
        import time
        from types import SimpleNamespace
        from unittest import mock

        from core.research.yhat_tf_desk import _metrics_from_holdout_summary
        from scripts.run_yhat_transformer_holdout import (
            _fit_arm_r_like_hub,
            _train_despite_empty_holdout,
        )

        self.assertTrue(_train_despite_empty_holdout(10, 0, 0))
        self.assertFalse(_train_despite_empty_holdout(10, 0, 20))
        self.assertFalse(_train_despite_empty_holdout(0, 0, 0))
        self.assertFalse(_train_despite_empty_holdout(10, 3, 0))

        class _Model:
            coefficients = {"z_a": 0.25}
            intercept = 0.01

        def _fit(*_a, **_k):
            return _Model(), {
                "success": True,
                "sample_count": 40,
                "active_features": ["z_a"],
                "excluded_features": [],
            }

        saved: dict = {}

        def _save(blob, meta):
            saved["blob"] = blob
            saved["meta"] = meta

        fac = SimpleNamespace(codes=["600000"], dates=["2024-01-02"])
        with mock.patch(
            "core.research.oo_model_compact.fit_oo_ridge_matrix", _fit
        ), mock.patch(
            "core.research.oo_model_compact.predict_oo_matrix",
            side_effect=AssertionError("empty holdout must not predict"),
        ), mock.patch(
            "scripts.run_yhat_transformer_holdout._try_save_ridge", _save
        ):
            out = _fit_arm_r_like_hub(
                fac,
                np.zeros((3, 1)),
                np.zeros(3),
                ["z_a"],
                ["2024-01-02", "2024-01-03", "2024-01-04"],
                [0, 1, 2],
                [],
                hid="oo",
                ridge_alpha=1.0,
                t0=time.time(),
            )
        self.assertTrue(out["success"])
        self.assertTrue(out["full_sample"])
        self.assertEqual(out["n_test"], 0)
        self.assertEqual(out["n_train"], 40)
        self.assertEqual(saved["blob"]["feature_names"], ["z_a"])
        self.assertAlmostEqual(saved["blob"]["intercept"], 0.01)
        self.assertIsNone(saved["meta"]["eval_start"])

        saved.clear()

        def _predict_miss(*_a, **_k):
            return [None]

        with mock.patch(
            "core.research.oo_model_compact.fit_oo_ridge_matrix", _fit
        ), mock.patch(
            "core.research.oo_model_compact.predict_oo_matrix", _predict_miss
        ), mock.patch(
            "scripts.run_yhat_transformer_holdout._try_save_ridge", _save
        ):
            missed = _fit_arm_r_like_hub(
                fac,
                np.zeros((3, 1)),
                np.zeros(3),
                ["z_a"],
                ["2024-01-02", "2024-01-03", "2024-01-04"],
                [0, 1, 2],
                [0],
                hid="oo",
                ridge_alpha=1.0,
                t0=time.time(),
            )
        self.assertFalse(missed["success"])
        self.assertIn("holdout preds empty", missed["error"])
        self.assertFalse(saved)

        metrics = _metrics_from_holdout_summary(
            {
                "factory": "R",
                "arm_R": out,
                "split_R": {"n_train": 40, "n_test": 0, "fit_end": "2026-11-19"},
            }
        )
        self.assertEqual(metrics["arms"]["R"]["n_train"], 40)
        self.assertEqual(metrics["arms"]["R"]["n_test"], 0)

    def test_hub_live_note_forces_holdout_zero(self):
        from unittest import mock

        from web.routers.quant_yhat_tf import YhatFactoryFitBody, _fit

        seen = {}

        def _capture(head, params, factory=None, suite=None):
            seen["head"] = head
            seen["params"] = dict(params)
            seen["factory"] = factory
            seen["suite"] = suite
            return {"success": True, "ok": True}

        body = YhatFactoryFitBody(
            head="tc_rank",
            params={"holdout_trading_days": 5, "epochs": 2},
            note="hub arm-t live",
        )
        with mock.patch("web.routers.quant_yhat_tf.start_holdout_fit", _capture):
            out = _fit("T", body)
        self.assertTrue(out.get("success"))
        self.assertEqual(seen["params"]["holdout_trading_days"], 0)
        self.assertEqual(seen["params"]["epochs"], 2)
        self.assertEqual(seen["suite"], "live")

        research = YhatFactoryFitBody(
            head="tc_rank",
            params={"holdout_trading_days": 5},
            note="hub arm-t research",
        )
        with mock.patch("web.routers.quant_yhat_tf.start_holdout_fit", _capture):
            _fit("T", research)
        self.assertEqual(seen["params"]["holdout_trading_days"], 5)
        self.assertEqual(seen["suite"], "research")

        ridge = YhatFactoryFitBody(
            head="oo",
            params={"holdout_trading_days": 20},
            note="hub arm-r live",
        )
        with mock.patch("web.routers.quant_yhat_tf.start_holdout_fit", _capture):
            _fit("R", ridge)
        self.assertEqual(seen["factory"], "R")
        self.assertEqual(seen["suite"], "live")
        self.assertEqual(seen["params"]["holdout_trading_days"], 0)

    def test_max_train_default_unlimited(self):
        import os
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _arm_l_max_train_rows

        self.assertEqual(_arm_l_max_train_rows(), 0)
        with mock.patch.dict(os.environ, {"YHAT_ARM_L_MAX_TRAIN": "0"}):
            self.assertEqual(_arm_l_max_train_rows(), 0)
        with mock.patch.dict(os.environ, {"YHAT_ARM_L_MAX_TRAIN": "8000"}):
            self.assertEqual(_arm_l_max_train_rows(), 8000)

    def test_drop_aligned_rows_without_cube_keeps_later_days(self):
        from unittest import mock

        from core.research.yhat_transformer.factor_panel import FactorPanel
        from core.research.yhat_transformer.seq_dataset import SeqPanel
        from scripts.run_yhat_transformer_holdout import (
            _drop_aligned_rows_without_cube,
        )

        dates = ["2022-06-22", "2022-07-01", "2022-06-22", "2022-07-04"]
        codes = ["000001", "000001", "000002", "000002"]
        seq = SeqPanel(
            X=np.arange(4 * 2 * 2, dtype=np.float32).reshape(4, 2, 2),
            gap=np.arange(4, dtype=np.float32),
            y=np.arange(4, dtype=np.float32),
            dates=dates,
            codes=codes,
            seq_len=2,
        )
        fac = FactorPanel(
            X=np.arange(8, dtype=np.float32).reshape(4, 2),
            y=np.arange(4, dtype=np.float32),
            dates=list(dates),
            codes=list(codes),
            feature_names=["a", "b"],
        )
        # 可读起点 07-01：06-22 切掉，当天及之后留下。
        with mock.patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=("2022-07-01", 0),
        ):
            seq2, fac2 = _drop_aligned_rows_without_cube(seq, fac)
        self.assertEqual(seq2.dates, ["2022-07-01", "2022-07-04"])
        self.assertEqual(fac2.codes, ["000001", "000002"])
        self.assertEqual(int(seq2.n), 2)
        self.assertEqual(int(fac2.n), 2)
        np.testing.assert_array_equal(fac2.X[:, 0], np.asarray([2, 6], dtype=np.float32))

        with mock.patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=("2022-06-22", 0),
        ):
            seq3, fac3 = _drop_aligned_rows_without_cube(seq, fac)
        self.assertIs(seq3, seq)
        self.assertIs(fac3, fac)

        with mock.patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=("", 0),
        ):
            seq4, fac4 = _drop_aligned_rows_without_cube(seq, fac)
        self.assertIs(seq4, seq)
        self.assertEqual(seq4.dates, dates)
        self.assertIs(fac4, fac)

        # 起点之后的缺口留下：06-22 切掉，08-15 仍留在面板里。
        hole_dates = ["2022-06-22", "2022-07-01", "2022-08-15", "2022-08-16"]
        seq_h = SeqPanel(
            X=np.arange(4 * 2 * 2, dtype=np.float32).reshape(4, 2, 2),
            gap=np.arange(4, dtype=np.float32),
            y=np.arange(4, dtype=np.float32),
            dates=hole_dates,
            codes=list(codes),
            seq_len=2,
        )
        fac_h = FactorPanel(
            X=np.arange(8, dtype=np.float32).reshape(4, 2),
            y=np.arange(4, dtype=np.float32),
            dates=list(hole_dates),
            codes=list(codes),
            feature_names=["a", "b"],
        )
        with mock.patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=("2022-07-01", 1),
        ):
            seq5, fac5 = _drop_aligned_rows_without_cube(seq_h, fac_h)
        self.assertEqual(seq5.dates, ["2022-07-01", "2022-08-15", "2022-08-16"])
        self.assertEqual(fac5.dates, ["2022-07-01", "2022-08-15", "2022-08-16"])

        with mock.patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=("2022-08-01", 0),
        ):
            seq6, fac6 = _drop_aligned_rows_without_cube(seq, fac)
        self.assertEqual(int(seq6.n), 0)
        self.assertEqual(int(fac6.n), 0)

    def test_first_readable_cube_day_skips_bad_prefix(self):
        from types import SimpleNamespace
        from unittest import mock

        from core.research.materialize.cube_z import first_readable_cube_day

        good = SimpleNamespace(
            names=["a"],
            z_mean=np.ones(1),
            z_std=np.ones(1),
        )
        thin = SimpleNamespace(
            names=["a"],
            z_mean=np.zeros(0),
            z_std=np.zeros(0),
        )

        def _load(day, _univ, **_kw):
            if day == "2022-06-01":
                raise OSError("truncated")
            if day == "2022-06-22":
                return thin
            if day == "2022-07-01":
                return good
            return None

        with mock.patch(
            "core.research.materialize.cube_z._cube_disk_span",
            return_value=("2022-06-01", "2022-06-22", "2022-07-01", "2022-08-15"),
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ), mock.patch(
            "core.research.day_cube.load_day_cube",
            side_effect=_load,
        ):
            day, skipped = first_readable_cube_day("open")
        self.assertEqual(day, "2022-07-01")
        self.assertEqual(skipped, 2)

        with mock.patch(
            "core.research.materialize.cube_z._cube_disk_span",
            return_value=(),
        ):
            self.assertEqual(first_readable_cube_day("open"), ("", 0))

    def test_arm_t_uses_l2_cube_cs_z(self):
        """序列模型不把因子矩阵送进网络。稠密价量序列不做截面 z。"""
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
            dropout=0.0,
            epochs=1,
            batch_size=8,
            lr=1e-3,
            device="cpu",
            t_sample_frac=1.0,
            loss="mse",
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
            "core.research.yhat_transformer.train.train_arm_t",
            return_value=(fake_state, dict(fake_report)),
        ) as train_m, mock.patch(
            "core.research.yhat_transformer.train.predict_arm_t",
            return_value=np.zeros(4, dtype=np.float64),
        ):
            out = _run_arm_t(
                seq, fac, list(range(16)), list(range(16, 20)), args
            )
        self.assertTrue(out.get("success"))
        self.assertIn("sequence-only", str(out.get("note") or ""))
        csz_m.assert_not_called()
        train_m.assert_called_once()
        self.assertEqual(list(train_m.call_args.args[1]), list(range(16)))
        self.assertNotIn("arch", train_m.call_args.kwargs)
        self.assertNotIn("factor_hidden", train_m.call_args.kwargs)

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
        from core.research.yhat_transformer.seq_dataset import FactorWindowView

        values = np.ones((2, 2), dtype=np.float32)
        windows = np.zeros((n, 2), dtype=np.int32)
        view = FactorWindowView(values, windows, None, ("a", "b"))
        view.value_dates = ["2024-01-02", "2024-01-03"]
        seq = SimpleNamespace(
            X=view,
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
            dropout=0.0,
            epochs=1,
            batch_size=4,
            lr=1e-3,
            device="cpu",
            t_sample_frac=1.0,
            loss="mse",
        )
        with mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            side_effect=MaterializeRequired({"error": "缺 L2"}),
        ), mock.patch(
            "core.research.yhat_transformer.train.train_arm_t"
        ) as train_m:
            out = _run_arm_t(seq, fac, list(range(4)), list(range(4, 8)), args)
        self.assertFalse(out.get("success"))
        self.assertIn("MaterializeRequired", str(out.get("error") or ""))
        train_m.assert_not_called()


class TestArmTGate(unittest.TestCase):
    def test_gate_sign_and_rank_icir(self):
        from core.research.yhat_transformer.metrics import hard_gate_vs_baseline

        def gate(bf, cf, bi, ci):
            return hard_gate_vs_baseline(
                {"cs_rank_ic": bf, "cs_rank_icir": bi},
                {"cs_rank_ic": cf, "cs_rank_icir": ci},
            )

        self.assertFalse(gate(-0.04, -0.03, 0.40, 0.40)["pass"])
        self.assertTrue(gate(0.05, 0.07, 0.50, 0.48)["pass"])
        self.assertFalse(gate(0.10, 0.112, 0.50, 0.40)["pass"])
        self.assertTrue(gate(-0.02, 0.03, -0.10, 0.20)["pass"])

    def test_factor_sequence_uses_open_cube_z_and_keeps_age(self):
        from unittest.mock import patch

        from core.research.yhat_transformer.factor_panel import (
            FactorPanel,
            cs_z_factor_sequence,
            sequence_from_factor_panel,
        )

        bars = _synth_bars(12, seed=1)
        dates = [b["date"] for b in bars]
        width = 3
        raw = np.arange(len(dates) * width, dtype=np.float32).reshape(len(dates), width)
        fac = FactorPanel(
            X=raw.copy(),
            y=np.arange(len(dates), dtype=np.float32),
            dates=list(dates),
            codes=["000001"] * len(dates),
            feature_names=["c0", "c1", "c2"],
        )
        seq = sequence_from_factor_panel(fac, {"000001": bars}, seq_len=5)
        age_before = float(seq.X[0, 0, -1])
        seen = {}

        def fake_z(x, names, groups, clock="open"):
            seen["clock"] = clock
            seen["names"] = list(names)
            seen["days"] = list(groups)
            return np.full_like(x, 4.0, dtype=np.float64)

        with patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=(dates[2], 0),
        ), patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            side_effect=fake_z,
        ):
            cs_z_factor_sequence(seq, z_clip=3.0)
            cs_z_factor_sequence(seq, z_clip=3.0)
        self.assertEqual(seen["clock"], "open")
        self.assertEqual(seen["names"], ["c0", "c1", "c2"])
        self.assertNotIn(dates[0], seen["days"])
        self.assertIn(dates[2], seen["days"])
        self.assertEqual(float(seq.X.values[0, 0]), 0.0)
        self.assertEqual(float(seq.X.values[2, 0]), 3.0)
        self.assertAlmostEqual(float(seq.X[0, 0, -1]), age_before, places=5)
        sub = seq.subset([0])
        self.assertTrue(sub.X.cs_z)
        self.assertIs(sub.X.value_dates, seq.X.value_dates)
        gap = np.array([50.0] + [0.01] * (seq.n - 1), dtype=np.float32)
        values = np.array(seq.X.values, copy=True)
        clip = seq.X.clip_train(gap, list(range(seq.n)), seed=0)
        self.assertTrue(clip["seq_cs_z"])
        np.testing.assert_array_equal(seq.X.values, values)
        seq.X.values[3, 0] = np.nan
        seq.X.apply_saved_clip(
            gap,
            {"seq_cs_z": True, "gap_lo": 0.0, "gap_hi": 1.0, "x_lo": [-5.0], "x_hi": [-4.0]},
        )
        self.assertEqual(float(seq.X.values[3, 0]), 0.0)
        self.assertEqual(float(seq.X.values[2, 0]), 3.0)
        self.assertLessEqual(float(gap.max()), 1.0)

    def test_seq_nan_imputes_train_median(self):
        from core.research.yhat_factory_backtest import apply_seq_clip
        from core.research.yhat_transformer.train import _impute_seq_median

        X = np.array([[[1.0], [np.nan]], [[3.0], [5.0]]], dtype=np.float32)
        med = _impute_seq_median(X, [0, 1], seed=0)
        self.assertEqual(med, [3.0])
        self.assertEqual(float(X[0, 1, 0]), 3.0)
        filled = np.array([[[np.nan]]], dtype=np.float32)
        gap = np.zeros((1,), dtype=np.float32)
        apply_seq_clip(
            filled,
            gap,
            {
                "x_median": [3.0],
                "x_lo": [-10.0],
                "x_hi": [10.0],
                "gap_lo": -1.0,
                "gap_hi": 1.0,
            },
        )
        self.assertEqual(float(filled[0, 0, 0]), 3.0)


class TestRelativeHardGates(unittest.TestCase):
    def test_single_arm_factory_returns_none(self):
        from scripts.run_yhat_transformer_holdout import _relative_hard_gates

        summary = {"arm_L": {"success": True, "cs_rank_ic": 0.04}}
        called = {"n": 0}

        def _gate(_base, _pack):
            called["n"] += 1
            return {"pass": True}

        self.assertIsNone(_relative_hard_gates(summary, _gate, factory="L"))
        self.assertIsNone(_relative_hard_gates(summary, _gate, factory="T"))
        self.assertEqual(called["n"], 0)

    def test_lt_missing_baseline_is_fail_not_crash(self):
        from scripts.run_yhat_transformer_holdout import _relative_hard_gates

        summary = {
            "arm_L": {"success": False, "error": "boom"},
            "arm_T": {"success": True, "cs_rank_ic": 0.02},
        }
        gates = _relative_hard_gates(summary, lambda _a, _b: {"pass": True}, factory="LT")
        self.assertIsInstance(gates, dict)
        self.assertFalse(gates["T"]["pass"])
        self.assertIn("baseline missing", gates["T"]["reasons"][0])

    def test_informal_lt_is_not_applicable(self):
        from scripts.run_yhat_transformer_holdout import _relative_hard_gates

        summary = {
            "arm_L": {"success": True, "cs_rank_ic": 0.01, "cs_rank_icir": 0.2},
            "arm_T": {"success": True, "cs_rank_ic": 0.05, "cs_rank_icir": 0.3},
        }
        gates = _relative_hard_gates(
            summary, lambda _a, _b: {"pass": True}, factory="LT", formal=False
        )
        self.assertFalse(gates["T"]["pass"])
        self.assertFalse(gates["T"]["applicable"])
        self.assertIn("非正式对照", gates["T"]["reasons"][0])


class TestHoldoutSystemExitDesk(unittest.TestCase):
    def test_sigterm_systemexit_marks_desk_failed(self):
        """SIGTERM → SystemExit(143) 时回写 desk，避免 job 假 running。"""
        from unittest import mock

        import scripts.run_yhat_transformer_holdout as holdout

        marked = {}

        def _fake_parse():
            ns = mock.Mock()
            ns.head = "oo"
            ns.factory = "L"
            ns.update_desk = True
            return ns

        with mock.patch.object(
            holdout.argparse.ArgumentParser, "parse_args", return_value=_fake_parse()
        ), mock.patch.object(
            holdout, "_run_holdout", side_effect=SystemExit(143)
        ), mock.patch.object(
            holdout,
            "_desk_mark_failed",
            side_effect=lambda *a, **k: marked.update({"args": a, **k}),
        ):
            with self.assertRaises(SystemExit) as cm:
                holdout.main()
            self.assertEqual(cm.exception.code, 143)
        self.assertEqual(marked["args"][0], "oo")
        self.assertIn("SIGTERM", marked["args"][1])
        self.assertEqual(marked.get("factory"), "L")

    def test_zero_systemexit_does_not_mark_failed(self):
        from unittest import mock

        import scripts.run_yhat_transformer_holdout as holdout

        calls = []

        def _fake_parse():
            ns = mock.Mock()
            ns.head = "oo"
            ns.factory = "T"
            ns.update_desk = True
            return ns

        with mock.patch.object(
            holdout.argparse.ArgumentParser, "parse_args", return_value=_fake_parse()
        ), mock.patch.object(
            holdout, "_run_holdout", side_effect=SystemExit(0)
        ), mock.patch.object(
            holdout, "_desk_mark_failed", side_effect=lambda *a, **k: calls.append((a, k))
        ):
            with self.assertRaises(SystemExit):
                holdout.main()
        self.assertEqual(calls, [])


class TestFactorPanelBuildLine(unittest.TestCase):
    def test_arm_t_only_names_the_slice_not_arm_l(self):
        from scripts.run_yhat_transformer_holdout import _factor_panel_build_line

        oo = _factor_panel_build_line("oo", ["T"])
        self.assertIn("head=oo", oo)
        self.assertIn("先建 aoo_full", oo)
        self.assertIn("裁成 aoo_core", oo)
        self.assertNotIn("Arm·L", oo)
        rank = _factor_panel_build_line("oo_rank", ["T"])
        self.assertIn("先建 aoo_full", rank)
        self.assertIn("裁成 aoo_core", rank)
        full = _factor_panel_build_line("oo", ["T"], full_aoo=True)
        self.assertIn("Arm·T 用 aoo_full", full)
        self.assertNotIn("Arm·L", full)
        tc = _factor_panel_build_line("tc", ["T"], atc_core=True)
        self.assertIn("先建 atc_full", tc)
        self.assertIn("裁成 atc_core", tc)
        self.assertNotIn("Arm·L", tc)
        tc_full = _factor_panel_build_line("tc", ["T"])
        self.assertIn("Arm·T 用 atc_full", tc_full)
        self.assertNotIn("裁成", tc_full)

    def test_arm_l_and_both_keep_the_full_source(self):
        from scripts.run_yhat_transformer_holdout import _factor_panel_build_line

        lone = _factor_panel_build_line("oo", ["L"])
        self.assertIn("Arm·L 用 aoo_full", lone)
        self.assertNotIn("Arm·T", lone)
        both = _factor_panel_build_line("oo", ["L", "T"])
        self.assertIn("Arm·L 用 aoo_full", both)
        self.assertIn("裁成 aoo_core", both)
        same = _factor_panel_build_line("oo", ["L", "T"], full_aoo=True)
        self.assertIn("Arm·L 与 Arm·T 都用 aoo_full", same)
        tc_both = _factor_panel_build_line("tc_rank", ["L", "T"], atc_core=True)
        self.assertIn("Arm·L 用 atc_full", tc_both)
        self.assertIn("裁成 atc_core", tc_both)


class TestLabelHorizonByHead(unittest.TestCase):
    def test_tc_embargo_stays_zero_when_cli_horizon_is_one(self):
        import datetime

        from core.research.holdout import resolve_ridge_split
        from scripts.run_yhat_transformer_holdout import label_horizon_days_for_head

        self.assertEqual(label_horizon_days_for_head("tc", 1), 0)
        self.assertEqual(label_horizon_days_for_head("tc_rank", 1), 0)
        self.assertEqual(label_horizon_days_for_head("oo", 1), 1)
        self.assertEqual(label_horizon_days_for_head("oo_rank", 3), 3)

        dates = []
        day = datetime.date(2026, 1, 5)
        while len(dates) < 40:
            if day.weekday() < 5:
                dates.append(day.isoformat())
            day += datetime.timedelta(days=1)
        _, _, same_day = resolve_ridge_split(
            dates,
            holdout_trading_days=5,
            label_horizon_days=label_horizon_days_for_head("tc", 1),
            calendar_dates=dates,
        )
        _, _, overnight = resolve_ridge_split(
            dates,
            holdout_trading_days=5,
            label_horizon_days=label_horizon_days_for_head("oo", 1),
            calendar_dates=dates,
        )
        self.assertEqual(same_day["eval_start"], overnight["eval_start"])
        self.assertEqual(same_day["label_horizon_days"], 0)
        eval_i = dates.index(same_day["eval_start"])
        self.assertEqual(same_day["fit_end"], dates[eval_i - 1])
        self.assertEqual(overnight["fit_end"], dates[eval_i - 2])


class TestRegressionLevelMetrics(unittest.TestCase):
    def test_mse_uses_raw_points(self):
        from core.research.yhat_transformer.metrics import holdout_mse

        preds = [0.0, 1.0, 2.0]
        ys = [0.0, 1.0, 3.0]
        self.assertAlmostEqual(holdout_mse(preds, ys), 1.0 / 3.0, places=6)

    def test_mse_skips_non_finite_and_empty(self):
        from core.research.yhat_transformer.metrics import holdout_mse

        self.assertIsNone(holdout_mse([], []))
        self.assertIsNone(holdout_mse([float("nan")], [1.0]))
        self.assertAlmostEqual(holdout_mse([0.2] * 2, [1.0, 1.0]), 0.64, places=6)


if __name__ == "__main__":
    unittest.main()