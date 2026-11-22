"""模型中心 · L4 四头 ŷ Transformer 研究台。"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest import mock


class TestYhatTfLegacyMigrate(unittest.TestCase):
    def test_coerce_drops_legacy_seeds_field(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "oo_draft.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "role": "yhat_tf_draft",
                        "params": {"seeds": "42,43", "d_model": 64, "pool_n": 200},
                        "cli": "--seeds 42,43",
                    },
                    f,
                )
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td):
                from core.research.yhat_tf_desk import _coerce_slot_params

                with open(path, encoding="utf-8") as f:
                    blob = json.load(f)
                out = _coerce_slot_params("oo", blob, persist_path=path)
                self.assertNotIn("seeds", out["params"])
                self.assertNotIn("--seeds", out.get("cli") or "")
                with open(path, encoding="utf-8") as f:
                    disk = json.load(f)
                self.assertNotIn("seeds", disk["params"])


class TestYhatTfHardenParams(unittest.TestCase):
    def test_mlp_hidden_is_dropped(self):
        """因子 MLP 的 mlp_hidden 不再进入拟合参数。"""
        from core.research.yhat_tf_desk import (
            _merge_request_over_draft,
            _normalize_fit_params,
        )

        draft = _normalize_fit_params("oo", {"d_model": 64, "mlp_hidden": 32})
        self.assertNotIn("mlp_hidden", draft)
        merged = _merge_request_over_draft(
            {"d_model": 64, "mlp_hidden": 32}, {"d_model": 128, "pool_n": 800}
        )
        out = _normalize_fit_params("oo", merged)
        self.assertEqual(out["d_model"], 128)
        self.assertNotIn("mlp_hidden", out)

    def test_tc_lookback_clamped_and_nhead_aligned(self):
        from core.research.yhat_tf_desk import harden_fit_params, _normalize_fit_params

        out, warns = harden_fit_params(
            "tc",
            {
                "lookback": 900,
                "holdout_trading_days": 60,
                "d_model": 64,
                "nhead": 5,
                "pool_n": 800,
                "arms": "0,C",
                "seeds": "42,43",
            },
        )
        # 900/60 须保留，不再写长窗内存提示
        self.assertEqual(out["lookback"], 900)
        self.assertEqual(out["holdout_trading_days"], 60)
        self.assertEqual(out["nhead"], 4)  # 64 % 5 != 0 → 4
        live, live_warns = harden_fit_params("oo", {"holdout_trading_days": 0})
        self.assertEqual(live["holdout_trading_days"], 0)
        self.assertFalse(any(w.startswith("Holdout") for w in live_warns))
        raised, raised_warns = harden_fit_params("oo", {"holdout_trading_days": 3})
        self.assertEqual(raised["holdout_trading_days"], 5)
        self.assertTrue(any(w.startswith("Holdout") for w in raised_warns))
        self.assertEqual(out["arms"], "T")  # 厂 T 单臂；传入的臂码不另开对侧
        self.assertNotIn("seeds", out)  # 固定 seed=42，不再入参
        self.assertFalse(any("长窗" in w for w in warns))
        def_p, _ = harden_fit_params("oo", {})
        self.assertNotIn("seeds", def_p)
        norm = _normalize_fit_params("tc", {"lookback": 900, "holdout_trading_days": 60})
        self.assertEqual(norm["lookback"], 900)
        self.assertEqual(norm["holdout_trading_days"], 60)
        self.assertEqual(norm["arms"], "T")
        from core.research.yhat_tf_desk import factory_scope

        with factory_scope("LT"):
            both, _ = harden_fit_params("oo", {})
        self.assertEqual(both["arms"], "L,T")
        # 脚本 args 只有 watching_limit 时须回填 pool_n，避免 ingest 盖回默认池
        from_args = _normalize_fit_params(
            "tc", {"watching_limit": 600, "lookback": 180, "d_model": 64}
        )
        self.assertEqual(from_args["pool_n"], 600)
        self.assertEqual(from_args["watching_limit"], 600)
        big, big_warns = harden_fit_params("tc", {"d_model": 256, "pool_n": 800})
        self.assertEqual(big["d_model"], 256)
        self.assertFalse(any("显存/内存压力大" in w for w in big_warns))
        # 不硬钳上限：512 原样保留，不再写显存提示
        over, over_warns = harden_fit_params("tc", {"d_model": 512, "pool_n": 800})
        self.assertEqual(over["d_model"], 512)
        self.assertFalse(any("显存/内存压力大" in w or "d_model=512" in w for w in over_warns))
        self.assertFalse(any("→" in w and "d_model:" in w for w in over_warns))
        tiny, tiny_warns = harden_fit_params("tc", {"d_model": 8})
        self.assertEqual(tiny["d_model"], 16)
        self.assertTrue(any("d_model:" in w and "→16" in w for w in tiny_warns))

    def test_tc_full_atc_defaults_and_core_is_opt_in(self):
        from core.research.yhat_tf_desk import build_holdout_cli, harden_fit_params

        out, warns = harden_fit_params("tc", {"lookback": 900})
        self.assertTrue(out["full_atc"])
        self.assertFalse(out["full_aoo"])
        self.assertFalse(any("full_aoo" in w for w in warns))
        cli = build_holdout_cli("tc", out)
        self.assertNotIn("--atc-core", cli)
        core, core_warns = harden_fit_params("tc", {"full_atc": False, "full_aoo": True})
        self.assertFalse(core["full_atc"])
        self.assertFalse(core["full_aoo"])
        self.assertTrue(any("忽略 full_aoo" in w for w in core_warns))
        self.assertIn("--atc-core", build_holdout_cli("tc", core))
        oo, _ = harden_fit_params("oo", {"full_atc": False})
        self.assertNotIn("full_atc", oo)
        missing, _ = harden_fit_params("tc", {"full_atc": None})
        self.assertTrue(missing["full_atc"])

    def test_drops_preset_and_oo_tau_end(self):
        from core.research.yhat_tf_desk import harden_fit_params

        oo, _ = harden_fit_params(
            "oo",
            {"protocol_preset": "wide", "seq_feat_set": "seq32", "tau_end": "09:30"},
        )
        self.assertNotIn("protocol_preset", oo)
        self.assertNotIn("tau_end", oo)
        self.assertEqual(oo["seq_feat_set"], "seq_core")
        legacy, legacy_warns = harden_fit_params("oo", {"seq_feat_set": "seq16"})
        self.assertEqual(legacy["seq_feat_set"], "seq_core")
        self.assertTrue(any("已下线" in w for w in legacy_warns))
        legacy64, _ = harden_fit_params("oo", {"seq_feat_set": "seq64"})
        self.assertEqual(legacy64["seq_feat_set"], "seq_core")
        core_seq, _ = harden_fit_params("oo", {"seq_feat_set": "core"})
        self.assertEqual(core_seq["seq_feat_set"], "seq_core")
        full_seq, _ = harden_fit_params("oo", {"seq_feat_set": "full"})
        self.assertEqual(full_seq["seq_feat_set"], "seq_full")
        tc, _ = harden_fit_params("tc", {"protocol_preset": "ablation"})
        self.assertNotIn("protocol_preset", tc)
        self.assertEqual(tc["tau_end"], "09:30")

    def test_atc_core_args_map_to_full_atc_false(self):
        """Holdout summary.args 只有 atc_core 时，不能回落到 full_atc=True。"""
        from core.research.yhat_tf_desk import (
            _normalize_fit_params,
            build_holdout_cli,
            harden_fit_params,
        )

        core, _ = harden_fit_params("tc", {"atc_core": True, "lookback": 900})
        self.assertFalse(core["full_atc"])
        self.assertNotIn("atc_core", core)
        self.assertIn("--atc-core", build_holdout_cli("tc", core))
        # ingest 走 normalize：默认 full_atc=True 不得盖掉 atc_core
        via_args = _normalize_fit_params(
            "tc", {"atc_core": True, "lookback": 900, "watching_limit": 800}
        )
        self.assertFalse(via_args["full_atc"])
        self.assertNotIn("atc_core", via_args)
        self.assertIn("--atc-core", build_holdout_cli("tc", via_args))
        # desk 显式 full_atc 优先于 atc_core
        keep, _ = harden_fit_params(
            "tc", {"full_atc": False, "atc_core": False, "lookback": 900}
        )
        self.assertFalse(keep["full_atc"])
        self.assertIn("--atc-core", build_holdout_cli("tc", keep))

    def test_loss_clamp_warns_and_cli_has_lr(self):
        from core.research.yhat_tf_desk import build_holdout_cli, harden_fit_params

        oo, warns = harden_fit_params("oo", {"loss": "mae", "lr": 5e-4})
        self.assertEqual(oo["loss"], "mse")
        self.assertTrue(any("loss:" in w and "mae→mse" in w for w in warns))
        from core.research.yhat_tf_desk import factory_scope

        with factory_scope("L"):
            arm_l, l_warns = harden_fit_params("oo", {"loss": "mae"})
            arm_l_rank, _ = harden_fit_params("oo_rank", {"loss": "mae"})
        self.assertEqual(arm_l["loss"], "l2")
        self.assertTrue(any("mae→l2" in w for w in l_warns))
        self.assertEqual(arm_l_rank["loss"], "lambdarank")
        cli = build_holdout_cli("oo", oo)
        self.assertIn("--lr 0.0005", cli)

    def test_holdout_argv_flags_exist_in_script(self):
        """desk 拼的每个 flag 都必须被 Holdout 脚本接受（防止 --arms 这类漂移）。"""
        import subprocess
        import sys

        from core.research.yhat_tf_desk import ROOT_DIR, build_holdout_argv

        for head in ("oo", "tc", "oo_rank", "tc_rank"):
            argv = build_holdout_argv(head, {})
            self.assertNotIn("--arms", argv)
            help_out = subprocess.run(
                [sys.executable, argv[0], "--help"],
                capture_output=True,
                text=True,
                cwd=ROOT_DIR,
            ).stdout
            for tok in argv[1:]:
                if tok.startswith("--"):
                    self.assertIn(tok, help_out, f"{head}: 脚本不接受 {tok}")

    def test_t_sample_pct_desk_and_cli(self):
        from core.research.yhat_tf_desk import (
            _normalize_fit_params,
            build_holdout_cli,
            default_fit_params,
            harden_fit_params,
        )

        self.assertEqual(default_fit_params("oo")["t_sample_pct"], 100.0)
        omitted, _ = harden_fit_params("oo", {"lookback": 1000})
        self.assertTrue(omitted["full_aoo"])
        self.assertEqual(omitted["factor_pack"], "aoo_full")
        self.assertEqual(omitted["t_sample_pct"], 100.0)
        keep, _ = harden_fit_params("oo", {"t_sample_pct": 25})
        self.assertEqual(keep["t_sample_pct"], 25)
        self.assertIn("--t-sample-frac 0.25", build_holdout_cli("oo", keep))
        full, warns = harden_fit_params("oo", {"t_sample_pct": 100, "d_model": 512})
        self.assertEqual(full["t_sample_pct"], 100)
        self.assertIn("--t-sample-frac 1", build_holdout_cli("oo", full))
        self.assertFalse(any("OOM" in w for w in warns))
        from core.research.yhat_tf_desk import without_retired_param_warnings

        stale = {
            "param_warnings": [
                "t_sample_pct=100 且 d_model≥256：Arm·T 易 OOM，建议降低采样%或 d_model",
                "batch: 8→16",
            ]
        }
        served = without_retired_param_warnings(stale)
        self.assertEqual(served["param_warnings"], ["batch: 8→16"])
        self.assertEqual(len(stale["param_warnings"]), 2)
        tiny, tw = harden_fit_params("oo", {"t_sample_pct": 0.01})
        self.assertEqual(tiny["t_sample_pct"], 0.1)
        self.assertTrue(any("t_sample_pct:" in w for w in tw))
        # 旧绝对行上限丢弃；厂 T 固定单臂，不因传入 arms 改回双臂
        legacy, _lw = harden_fit_params("oo", {"max_train_rows": 40000, "arms": "L,T"})
        self.assertNotIn("max_train_rows", legacy)
        self.assertEqual(legacy["arms"], "T")
        self.assertEqual(legacy["t_sample_pct"], 100.0)
        # Holdout args.t_sample_frac → desk t_sample_pct（避免 ingest 掉回别的数）
        via_frac = _normalize_fit_params("oo", {"t_sample_frac": 1.0, "watching_limit": 800})
        self.assertEqual(via_frac["t_sample_pct"], 100.0)
        self.assertNotIn("t_sample_frac", via_frac)
        self.assertIn("--t-sample-frac 1", build_holdout_cli("oo", via_frac))

    def test_arm_l_defaults_match_hub_windows(self):
        from core.research.yhat_tf_desk import (
            build_holdout_argv,
            default_fit_params,
            factory_scope,
        )

        from core.research.yhat_transformer.heads import (
            ARM_T_BATCH,
            ARM_T_D_MODEL,
            ARM_T_EPOCHS,
            ARM_T_HOLDOUT,
            ARM_T_LOOKBACK,
            ARM_T_NLAYERS,
            ARM_T_SEQ_LEN,
        )

        oo_t = default_fit_params("oo")
        self.assertEqual(oo_t["lookback"], ARM_T_LOOKBACK)
        self.assertEqual(oo_t["holdout_trading_days"], ARM_T_HOLDOUT)
        self.assertEqual(oo_t["d_model"], ARM_T_D_MODEL)
        self.assertEqual(oo_t["nlayers"], ARM_T_NLAYERS)
        self.assertEqual(oo_t["epochs"], ARM_T_EPOCHS)
        self.assertEqual(oo_t["batch_size"], ARM_T_BATCH)
        self.assertEqual(oo_t["seq_len"], ARM_T_SEQ_LEN)
        self.assertEqual(oo_t["t_sample_pct"], 100.0)
        self.assertEqual(oo_t["lr"], 1e-4)
        self.assertTrue(oo_t["full_aoo"])
        self.assertEqual(oo_t["feature_set"], "hs300")
        self.assertEqual(default_fit_params("oo_rank")["lookback"], 1000)
        self.assertEqual(default_fit_params("tc_rank")["lookback"], 1000)
        with factory_scope("L"):
            oo_l = default_fit_params("oo")
            self.assertEqual(oo_l["lookback"], 600)
            self.assertEqual(oo_l["holdout_trading_days"], 20)
            self.assertEqual(oo_l["arms"], "L")
            self.assertNotIn("feature_set", oo_l)
            self.assertTrue(oo_l["full_aoo"])
            self.assertEqual(oo_l["factor_pack"], "aoo_full")
            self.assertEqual(default_fit_params("tc")["lookback"], 120)
            self.assertEqual(default_fit_params("oo_rank")["lookback"], 700)
            self.assertEqual(default_fit_params("tc_rank")["lookback"], 700)
            self.assertEqual(default_fit_params("tc_rank")["holdout_trading_days"], 20)
        argv = build_holdout_argv("tc", factory="L")
        self.assertEqual(argv[argv.index("--lookback") + 1], "120")
        self.assertEqual(argv[argv.index("--holdout-trading-days") + 1], "20")
        argv_t = build_holdout_argv("oo", factory="T")
        self.assertEqual(argv_t[argv_t.index("--lookback") + 1], str(ARM_T_LOOKBACK))
        self.assertEqual(
            argv_t[argv_t.index("--holdout-trading-days") + 1], str(ARM_T_HOLDOUT)
        )
        self.assertEqual(argv_t[argv_t.index("--d-model") + 1], str(ARM_T_D_MODEL))
        self.assertEqual(argv_t[argv_t.index("--batch-size") + 1], str(ARM_T_BATCH))
        self.assertEqual(argv_t[argv_t.index("--epochs") + 1], str(ARM_T_EPOCHS))
        self.assertEqual(argv_t[argv_t.index("--seq-len") + 1], str(ARM_T_SEQ_LEN))
        self.assertEqual(argv_t[argv_t.index("--nlayers") + 1], str(ARM_T_NLAYERS))
        import inspect

        from core.research.yhat_transformer.train import train_arm_t

        sig = inspect.signature(train_arm_t)
        self.assertEqual(sig.parameters["d_model"].default, ARM_T_D_MODEL)
        self.assertEqual(sig.parameters["nlayers"].default, ARM_T_NLAYERS)
        self.assertEqual(sig.parameters["epochs"].default, ARM_T_EPOCHS)
        self.assertEqual(sig.parameters["batch_size"].default, ARM_T_BATCH)

    def test_arm_t_both_pools_chain_finetune(self):
        from unittest import mock

        from core.research.yhat_tf_desk import build_holdout_argv, factory_scope

        codes = ["000001", "000002"]
        with factory_scope("T"), mock.patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=codes,
        ):
            argv = build_holdout_argv(
                "oo",
                {"feature_set": "pool", "train_stage": "chain"},
                factory="T",
            )
        self.assertIn("--chain-finetune", argv)
        self.assertNotIn("--finetune", argv)
        self.assertEqual(argv[argv.index("--codes") + 1], "000001,000002")
        self.assertEqual(argv[argv.index("--lr") + 1], "0.001")
        with factory_scope("T"), mock.patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=codes,
        ):
            argv_hs = build_holdout_argv(
                "oo",
                {"feature_set": "hs300"},
                factory="T",
            )
        self.assertNotIn("--finetune", argv_hs)
        self.assertNotIn("--chain-finetune", argv_hs)
        self.assertEqual(argv_hs[argv_hs.index("--codes") + 1], "000001,000002")
        self.assertEqual(argv_hs[argv_hs.index("--lr") + 1], "0.0001")
        with factory_scope("T"), mock.patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=codes,
        ):
            kept = build_holdout_argv(
                "oo",
                {"feature_set": "hs300", "lr": 5e-4},
                factory="T",
            )
            legacy = build_holdout_argv(
                "oo",
                {"feature_set": "hs300", "lr": 1e-3},
                factory="T",
            )
        self.assertEqual(kept[kept.index("--lr") + 1], "0.0005")
        self.assertEqual(legacy[legacy.index("--lr") + 1], "0.0001")
        from core.research.yhat_tf_desk import resolve_arm_t_lr

        self.assertEqual(resolve_arm_t_lr(None, feature_set="hs300"), 1e-4)
        self.assertEqual(resolve_arm_t_lr(1e-3, feature_set="hs300"), 1e-4)
        self.assertEqual(resolve_arm_t_lr(5e-4, feature_set="hs300"), 5e-4)
        self.assertEqual(resolve_arm_t_lr(1e-4, feature_set="pool"), 1e-3)
        self.assertEqual(
            resolve_arm_t_lr(1e-3, feature_set="pool", finetune=True), 1e-4
        )
        with factory_scope("T"), mock.patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=codes,
        ):
            argv_old = build_holdout_argv(
                "oo",
                {"feature_set": "hs300", "train_stage": "finetune"},
                factory="T",
            )
        self.assertNotIn("--finetune", argv_old)
        self.assertNotIn("--chain-finetune", argv_old)
        with factory_scope("T"), mock.patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=codes,
        ):
            argv_stuck = build_holdout_argv(
                "oo",
                {"feature_set": "hs300", "train_stage": "chain"},
                factory="T",
            )
        self.assertNotIn("--chain-finetune", argv_stuck)
        self.assertNotIn("--finetune", argv_stuck)
        from core.research.yhat_tf_desk import (
            _merge_request_over_draft,
            _normalize_fit_params,
        )

        stuck = _merge_request_over_draft(
            {
                "feature_set": "pool",
                "train_stage": "chain",
                "pool_n": 1600,
                "watching_limit": 1600,
            },
            {"feature_set": "hs300"},
        )
        self.assertEqual(stuck.get("train_stage"), "chain")
        wide = [f"{i:06d}" for i in range(300)]
        with factory_scope("T"), mock.patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=wide,
        ):
            fixed = _normalize_fit_params("tc", stuck)
        self.assertEqual(fixed.get("feature_set"), "hs300")
        self.assertFalse(fixed.get("train_stage"))
        self.assertEqual(fixed.get("pool_n"), 300)
        self.assertEqual(fixed.get("watching_limit"), 300)
        with factory_scope("L"), mock.patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=codes,
        ):
            argv_l = build_holdout_argv(
                "oo",
                {"feature_set": "hs300"},
                factory="L",
            )
        self.assertNotIn("--chain-finetune", argv_l)
        self.assertNotIn("--finetune", argv_l)
        self.assertEqual(argv_l[argv_l.index("--codes") + 1], "000001,000002")

    def test_arm_r_is_ridge_factory(self):
        from core.research.yhat_tf_desk import (
            _metrics_from_holdout_summary,
            build_holdout_argv,
            default_fit_params,
            desk_status,
            factory_scope,
            harden_fit_params,
            normalize_factory,
        )

        self.assertEqual(normalize_factory("ARM-R"), "R")
        self.assertEqual(normalize_factory("ridge"), "R")
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as td, mock.patch(
            "core.research.yhat_tf_desk.YHAT_R_DIR", td
        ), mock.patch(
            "core.research.yhat_tf_desk.YHAT_R_LAST_PATH",
            os.path.join(td, "last_status.json"),
        ), factory_scope("R"):
            oo = default_fit_params("oo")
            self.assertEqual(oo["arms"], "R")
            self.assertEqual(oo["loss"], "ridge")
            self.assertEqual(oo["lookback"], 600)
            self.assertEqual(oo["holdout_trading_days"], 20)
            self.assertEqual(oo["ridge_alpha"], 1.0)
            self.assertNotIn("d_model", oo)
            self.assertNotIn("seq_len", oo)
            self.assertNotIn("batch_size", oo)
            self.assertEqual(default_fit_params("tc")["lookback"], 120)
            self.assertEqual(default_fit_params("oo_rank")["loss"], "ridge")
            kept, warns = harden_fit_params("oo", {"ridge_alpha": 250, "d_model": 128})
            self.assertEqual(kept["ridge_alpha"], 100.0)
            self.assertNotIn("d_model", kept)
            self.assertTrue(any("ridge_alpha:" in w for w in warns))
            st = desk_status(factory="R")
        self.assertEqual(st["package"], "yhat_r")
        argv = build_holdout_argv("oo", factory="R")
        self.assertIn("--factory", argv)
        self.assertEqual(argv[argv.index("--factory") + 1], "R")
        self.assertIn("--ridge-alpha", argv)
        self.assertNotIn("--d-model", argv)
        self.assertNotIn("--seq-len", argv)
        metrics = _metrics_from_holdout_summary(
            {"arm_R": {"success": True, "cs_rank_ic": 0.21, "n_test": 4}}
        )
        self.assertEqual(metrics.get("arm"), "R")
        self.assertEqual(metrics.get("cs_rank_ic"), 0.21)
        failed = _metrics_from_holdout_summary(
            {
                "factory": "R",
                "arm_R": {"success": False, "error": "cube miss"},
                "split_R": {
                    "n_train": 10,
                    "n_test": 4,
                    "fit_end": "2026-08-13",
                    "eval_start": "2026-08-17",
                },
            }
        )
        self.assertEqual(failed.get("arm"), "R")
        self.assertNotIn("T", (failed.get("splits") or {}))

    def test_nested_lock_keeps_ambient_factory_L(self):
        """外层 factory_scope(L) 时，无 factory= 的嵌套调用不得盖成 T。"""
        from core.research.yhat_tf_desk import (
            _factory_from_call,
            default_fit_params,
            factory_scope,
            harden_fit_params,
        )

        with factory_scope("L"):
            self.assertEqual(_factory_from_call(), "L")
            p, _ = harden_fit_params("oo", default_fit_params("oo"))
            self.assertEqual(p["arms"], "L")
            self.assertTrue(p["full_aoo"])
            self.assertEqual(p["factor_pack"], "aoo_full")

    def test_bool_strings_share_one_table(self):
        from core.research.yhat_tf_desk import _protocol_matches, compute_parity

        formal = compute_parity(
            "oo", {"full_aoo": "full", "t_sample_pct": 100}, eval_shared=True
        )
        self.assertTrue(formal["factor_width_match"])
        self.assertTrue(formal["formal"])
        core = compute_parity(
            "tc", {"full_atc": "false", "t_sample_pct": 100}, eval_shared=True
        )
        self.assertFalse(core["factor_width_match"])
        named = compute_parity("tc", {"full_atc": "core"}, eval_shared=False)
        self.assertFalse(named["factor_width_match"])
        bare = compute_parity("oo", {}, eval_shared=True)
        self.assertTrue(bare["factor_width_match"])
        self.assertTrue(bare["train_sample_full"])
        self.assertTrue(bare["formal"])
        kept = compute_parity(
            "oo", {"full_aoo": False, "t_sample_pct": 10}, eval_shared=True
        )
        self.assertFalse(kept["factor_width_match"])
        self.assertFalse(kept["train_sample_full"])
        ok, drift = _protocol_matches({"full_aoo": "full"}, {"full_aoo": True})
        self.assertTrue(ok)
        self.assertEqual(drift, [])
        ok_atc, drift_atc = _protocol_matches({"full_atc": "core"}, {"full_atc": False})
        self.assertTrue(ok_atc)
        self.assertEqual(drift_atc, [])

    def test_coerce_persists_stray_tau_end_on_oo(self):
        from core.research.yhat_tf_desk import _coerce_slot_params, _normalize_fit_params

        params = dict(_normalize_fit_params("oo", {"lookback": 900}))
        params["tau_end"] = "09:30"
        blob = {"role": "yhat_tf_last", "head": "oo", "params": params}
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "oo_last.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(blob, f)
            out = _coerce_slot_params("oo", blob, persist_path=path)
            self.assertNotIn("tau_end", out["params"])
            with open(path, encoding="utf-8") as f:
                disk = json.load(f)
            self.assertNotIn("tau_end", disk.get("params") or {})

    def test_summary_arms_falls_back_to_metrics_arms(self):
        from core.research.yhat_tf_desk import _summary_arms

        last = {
            "metrics": {
                "arms": {
                    "L": {"cs_rank_ic": 0.01, "n": 10},
                    "T": {"cs_rank_ic": 0.02, "n": 10},
                }
            }
        }
        arms = _summary_arms(last)
        self.assertEqual(arms["L"]["cs_rank_ic"], 0.01)
        self.assertEqual(arms["T"]["cs_rank_ic"], 0.02)

    def test_summary_arms_keeps_failed_arm_marker(self):
        """失败臂须保留 success=false，前端才能显示「失败」而非「无指标」。"""
        from core.research.yhat_tf_desk import _summary_arms

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "summary.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "arm_L": {
                            "success": False,
                            "error": "MaterializeRequired: cube miss",
                            "cs_rank_ic": 0.9,
                            "elapsed_sec": 12.5,
                        },
                        "arm_T": {"success": True, "cs_rank_ic": 0.02},
                    },
                    f,
                )
            arms = _summary_arms({"action": "holdout_result", "experiment_out": path})
        self.assertIs(arms["L"]["success"], False)
        self.assertIn("cube miss", arms["L"]["error"])
        self.assertEqual(arms["L"]["elapsed_sec"], 12.5)
        self.assertNotIn("cs_rank_ic", arms["L"])
        self.assertEqual(arms["T"]["cs_rank_ic"], 0.02)

    def test_summary_arms_keeps_rank_ndcg(self):
        """排序头运行台第三格读 cs_ndcg_30。切片丢掉它，NDCG@30 会是空的。"""
        from core.research.yhat_tf_desk import _summary_arms

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "summary.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "arm_T": {
                            "success": True,
                            "cs_rank_ic": 0.04,
                            "cs_icir": 0.22,
                            "cs_ndcg_30": 0.61,
                            "cs_top_mean_y": 0.18,
                        }
                    },
                    f,
                )
            arms = _summary_arms({"action": "holdout_result", "experiment_out": path})
        self.assertEqual(arms["T"]["cs_ndcg_30"], 0.61)

    def test_mark_fit_failed_writes_job_and_last(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH",
                os.path.join(td, "last_status.json"),
            ):
                from core.research.yhat_tf_desk import mark_fit_failed

                out = mark_fit_failed("oo", "boom")
                self.assertTrue(out["success"])
                with open(os.path.join(td, "oo_job.json"), encoding="utf-8") as f:
                    job = json.load(f)
                with open(os.path.join(td, "oo_last.json"), encoding="utf-8") as f:
                    last = json.load(f)
                self.assertEqual(job["status"], "error")
                self.assertEqual(job["error"], "boom")
                self.assertEqual(last["action"], "holdout_failed")
                self.assertIsNone(last.get("metrics"))


class TestYhatTfFitExitError(unittest.TestCase):
    def test_oom_hint_from_transformer_build_log(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td):
                from core.research.yhat_tf_desk import _fit_exit_error_from_log

                log_path = os.path.join(td, "oo_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(
                        "ArmC 切分 train=1 test=1 rss=1062.7\n"
                        "    transformer build d_model=128 layers=2 n_train=100 "
                        "batch=32 device=cpu\n"
                    )
                msg = _fit_exit_error_from_log("oo")
                self.assertIn("内存不足", msg)
                self.assertIn("Arm·T", msg)

    def test_segfault_hint_from_transformer_fit_start_log(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td):
                from core.research.yhat_tf_desk import _fit_exit_error_from_log

                log_path = os.path.join(td, "oo_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(
                        "    transformer model ready n_params=1\n"
                        "    transformer fit start loss=mse batch=32 "
                        "steps/ep=990 n_tr=100\n"
                    )
                msg = _fit_exit_error_from_log("oo")
                self.assertIn("OpenMP", msg)
                self.assertIn("再试", msg)

    def test_arml_lgb_oom_hint_from_log(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td):
                from core.research.yhat_tf_desk import _fit_exit_error_from_log

                log_path = os.path.join(td, "tc_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(
                        "ArmL 切分 train=1 test=1\n"
                        "  ArmL begin F=230 rss=941.8\n"
                    )
                msg = _fit_exit_error_from_log("tc")
                self.assertIn("Arm·L", msg)
                self.assertIn("LightGBM", msg)
                self.assertIn("τc", msg)

    def test_arml_column_filter_exit_is_not_called_lgb(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td):
                from core.research.yhat_tf_desk import _fit_exit_error_from_log

                log_path = os.path.join(td, "tc_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(
                        "  ArmL live-fit start head=tc n_train=1512391 "
                        "n_test=47866 F=230 rounds=300 rss=1539.3\n"
                        "  ArmL τc columns start N=1561851 F=230 rss=1539.3\n"
                    )
                msg = _fit_exit_error_from_log("tc")
                self.assertIn("截面列", msg)
                self.assertNotIn("LightGBM 训练中", msg)

    def test_oo_arml_oom_hint_does_not_mention_tc(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td):
                from core.research.yhat_tf_desk import _fit_exit_error_from_log

                log_path = os.path.join(td, "oo_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(
                        "ArmL 切分 train=1 test=1\n"
                        "  ArmL begin F=225 rss=1200\n"
                    )
                msg = _fit_exit_error_from_log("oo")
                self.assertIn("Arm·L", msg)
                self.assertNotIn("τc", msg)

    def test_live_fit_start_alone_gives_arml_oom_hint(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td):
                from core.research.yhat_tf_desk import _fit_exit_error_from_log

                log_path = os.path.join(td, "oo_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(
                        "ArmL 切分 train=1 test=1\n"
                        "  ArmL live-fit start head=oo n_train=1601922 "
                        "n_test=47869 F=225 rounds=300 rss=1637.0\n"
                        "fit drop 3205/1601753 rows without L2\n"
                    )
                msg = _fit_exit_error_from_log("oo")
                self.assertIn("Arm·L", msg)
                self.assertIn("LightGBM", msg)

    def test_interrupt_hint_from_mid_train_log(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td):
                from core.research.yhat_tf_desk import _fit_exit_error_from_log

                log_path = os.path.join(td, "tc_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(
                        "    transformer fit start loss=mse batch=32\n"
                        "    transformer step=1070 ep=2/6 [97/973] loss=1.65\n"
                    )
                msg = _fit_exit_error_from_log("tc")
                self.assertIn("中断", msg)

    def test_signal_exit_hint(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td):
                from core.research.yhat_tf_desk import _fit_exit_error_from_log

                log_path = os.path.join(td, "oo_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write("  序列面板 800/1598\nholdout 收到 SIGTERM，中途退出（未回写 metrics）\n")
                msg = _fit_exit_error_from_log("oo")
                self.assertIn("SIGTERM", msg)


class TestYhatTfPidAlive(unittest.TestCase):
    def test_waitpid_reaps_zombie_child(self):
        import subprocess
        import sys
        import time

        from core.research.yhat_tf_desk import _pid_alive, _reap_pid

        # 短命子进程：未 wait 前是僵尸；_pid_alive 应收割并返回 False
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.05)"]
        )
        time.sleep(0.25)
        self.assertFalse(_pid_alive(int(proc.pid)))
        self.assertIsNone(_reap_pid(int(proc.pid)))  # 已收割
        # _pid_alive 已 waitpid 收割；避免 Popen.__del__ ResourceWarning
        proc.returncode = proc.returncode if proc.returncode is not None else 0

    def test_refresh_revives_error_when_holdout_still_alive(self):
        """error 但 Holdout pid 仍在 → 纠回 running，避免假失败钉死。"""
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ), mock.patch(
                "core.research.yhat_tf_desk._pid_alive", return_value=True
            ), mock.patch(
                "core.research.yhat_tf_desk._reap_pid", return_value=None
            ), mock.patch(
                "core.research.yhat_tf_desk._pid_holdout_check", return_value=True
            ):
                from core.research.yhat_tf_desk import refresh_fit_job

                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "error",
                            "running": False,
                            "pid": 77598,
                            "started_at": "2000-01-01T00:00:00",
                            "finished_at": "2000-01-01T00:01:00",
                            "error": "拟合进程已退出，未回写 metrics",
                            "exit_hint": "early_exit",
                            "params": {"epochs": 2},
                        },
                        f,
                    )
                with open(os.path.join(td, "oo_last.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "action": "holdout_intent",
                            "params": {"epochs": 2},
                            "updated_at": "2000-01-01T00:00:00",
                        },
                        f,
                    )
                job = refresh_fit_job("oo")
                self.assertEqual(job.get("status"), "running")
                self.assertTrue(job.get("running"))
                self.assertNotIn("error", job)
                self.assertNotIn("exit_hint", job)

    def test_refresh_marks_pid_reused_when_command_mismatch(self):
        """running job 的 pid 仍在但不是 Holdout → 标 error，避免假拟合中。"""
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ), mock.patch(
                "core.research.yhat_tf_desk._pid_alive", return_value=True
            ), mock.patch(
                "core.research.yhat_tf_desk._reap_pid", return_value=None
            ), mock.patch(
                "core.research.yhat_tf_desk._pid_command",
                return_value="/usr/bin/sleep 999",
            ):
                from core.research.yhat_tf_desk import refresh_fit_job

                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "running",
                            "running": True,
                            "pid": 55555,
                            "started_at": "2000-01-01T00:00:00",
                            "params": {"epochs": 2},
                        },
                        f,
                    )
                with open(os.path.join(td, "oo_last.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "action": "holdout_intent",
                            "params": {"epochs": 2},
                            "updated_at": "2000-01-01T00:00:00",
                        },
                        f,
                    )
                # 需连续确认 holdout 错读，避免 ps 截断误杀
                mid = refresh_fit_job("oo")
                self.assertEqual(mid.get("status"), "running")
                self.assertEqual(int(mid.get("holdout_miss") or 0), 1)
                refresh_fit_job("oo")
                job = refresh_fit_job("oo")
                self.assertEqual(job.get("status"), "error")
                self.assertFalse(job.get("running"))
                self.assertEqual(job.get("exit_hint"), "pid_reused")
                self.assertIn("复用", job.get("error") or "")
                with open(os.path.join(td, "oo_last.json"), encoding="utf-8") as f:
                    last_blob = json.load(f)
                self.assertEqual(last_blob.get("action"), "holdout_failed")

    def test_refresh_keeps_running_when_holdout_cmd_matches(self):
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ), mock.patch(
                "core.research.yhat_tf_desk._pid_alive", return_value=True
            ), mock.patch(
                "core.research.yhat_tf_desk._reap_pid", return_value=None
            ), mock.patch(
                "core.research.yhat_tf_desk._pid_command",
                return_value="python -u scripts/run_yhat_transformer_holdout.py --head oo",
            ):
                from core.research.yhat_tf_desk import refresh_fit_job

                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "running",
                            "running": True,
                            "pid": 55556,
                            "started_at": "2000-01-01T00:00:00",
                        },
                        f,
                    )
                job = refresh_fit_job("oo")
                self.assertEqual(job.get("status"), "running")
                self.assertTrue(job.get("running"))

    def test_refresh_grace_when_alive_flickers_during_load_bars(self):
        """日线静默期 alive 抖动：有 load_bars 日志时勿一次探测失败就钉死。"""
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ), mock.patch(
                "core.research.yhat_tf_desk._pid_alive", return_value=False
            ), mock.patch(
                "core.research.yhat_tf_desk._reap_pid", return_value=None
            ), mock.patch(
                "core.research.yhat_tf_desk.time.sleep"
            ):
                from core.research.yhat_tf_desk import refresh_fit_job

                with open(os.path.join(td, "oo_fit.log"), "w", encoding="utf-8") as f:
                    f.write(
                        "==== fit start ====\n"
                        "load_bars start n=1600 lookback=1060\n"
                    )
                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "running",
                            "running": True,
                            "pid": 77598,
                            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        },
                        f,
                    )
                job = refresh_fit_job("oo")
                self.assertEqual(job.get("status"), "running")
                self.assertTrue(job.get("running"))
                self.assertGreaterEqual(int(job.get("alive_miss") or 0), 1)

    def test_refresh_marks_dead_when_active_looking_log_is_stale(self):
        """日志里还有 load_bars，但文件早已不更新、进程也不在：不要再挂 45 分钟。"""
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ), mock.patch(
                "core.research.yhat_tf_desk._pid_alive", return_value=False
            ), mock.patch(
                "core.research.yhat_tf_desk._reap_pid", return_value=None
            ), mock.patch(
                "core.research.yhat_tf_desk.time.sleep"
            ):
                from core.research.yhat_tf_desk import refresh_fit_job

                log_path = os.path.join(td, "oo_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(
                        "==== fit start ====\n"
                        "load_bars start n=1600 lookback=1060\n"
                        "日线 1600\n"
                    )
                old = time.time() - 600
                os.utime(log_path, (old, old))
                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "running",
                            "running": True,
                            "pid": 77598,
                            "started_at": "2000-01-01T00:00:00",
                        },
                        f,
                    )
                # 日志已过期 + 进程不在：一次 refresh 即钉死（不必连刷 miss）
                job = refresh_fit_job("oo")
                self.assertEqual(job.get("status"), "error")
                self.assertFalse(job.get("running"))

    def test_reap_orphaned_fit_jobs_settles_dead_running(self):
        """启动收尸：无人轮询时也能把死 pid 的 running job 结算掉。"""
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ), mock.patch(
                "core.research.yhat_tf_desk._pid_alive", return_value=False
            ), mock.patch(
                "core.research.yhat_tf_desk._reap_pid", return_value=None
            ), mock.patch(
                "core.research.yhat_tf_desk.time.sleep"
            ):
                from core.research.yhat_tf_desk import reap_orphaned_fit_jobs

                log_path = os.path.join(td, "oo_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(
                        "ArmL begin F=225\n"
                        "  ArmL live-fit start head=oo n_train=1\n"
                    )
                old = time.time() - 600
                os.utime(log_path, (old, old))
                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "running",
                            "running": True,
                            "pid": 31917,
                            "started_at": "2000-01-01T00:00:00",
                        },
                        f,
                    )
                with open(os.path.join(td, "oo_last.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {"action": "holdout_intent", "updated_at": "2000-01-01T00:00:00"},
                        f,
                    )
                out = reap_orphaned_fit_jobs(factories=["T"])
                self.assertTrue(out.get("success"))
                reaped = out.get("reaped") or []
                self.assertTrue(
                    any(r.get("head") == "oo" and r.get("status") == "error" for r in reaped)
                )
                with open(os.path.join(td, "oo_job.json"), encoding="utf-8") as f:
                    job = json.load(f)
                self.assertEqual(job.get("status"), "error")
                self.assertFalse(job.get("running"))

    def test_head_status_does_not_rewrite_slots(self):
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ):
                from core.research.yhat_tf_desk import head_status

                path = os.path.join(td, "oo_draft.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "role": "yhat_tf_draft",
                            "head": "oo",
                            "params": {"tau_end": "09:30", "d_model": 64, "pool_n": 200},
                        },
                        f,
                    )
                st = head_status("oo")
                self.assertNotIn("tau_end", (st.get("draft") or {}).get("params") or {})
                with open(path, encoding="utf-8") as f:
                    disk = json.load(f)
                self.assertEqual((disk.get("params") or {}).get("tau_end"), "09:30")

    def test_ingest_skips_starting_job_with_pid_zero(self):
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ):
                from core.research.yhat_tf_desk import ingest_holdout_summary

                with open(os.path.join(td, "oo_last.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "action": "holdout_intent",
                            "head": "oo",
                            "updated_at": "2026-01-01T00:00:00",
                        },
                        f,
                    )
                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {"status": "starting", "running": True, "pid": 0, "head": "oo"},
                        f,
                    )
                out = ingest_holdout_summary(
                    "oo",
                    {
                        "arm_L": {"success": True, "cs_rank_ic": 0.1},
                        "arm_T": {"success": True, "cs_rank_ic": 0.2},
                        "hard_gate_vs_arm_L": {"T": {"pass": True, "lift": 0.1}},
                    },
                )
                self.assertTrue(out.get("skipped"))
                self.assertFalse(out.get("success"))
                with open(os.path.join(td, "oo_last.json"), encoding="utf-8") as f:
                    kept = json.load(f)
                self.assertEqual(kept.get("action"), "holdout_intent")
                with open(os.path.join(td, "oo_job.json"), encoding="utf-8") as f:
                    job = json.load(f)
                self.assertEqual(job.get("pid"), 0)
                self.assertEqual(job.get("status"), "starting")

    def test_ingest_accepts_finished_job_with_other_pid(self):
        """已结束的旧 job 只是遗留 pid，不拦下一次回写。"""
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ):
                from core.research.yhat_tf_desk import ingest_holdout_summary

                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "done",
                            "running": False,
                            "pid": os.getpid() + 99999,
                            "head": "oo",
                        },
                        f,
                    )
                out = ingest_holdout_summary(
                    "oo",
                    {
                        "arm_L": {"success": True, "cs_rank_ic": 0.1},
                        "arm_T": {"success": True, "cs_rank_ic": 0.2},
                        "hard_gate_vs_arm_L": {"T": {"pass": True, "lift": 0.1}},
                    },
                )
                self.assertFalse(out.get("skipped"))
                self.assertTrue(out.get("success"))
                self.assertEqual((out.get("last") or {}).get("action"), "holdout_result")

    def test_desk_status_isolates_head_errors(self):
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ), mock.patch(
                "core.research.yhat_tf_desk.head_status",
                side_effect=lambda h: (_ for _ in ()).throw(RuntimeError(f"boom-{h}"))
                if h == "tc"
                else {
                    "head": h,
                    "fit_running": False,
                    "has_draft": False,
                    "has_research": False,
                },
            ):
                from core.research.yhat_tf_desk import desk_status

                st = desk_status()
                self.assertTrue(st["success"])
                by_head = {h["head"]: h for h in st["heads"]}
                self.assertIn("error", by_head["tc"])
                self.assertIn("boom-tc", by_head["tc"]["error"])
                self.assertEqual(by_head["oo"]["head"], "oo")

    def test_start_holdout_early_exit(self):
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ), mock.patch.dict(
                os.environ, {"OMP_NUM_THREADS": "8"}, clear=False
            ), mock.patch(
                "core.research.yhat_tf_desk.subprocess.Popen"
            ) as popen, mock.patch(
                "core.research.yhat_tf_desk._pid_alive", return_value=False
            ), mock.patch(
                "core.research.yhat_tf_desk._reap_pid", return_value=256
            ), mock.patch(
                "core.research.yhat_tf_desk.time.sleep"
            ):
                from core.research.yhat_tf_desk import start_holdout_fit

                proc = mock.Mock()
                proc.pid = 7777

                def _popen(*_a, **kw):
                    # 模拟子进程立刻往日志写 Traceback 后退出（stdout 为裸 fd）
                    log_fd = kw.get("stdout")
                    if isinstance(log_fd, int):
                        os.write(
                            log_fd,
                            b"Traceback (most recent call last):\nImportError: nope\n",
                        )
                    return proc

                popen.side_effect = _popen
                out = start_holdout_fit("oo", {"epochs": 1})
                self.assertFalse(out["success"])
                env = popen.call_args.kwargs.get("env") or {}
                self.assertEqual(env.get("OMP_NUM_THREADS"), "1")
                self.assertEqual(env.get("MKL_NUM_THREADS"), "1")
                self.assertEqual(env.get("OPENBLAS_NUM_THREADS"), "1")
                self.assertEqual(env.get("TORCH_NUM_THREADS"), "4")
                self.assertEqual(env.get("HORIZON_LGB_NUM_THREADS"), "1")
                self.assertTrue(str(env.get("YHAT_FIT_CURVE") or "").endswith("oo_fit.curve.jsonl"))
                self.assertTrue(os.path.isfile(env["YHAT_FIT_CURVE"]))
                self.assertEqual(out["job"].get("exit_hint"), "early_exit")
                self.assertIn("Traceback", out.get("error") or "")
                with open(os.path.join(td, "oo_last.json"), encoding="utf-8") as f:
                    last_blob = json.load(f)
                self.assertEqual(last_blob.get("action"), "holdout_failed")

    def test_refresh_reaps_even_when_metrics_done(self):
        """成功回写 metrics 后也必须 waitpid，否则僵尸残留假死。"""
        import subprocess
        import sys
        import time

        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ):
                from core.research.yhat_tf_desk import refresh_fit_job

                proc = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(0.05)"]
                )
                time.sleep(0.25)
                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "done",
                            "running": False,
                            "pid": int(proc.pid),
                            "started_at": "2026-01-01T00:00:00",
                        },
                        f,
                    )
                with open(os.path.join(td, "oo_last.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "action": "holdout_result",
                            "metrics": {"cs_rank_ic": 0.1},
                            "updated_at": "2026-01-01T00:01:00",
                        },
                        f,
                    )
                job = refresh_fit_job("oo")
                self.assertEqual(job.get("status"), "done")
                self.assertIn("exit_status", job)

                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "running",
                            "running": True,
                            "pid": int(proc.pid),
                            "error": "Arm·T 因子准备失败：裁列",
                            "exit_hint": "partial",
                        },
                        f,
                    )
                kept = refresh_fit_job("oo")
                self.assertEqual(kept.get("status"), "done")
                self.assertFalse(kept.get("running"))
                self.assertIn("裁列", kept.get("error") or "")
                self.assertEqual(kept.get("exit_hint"), "partial")
                proc.returncode = proc.returncode if proc.returncode is not None else 0


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
                self.assertEqual(st0["package"], "yhat_t")
                self.assertEqual(st0["factory"], "T")
                self.assertFalse(st0["live_allowed"])
                self.assertEqual(len(st0["heads"]), 4)
                self.assertIn("run_yhat_transformer_holdout", st0["scheme"]["per_head"]["oo"]["script"])
                self.assertIn("--factory T", st0["scheme"]["per_head"]["oo"]["script"])
                self.assertEqual(st0["scheme"]["per_head"]["tc"]["status"], "holdout_ready")
                self.assertIn("--head tc", st0["scheme"]["per_head"]["tc"]["script"] or "")
                self.assertEqual(
                    st0["scheme"]["per_head"]["oo_rank"]["status"], "holdout_ready"
                )
                self.assertIn(
                    "--head oo_rank",
                    st0["scheme"]["per_head"]["oo_rank"]["script"] or "",
                )
                st_l = desk_status(factory="L")
                self.assertEqual(st_l["package"], "yhat_l")
                self.assertEqual(st_l["scheme"]["per_head"]["oo"]["loss"], "l2")
                self.assertEqual(st_l["scheme"]["per_head"]["tc"]["loss"], "l2")
                self.assertEqual(st_l["scheme"]["per_head"]["oo_rank"]["loss"], "lambdarank")
                self.assertEqual(st_l["scheme"]["per_head"]["tc_rank"]["loss"], "lambdarank")
                self.assertNotIn("smooth_l1", json.dumps(st_l["scheme"]))
                self.assertIn("--factory L", st_l["scheme"]["per_head"]["oo"]["script"])
                self.assertNotIn("--d-model", st_l["scheme"]["per_head"]["oo"]["script"])
                self.assertNotIn("--t-sample-frac", st_l["scheme"]["per_head"]["oo"]["script"])
                self.assertNotIn("--seq-feat-set", st_l["scheme"]["per_head"]["oo"]["script"])
                self.assertNotIn("--full-aoo", st_l["scheme"]["per_head"]["oo"]["script"])

                save_fit_draft(
                    "oo",
                    {"d_model": 32, "batch_size": 16, "pool_n": 200, "loss": "mae"},
                    note="t",
                )
                draft_path = os.path.join(td, "oo_draft.json")
                with open(draft_path, encoding="utf-8") as f:
                    draft_blob = json.load(f)
                self.assertEqual(draft_blob["params"]["watching_limit"], 200)
                self.assertEqual(draft_blob["params"]["loss"], "mse")

                out = record_holdout_intent("oo", {"epochs": 3})
                self.assertTrue(out["success"])
                cli = out.get("cli") or ""
                self.assertIn("run_yhat_transformer_holdout", cli)
                self.assertIn("--watching-limit 200", cli)
                self.assertIn("--epochs 3", cli)
                self.assertIn("--d-model 32", cli)
                self.assertIn("--batch-size 16", cli)
                # 已有 draft 时意图也要覆盖草稿，避免 UI 回退旧值
                with open(os.path.join(td, "oo_draft.json"), encoding="utf-8") as f:
                    draft_after_intent = json.load(f)
                self.assertEqual(draft_after_intent["params"]["epochs"], 3)
                self.assertEqual(draft_after_intent["params"]["d_model"], 32)
                out_r = record_holdout_intent("oo_rank", {"epochs": 3, "pool_n": 100})
                self.assertTrue(out_r["success"])
                self.assertIn("--head oo_rank", out_r.get("cli") or "")
                self.assertIn("--watching-limit 100", out_r.get("cli") or "")
                self.assertEqual(
                    st0["scheme"]["per_head"]["oo"]["gate_label"], "绝对指标 · Arm·T"
                )
                self.assertEqual(
                    st0["scheme"]["per_head"]["oo_rank"]["gate_label"], "绝对指标 · Arm·T"
                )
                self.assertIsNone(st0["scheme"]["per_head"]["oo"]["gate"])

                # 无过闸结果时不可晋级
                promo_block = promote_draft_to_research("oo")
                self.assertFalse(promo_block["success"])
                self.assertIn("不可晋级", str(promo_block.get("error") or ""))

                from core.research.yhat_tf_desk import ingest_holdout_summary, start_holdout_fit

                log_path = os.path.join(td, "tc_fit.log")
                old_job = os.path.join(td, "tc_job.json")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write("OLD LOG LINE SHOULD BE CLEARED\n")
                with open(old_job, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "done",
                            "running": False,
                            "pid": 1,
                            "run_id": "tc-old",
                            "started_at": "2000-01-01T00:00:00",
                        },
                        f,
                    )
                with open(os.path.join(td, "tc_last.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "action": "holdout_result",
                            "metrics": {"cs_rank_ic": 0.99},
                            "updated_at": "2000-01-01T00:00:01",
                        },
                        f,
                    )
                with mock.patch("core.research.yhat_tf_desk.subprocess.Popen") as popen, mock.patch(
                    "core.research.yhat_tf_desk._pid_alive", return_value=True
                ), mock.patch(
                    "core.research.yhat_tf_desk._reap_pid", return_value=None
                ), mock.patch(
                    "core.research.yhat_tf_desk.time.sleep"
                ):
                    proc = mock.Mock()
                    proc.pid = 4242
                    popen.return_value = proc
                    started = start_holdout_fit(
                        "tc", {"epochs": 2, "pool_n": 80, "lookback": 120}
                    )
                self.assertTrue(started["success"])
                self.assertEqual(started["job"]["pid"], 4242)
                self.assertNotEqual(started["job"].get("run_id"), "tc-old")
                self.assertIn("--head tc", started.get("cli") or "")
                self.assertTrue(os.path.isfile(os.path.join(td, "tc_job.json")))
                with open(log_path, encoding="utf-8") as f:
                    log_txt = f.read()
                self.assertNotIn("OLD LOG LINE SHOULD BE CLEARED", log_txt)
                self.assertIn("fit start", log_txt)
                self.assertIn("run=", log_txt)
                with open(os.path.join(td, "tc_last.json"), encoding="utf-8") as f:
                    last_blob = json.load(f)
                self.assertEqual(last_blob.get("action"), "holdout_intent")
                self.assertIsNone(last_blob.get("metrics"))

                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "running",
                            "running": True,
                            "pid": os.getpid(),  # 与 ingest 同进程，允许回写
                        },
                        f,
                    )
                ing = ingest_holdout_summary(
                    "oo",
                    {
                        "gate": "cs_rank_ic",
                        "arm_L": {
                            "success": True,
                            "cs_rank_ic": 0.04,
                            "n_test": 100,
                        },
                        "arm_T": {
                            "success": True,
                            "cs_rank_ic": 0.12,
                            "sign_hit": 0.55,
                        },
                        "split_L": {"n_train": 900, "n_test": 100},
                        "split_T": {"n_train": 880, "n_test": 100},
                        "hard_gate_vs_arm_L": {"T": {"pass": True, "lift": 0.03}},
                        "args": {
                            "lookback": 1000,
                            "d_model": 64,
                            "watching_limit": 800,
                            "update_desk": True,
                            "out": "/tmp/x.json",
                        },
                    },
                    out_path="/tmp/summary.json",
                )
                self.assertTrue(ing["success"])
                self.assertEqual(ing["metrics"].get("cs_rank_ic"), 0.12)
                self.assertEqual(ing["metrics"].get("gate_lift"), 0.03)
                self.assertEqual(ing["metrics"].get("arm"), "T")
                self.assertEqual(ing["metrics"]["arms"]["L"]["cs_rank_ic"], 0.04)
                self.assertEqual(ing["metrics"]["arms"]["T"]["cs_rank_ic"], 0.12)
                self.assertEqual(ing["metrics"]["splits"]["T"]["n_test"], 100)
                self.assertIn("Holdout 完成", (ing.get("last") or {}).get("message") or "")
                partial = ingest_holdout_summary(
                    "oo",
                    {
                        "arm_L": {"success": True, "cs_rank_ic": 0.04},
                        "arm_T": {
                            "success": False,
                            "error": "Arm·T 因子准备失败：ValueError: 裁列",
                        },
                    },
                )
                self.assertTrue(partial["success"])
                self.assertEqual(partial["metrics"].get("cs_rank_ic"), 0.04)
                self.assertIn("裁列", (partial.get("last") or {}).get("message") or "")
                with open(os.path.join(td, "oo_job.json"), encoding="utf-8") as f:
                    job_partial = json.load(f)
                self.assertEqual(job_partial.get("status"), "done")
                self.assertIn("裁列", job_partial.get("error") or "")
                with open(os.path.join(td, "oo_job.json"), encoding="utf-8") as f:
                    job_after = json.load(f)
                self.assertEqual(job_after.get("status"), "done")
                self.assertFalse(job_after.get("running"))
                with open(os.path.join(td, "oo_draft.json"), encoding="utf-8") as f:
                    draft_after = json.load(f)
                self.assertEqual(draft_after["params"]["lookback"], 1000)
                self.assertEqual(draft_after["params"]["d_model"], 64)
                self.assertNotIn("update_desk", draft_after["params"])
                self.assertNotIn("out", draft_after["params"])
                empty = ingest_holdout_summary(
                    "oo",
                    {"gate": "cs_rank_ic", "arm_T": {"success": False}},
                    out_path="/tmp/empty.json",
                )
                self.assertFalse(empty["success"])
                self.assertEqual((empty.get("last") or {}).get("action"), "holdout_failed")
                self.assertIsNone((empty.get("last") or {}).get("metrics"))
                with open(os.path.join(td, "oo_job.json"), encoding="utf-8") as f:
                    job_fail = json.load(f)
                self.assertEqual(job_fail.get("status"), "error")
                self.assertIn("--watching-limit", st0["scheme"]["per_head"]["oo"]["script"])

                # 双臂皆败但有硬门：failed + 保留 gate_pass
                both_fail = ingest_holdout_summary(
                    "oo",
                    {
                        "arm_L": {"success": False, "error": "L boom"},
                        "arm_T": {"success": False, "error": "T boom"},
                        "hard_gate_vs_arm_L": {
                            "T": {"pass": False, "reasons": ["双败"]}
                        },
                    },
                )
                self.assertFalse(both_fail["success"])
                self.assertEqual(
                    (both_fail.get("last") or {}).get("action"), "holdout_failed"
                )
                self.assertEqual(
                    (both_fail.get("last") or {}).get("metrics", {}).get("gate_pass"),
                    False,
                )

                # 失败回写不得继承上次 experiment_out（否则 UI 仍显示旧 L/T）
                with open(os.path.join(td, "oo_last.json"), encoding="utf-8") as f:
                    last_both = json.load(f)
                self.assertIsNone(last_both.get("experiment_out"))

                # 旧进程不得踩新 job
                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "running",
                            "running": True,
                            "pid": os.getpid() + 99999,
                            "params": {"d_model": 256},
                        },
                        f,
                    )
                prev_last = (both_fail.get("last") or {}).get("updated_at")
                stale = ingest_holdout_summary(
                    "oo",
                    {
                        "arm_L": {"success": True, "cs_rank_ic": 0.99},
                        "arm_T": {"success": True, "cs_rank_ic": 0.99},
                        "hard_gate_vs_arm_L": {"T": {"pass": True, "lift": 0.1}},
                    },
                )
                self.assertTrue(stale.get("skipped"))
                self.assertFalse(stale.get("success"))
                with open(os.path.join(td, "oo_job.json"), encoding="utf-8") as f:
                    job_stale = json.load(f)
                self.assertTrue(job_stale.get("running"))
                self.assertEqual(job_stale.get("pid"), os.getpid() + 99999)
                with open(os.path.join(td, "oo_last.json"), encoding="utf-8") as f:
                    last_stale = json.load(f)
                self.assertEqual(last_stale.get("updated_at"), prev_last)

    def test_promote_gate_parity_and_protocol(self):
        with tempfile.TemporaryDirectory() as td:
            last = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last
            ):
                from core.research.yhat_tf_desk import (
                    default_fit_params,
                    harden_fit_params,
                    ingest_holdout_summary,
                    promote_draft_to_research,
                    save_fit_draft,
                )
                from core.research.yhat_transformer.heads import (
                    ARM_T_BATCH_MAX,
                    ARM_T_BATCH_MIN,
                )

                # batch 跟训练入口同一对边界；factor_pack 跟 T 列宽；模型超参进 CLI
                p8, warns = harden_fit_params(
                    "oo", {"batch_size": ARM_T_BATCH_MIN - 1, "full_aoo": False}
                )
                self.assertEqual(p8["batch_size"], ARM_T_BATCH_MIN)
                self.assertTrue(any("batch" in w for w in warns))
                p_ok, warns_ok = harden_fit_params(
                    "oo", {"batch_size": ARM_T_BATCH_MAX, "full_aoo": False}
                )
                self.assertEqual(p_ok["batch_size"], ARM_T_BATCH_MAX)
                self.assertFalse(any(w.startswith("batch") for w in warns_ok))
                p_hi, warns_hi = harden_fit_params(
                    "oo", {"batch_size": ARM_T_BATCH_MAX + 1, "full_aoo": False}
                )
                self.assertEqual(p_hi["batch_size"], ARM_T_BATCH_MAX)
                self.assertTrue(any("batch" in w for w in warns_hi))
                self.assertEqual(p8["factor_pack"], "aoo_core")
                p_full, _ = harden_fit_params(
                    "oo",
                    {
                        "batch_size": 32,
                        "full_aoo": True,
                        "t_sample_pct": 100,
                        "mlp_hidden": 128,
                        "dropout": 0.2,
                        "patience": 6,
                        "nhead": 8,
                        "d_model": 64,
                        "lr": 5e-4,
                    },
                )
                self.assertEqual(p_full["factor_pack"], "aoo_full")
                self.assertNotIn("mlp_hidden", p_full)
                self.assertEqual(p_full["nhead"], 8)
                self.assertEqual(p_full["dropout"], 0.2)
                self.assertNotIn("patience", p_full)
                p_def, _ = harden_fit_params(
                    "oo", {"d_model": 128, "full_aoo": True}
                )
                self.assertEqual(p_def["nhead"], 4)
                self.assertNotIn("mlp_hidden", p_def)
                self.assertEqual(p_def["dropout"], 0.1)
                self.assertEqual(p_def["lr"], 1e-3)
                self.assertNotIn("patience", p_def)
                from core.research.yhat_tf_desk import build_holdout_cli

                cli = build_holdout_cli("oo", p_full)
                self.assertNotIn("--mlp-hidden", cli)
                self.assertIn("--dropout 0.2", cli)
                self.assertNotIn("--patience", cli)
                self.assertIn("--nhead 8", cli)
                self.assertIn("--lr 0.0005", cli)
                self.assertEqual(
                    default_fit_params("oo_rank")["holdout_trading_days"], 60
                )

                save_fit_draft(
                    "oo",
                    {
                        "full_aoo": True,
                        "t_sample_pct": 100,
                        "d_model": 32,
                        "seq_feat_set": "seq16",
                    },
                )
                blocked = promote_draft_to_research("oo")
                self.assertFalse(blocked["success"])

                ing = ingest_holdout_summary(
                    "oo",
                    {
                        "arm_L": {"success": True, "cs_rank_ic": 0.04},
                        "arm_T": {"success": True, "cs_rank_ic": 0.10},
                        "hard_gate_vs_arm_L": {"T": {"pass": True, "lift": 0.06}},
                        "eval_universe": {
                            "n_l_test": 120,
                            "n_t_test": 100,
                            "n_shared": 100,
                            "eval_shared": True,
                        },
                        "parity": {
                            "factor_width_match": True,
                            "train_sample_full": True,
                            "eval_shared": True,
                            "formal": True,
                        },
                        "args": {
                            "full_aoo": True,
                            "seq_feat_set": "seq_full",
                            "t_sample_frac": 1.0,
                            "d_model": 32,
                            "watching_limit": 800,
                            "lookback": 900,
                        },
                    },
                    out_path="/tmp/yhat_promo.json",
                )
                self.assertTrue(ing["success"])
                self.assertTrue(ing["metrics"].get("gate_pass"))
                self.assertTrue((ing.get("last") or {}).get("parity", {}).get("formal"))

                ok = promote_draft_to_research("oo")
                self.assertTrue(ok["success"], ok.get("error"))
                self.assertFalse(ok["research"]["live_allowed"])
                self.assertIn("promoted_from_last", ok["research"])
                self.assertTrue(
                    ok["research"]["promoted_from_last"].get("gate_pass")
                )

                # 改草稿协议 → 不可晋级
                save_fit_draft(
                    "oo",
                    {
                        "full_aoo": True,
                        "t_sample_pct": 10,
                        "d_model": 32,
                        "seq_feat_set": "seq16",
                    },
                )
                drift = promote_draft_to_research("oo")
                self.assertFalse(drift["success"])
                self.assertIn("不一致", str(drift.get("error") or ""))

                # 旧 last.fit_params 缺 dropout/lr 不应误拦晋级
                from core.research.yhat_tf_desk import promote_eligibility

                draft_ok = {
                    "params": {
                        "full_aoo": True,
                        "t_sample_pct": 100,
                        "d_model": 32,
                        "seq_feat_set": "seq16",
                        "seq_len": 20,
                        "lookback": 900,
                        "holdout_trading_days": 60,
                        "nlayers": 3,
                        "batch_size": 32,
                        "epochs": 12,
                        "arms": "L,T",
                        "loss": "mse",
                    }
                }
                last_sparse = {
                    "action": "holdout_result",
                    "head": "oo",
                    "metrics": {"gate_pass": True},
                    "parity": {
                        "factor_width_match": True,
                        "train_sample_full": True,
                        "eval_shared": True,
                        "formal": True,
                    },
                    "fit_params": {
                        "full_aoo": True,
                        "t_sample_pct": 100,
                        "d_model": 32,
                        "seq_feat_set": "seq16",
                        "seq_len": 20,
                        "lookback": 900,
                        "holdout_trading_days": 60,
                        "nlayers": 3,
                        "batch_size": 32,
                        "epochs": 12,
                        "arms": "L,T",
                        "loss": "mse",
                        # 故意缺 dropout / lr
                    },
                }
                elig = promote_eligibility(
                    "oo", last=last_sparse, draft=draft_ok
                )
                self.assertTrue(
                    elig.get("promotable"),
                    elig.get("reasons"),
                )


class TestYhatSharedEvalCalendar(unittest.TestCase):
    def test_shared_cal_aligns_eval_start_when_fac_longer(self):
        """fac 日域长于 seq 时，须用 shared_cal，否则 L/T Holdout 窗错位。"""
        from core.research.holdout import resolve_ridge_split

        base = [f"2024-01-{d:02d}" for d in range(2, 32)] + [
            f"2024-02-{d:02d}" for d in range(1, 29)
        ]
        fac_dates = list(base)
        seq_dates = list(base[:-5])
        shared_cal = sorted({str(d)[:10] for d in seq_dates})

        def _split(dates, cal=None):
            src = cal if cal is not None else dates
            c = sorted({str(d)[:10] for d in src if str(d)[:10]})
            _, _, meta = resolve_ridge_split(
                list(dates),
                holdout_trading_days=10,
                label_horizon_days=1,
                calendar_dates=c,
            )
            return meta.get("eval_start")

        self.assertNotEqual(
            _split(fac_dates),
            _split(seq_dates),
            "对照组：各自日历应错位",
        )
        self.assertEqual(
            _split(fac_dates, shared_cal),
            _split(seq_dates, shared_cal),
        )

    def test_l_pretrain_keeps_early_fac_days(self):
        """共享日历定 eval 窗时，L 训练应保留 fac 早于 shared 起点的日期。"""
        from core.research.holdout import resolve_ridge_split

        fac_dates = [f"2024-01-{d:02d}" for d in range(2, 32)]
        shared_cal = [f"2024-01-{d:02d}" for d in range(12, 32)]
        _, _, meta = resolve_ridge_split(
            list(shared_cal),
            holdout_trading_days=5,
            label_horizon_days=1,
            calendar_dates=shared_cal,
        )
        test_days = {str(d)[:10] for d in (meta.get("test_days") or [])}
        embargo_days = {str(d)[:10] for d in (meta.get("embargo_days") or [])}
        eval_start = str(meta.get("eval_start") or "")[:10]
        tr_days = set()
        for d in fac_dates:
            ds = str(d)[:10]
            if ds in test_days or ds in embargo_days:
                continue
            if eval_start and ds >= eval_start:
                continue
            tr_days.add(ds)
        self.assertTrue(any(d < shared_cal[0] for d in tr_days))
        self.assertTrue(all(d < eval_start for d in tr_days))

    def test_arml_lookback_drops_pad_and_keeps_holdout(self):
        """垫根不进 Arm·L 训练；Holdout 截止日不变。窗内早于序列的因子日仍保留。"""
        from core.research.holdout import resolve_ridge_split
        from scripts.run_yhat_transformer_holdout import (
            arml_fit_calendar,
            arml_keep_train_day,
        )

        full = [f"2024-03-{d:02d}" for d in range(1, 32)]
        lookback = 20
        fit = arml_fit_calendar(full, lookback)
        self.assertEqual(fit, full[-lookback:])
        self.assertEqual(arml_fit_calendar(full, None), full)

        _, _, wide = resolve_ridge_split(
            full,
            holdout_trading_days=5,
            label_horizon_days=1,
            calendar_dates=full,
        )
        _, _, clipped = resolve_ridge_split(
            full,
            holdout_trading_days=5,
            label_horizon_days=1,
            calendar_dates=fit,
        )
        self.assertEqual(wide.get("eval_start"), clipped.get("eval_start"))
        self.assertEqual(list(wide.get("test_days") or []), list(clipped.get("test_days") or []))
        self.assertEqual((clipped.get("train_days") or [None])[0], fit[0])
        pad = set(full[:-lookback])
        self.assertTrue(pad.isdisjoint(clipped.get("train_days") or []))
        self.assertTrue(pad.issubset(set(wide.get("train_days") or [])))

        test_days = set(clipped.get("test_days") or [])
        embargo = set(clipped.get("embargo_days") or [])
        eval_start = clipped.get("eval_start")
        inside = fit[1]
        shared_start = fit[4]
        self.assertLess(inside, shared_start)
        self.assertTrue(
            arml_keep_train_day(
                inside,
                test_days=test_days,
                embargo_days=embargo,
                eval_start=eval_start,
                train_start=fit[0],
            )
        )
        self.assertFalse(
            arml_keep_train_day(
                full[0],
                test_days=test_days,
                embargo_days=embargo,
                eval_start=eval_start,
                train_start=fit[0],
            )
        )
        self.assertFalse(
            arml_keep_train_day(
                next(iter(test_days)),
                test_days=test_days,
                embargo_days=embargo,
                eval_start=eval_start,
                train_start=fit[0],
            )
        )

    def test_clip_train_start_to_open_cube(self):
        """训练窗早于立方时收到立方日；立方落进 Holdout 时不裁。"""
        from scripts.run_yhat_transformer_holdout import clip_train_to_open_cube

        dates = ["2022-07-01", "2022-07-04", "2024-01-02"]
        kept, start, n_drop = clip_train_to_open_cube(
            [0, 1, 2],
            dates,
            train_start="2022-07-01",
            eval_start="2026-10-20",
            cube_start="2022-07-04",
        )
        self.assertEqual(kept, [1, 2])
        self.assertEqual(start, "2022-07-04")
        self.assertEqual(n_drop, 1)

        kept_hold, start_hold, n_hold = clip_train_to_open_cube(
            [0, 1],
            ["2022-07-01", "2022-07-04"],
            train_start="2022-07-01",
            eval_start="2022-07-04",
            cube_start="2022-07-04",
        )
        self.assertEqual(kept_hold, [0, 1])
        self.assertEqual(start_hold, "2022-07-01")
        self.assertEqual(n_hold, 0)

    def test_metrics_gate_pass_when_t_fails(self):
        from core.research.yhat_tf_desk import _metrics_from_holdout_summary

        m = _metrics_from_holdout_summary(
            {
                "arm_L": {"success": True, "cs_rank_ic": 0.05},
                "arm_T": {"success": False, "error": "boom"},
                "hard_gate_vs_arm_L": {
                    "T": {"pass": False, "lift": None, "reasons": ["Arm·T 失败"]}
                },
            }
        )
        self.assertEqual(m.get("gate_pass"), False)
        self.assertEqual(m.get("arm"), "L")

        both = _metrics_from_holdout_summary(
            {
                "arm_L": {"success": False, "error": "L"},
                "arm_T": {"success": False, "error": "T"},
                "hard_gate_vs_arm_L": {
                    "T": {"pass": False, "reasons": ["双败"]}
                },
            }
        )
        self.assertEqual(both.get("gate_pass"), False)


class TestYhatTfPageWiring(unittest.TestCase):
    def test_palette_and_render(self):
        from web.page_html import clear_html_cache, render_tool_html

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pal = os.path.join(root, "web/static/js/command_palette.js")
        with open(pal, encoding="utf-8") as f:
            pal_js = f.read()
        self.assertIn('id: "nav-yhat-t"', pal_js)
        self.assertIn('id: "nav-yhat-l"', pal_js)
        self.assertIn("/yhat-t", pal_js)
        self.assertIn("/yhat-l", pal_js)
        self.assertIn("模型中心 Arm·T", pal_js)
        self.assertIn("模型中心 Arm·L", pal_js)
        self.assertIn('id: "nav-yhat-r"', pal_js)
        self.assertIn("/yhat-r", pal_js)
        self.assertIn("模型中心 Arm·R", pal_js)
        self.assertIn('id: "nav-yhat-bt"', pal_js)
        self.assertIn("/yhat-bt", pal_js)
        self.assertIn("回测中心", pal_js)

        clear_html_cache()
        html_t = render_tool_html("yhat_t")
        html_l = render_tool_html("yhat_l")
        html_r = render_tool_html("yhat_r")
        html = html_t  # 契约主检 Arm·T 页
        self.assertIn("模型中心 · Arm·T", html_t)
        self.assertIn("模型中心 · Arm·L", html_l)
        self.assertIn("模型中心 · Arm·R", html_r)
        self.assertIn("<title>QuantLab · 模型中心 · Arm·T</title>", html_t)
        self.assertIn("<title>QuantLab · 模型中心 · Arm·L</title>", html_l)
        self.assertIn("<title>QuantLab · 模型中心 · Arm·R</title>", html_r)
        self.assertIn('data-factory="T"', html_t)
        self.assertIn('data-factory="L"', html_l)
        self.assertIn('data-factory="R"', html_r)
        self.assertIn('data-head-panel="oo_rank"', html)
        self.assertIn('data-head-select="oo_rank"', html)
        self.assertIn('data-head-section="tc_rank"', html)
        self.assertIn('href="#yhat-tf-section-oo-rank"', html)
        self.assertNotIn('id="yhat-tf-toggle-view"', html)
        self.assertNotIn("全部展开", html)
        self.assertIn("yhat-tf-protocol", html)
        self.assertNotIn('data-param="protocol_preset"', html)
        self.assertIn("data-fit-contrast", html)
        self.assertNotIn("快速消融", html)
        self.assertNotIn("默认对照", html)
        self.assertNotIn("贴 live", html)
        self.assertIn('data-param-radio="seq_feat_set"', html)
        self.assertNotIn('data-param-radio="full_aoo"', html)
        self.assertNotIn('data-param-radio="full_atc"', html)
        self.assertNotIn('type="hidden" data-param="seq_len"', html)
        self.assertIn("core · 61", html)
        self.assertIn("full · 218", html)
        self.assertNotIn("core · 55", html)
        self.assertNotIn("full · 230", html)
        self.assertNotIn(">atc_core<", html)
        self.assertIn("yhat-tf-param-group", html)
        self.assertNotIn("yhat-tf-arm-picks", html)
        self.assertIn("yhat-tf-arms-title", html)
        self.assertIn("本厂模型臂", html_t)
        self.assertIn("本厂模型臂", html_l)
        self.assertIn("open[T]→open[T+1] · LightGBM", html_l)
        self.assertIn("CS rank · LambdaRank", html_l)
        self.assertIn('data-arm-console="L"', html_l)
        self.assertIn("data-contrast-protocol", html_l)
        self.assertIn('data-pool="pool"', html_l)
        self.assertIn('type="radio" name="pool_set" data-pool="pool"', html_l)
        self.assertNotIn('type="checkbox" data-pool="pool"', html_l)
        self.assertIn('data-pool="pool"', html_r)
        self.assertIn('type="checkbox" data-pool="pool"', html_t)
        self.assertNotIn('type="radio" name="pool_set"', html_t)
        self.assertNotIn("池子（股）", html_t)
        self.assertNotIn("{{FEATURE_CONTROL}}", html_t)
        self.assertIn('data-param="tau_end"', html_l)
        for gone in (
            'data-param-radio="seq_feat_set"',
            'data-param="t_sample_pct"',
            'data-param="d_model"',
            'data-param="batch_size"',
            'data-param="loss"',
            'data-arm-console="T"',
            'data-arm-console="R"',
            'data-param="ridge_alpha"',
            "data-contrast-lift",
            "yhat-only:T",
            "ListNet",
            "T采样%",
            "SmoothL1",
            "smooth_l1",
            '<option value="mse"',
            "序列 Transformer",
            "Scheme LT",
        ):
            self.assertNotIn(gone, html_l, msg=gone)
        self.assertIn(
            'data-param="lookback" type="number" min="100" max="1200" step="10" value="600"',
            html_l,
        )
        self.assertIn(
            'data-param="lookback" type="number" min="100" max="1200" step="10" value="120"',
            html_l,
        )
        self.assertIn(
            'data-param="holdout_trading_days" type="number" min="5" max="90" step="1" value="20"',
            html_l,
        )
        self.assertNotIn('value="900"', html_l)
        self.assertNotIn('value="60"', html_l)
        self.assertIn("open[T]→open[T+1] · Ridge", html_r)
        self.assertIn("CS rank · Ridge", html_r)
        self.assertIn('data-arm-console="R"', html_r)
        self.assertIn('data-param="ridge_alpha"', html_r)
        self.assertNotIn('data-arm-console="L"', html_r)
        self.assertNotIn('data-arm-console="T"', html_r)
        self.assertNotIn('data-param="d_model"', html_r)
        self.assertIn(
            'data-param="lookback" type="number" min="100" max="1200" step="10" value="600"',
            html_r,
        )
        self.assertIn('value="1000"', html_t)
        self.assertIn('value="128"', html_t)
        self.assertIn('value="2048"', html_t)
        self.assertIn(
            'data-param="epochs" type="number" min="1" max="40" step="1" value="20"',
            html_t,
        )
        self.assertNotIn('value="256"', html_t)
        self.assertIn('value="100"', html_t)
        self.assertIn('data-pool="hs300" value="hs300" checked', html_t)
        self.assertIn('value="60"', html_t)
        self.assertNotIn("实验臂与 Gate 说明", html)
        self.assertNotIn("yhat-tf-arms-fold", html)
        self.assertNotIn('data-arm-console="L"', html_t)
        self.assertIn('data-arm-console="T"', html_t)
        self.assertNotIn("yhat-only:L", html_t)
        self.assertNotIn("data-contrast-lift", html)
        self.assertIn("序列 Transformer", html_t)
        self.assertNotIn('data-arm="0"', html)
        self.assertNotIn('data-arm="A"', html)
        self.assertIn('data-param="loss"', html)
        self.assertIn("<option value=\"lambdarank\"", html)
        self.assertIn("<option value=\"mse\"", html)
        self.assertNotIn("<option value=\"smooth_l1\"", html)
        self.assertIn("open[T]→open[T+1] · mse", html)
        self.assertIn("price[τ]→close[T] · mse", html)
        self.assertNotIn("· smooth_l1", html)
        self.assertNotIn('data-param="arms" type="text"', html)
        self.assertNotIn('data-param="loss" type="text"', html)
        from core.research.yhat_transformer.heads import (
            ARM_T_BATCH,
            ARM_T_BATCH_MAX,
            ARM_T_BATCH_MIN,
            ARM_T_D_MODEL,
            ARM_T_EPOCHS,
            ARM_T_HOLDOUT,
            ARM_T_LOOKBACK,
            ARM_T_NLAYERS,
            ARM_T_SEQ_LEN,
            ARM_T_SEQ_MIN,
        )

        batch_bounds = f'min="{ARM_T_BATCH_MIN}" max="{ARM_T_BATCH_MAX}"'
        seq_len = (
            'data-param="seq_len" type="number" '
            f'min="{ARM_T_SEQ_MIN}" max="{ARM_T_SEQ_LEN}" step="1" value="{ARM_T_SEQ_LEN}"'
        )
        fit_defaults = (
            seq_len,
            f'data-param="d_model" type="number" min="16" step="8" value="{ARM_T_D_MODEL}"',
            f'data-param="nlayers" type="number" min="1" max="6" step="1" value="{ARM_T_NLAYERS}"',
            (
                'data-param="batch_size" type="number" '
                f'min="{ARM_T_BATCH_MIN}" max="{ARM_T_BATCH_MAX}" step="8" value="{ARM_T_BATCH}"'
            ),
            (
                'data-param="epochs" type="number" '
                f'min="1" max="40" step="1" value="{ARM_T_EPOCHS}"'
            ),
        )
        factory_windows = (
            (
                'data-param="lookback" type="number" '
                f'min="100" max="1200" step="10" value="{ARM_T_LOOKBACK}"'
            ),
            (
                'data-param="holdout_trading_days" type="number" '
                f'min="5" max="90" step="1" value="{ARM_T_HOLDOUT}"'
            ),
        )
        self.assertEqual(html_t.count(batch_bounds), 4)
        hub_path = os.path.join(root, "web/static/partials/quant_panel.html")
        with open(hub_path, encoding="utf-8") as f:
            hub_html = f.read()
        self.assertEqual(hub_html.count(batch_bounds), 4)
        for snippet in fit_defaults:
            self.assertEqual(html_t.count(snippet), 4, msg=snippet)
            self.assertEqual(hub_html.count(snippet), 4, msg=snippet)
        for snippet in factory_windows:
            self.assertEqual(html_t.count(snippet), 4, msg=snippet)
        import re

        self.assertEqual(
            len(
                re.findall(
                    rf'data-param="lookback"[^>]*value="{ARM_T_LOOKBACK}"',
                    hub_html,
                )
            ),
            4,
        )
        self.assertEqual(
            len(
                re.findall(
                    rf'data-param="holdout_trading_days"[^>]*value="{ARM_T_HOLDOUT}"',
                    hub_html,
                )
            ),
            4,
        )
        hub_js = os.path.join(root, "web/static/js/quant/hub_arm_t.js")
        with open(hub_js, encoding="utf-8") as f:
            hub_js_src = f.read()
        for key, num in (
            ("d_model", ARM_T_D_MODEL),
            ("nlayers", ARM_T_NLAYERS),
            ("batch_size", ARM_T_BATCH),
            ("epochs", ARM_T_EPOCHS),
            ("seq_len", ARM_T_SEQ_LEN),
        ):
            self.assertIn(f'sectionNum(section, "{key}", {num})', hub_js_src)
        self.assertIn(
            f"T: {{ holdout: {ARM_T_HOLDOUT}, lookback: {{ oo: {ARM_T_LOOKBACK}, tc: {ARM_T_LOOKBACK}, oo_rank: {ARM_T_LOOKBACK}, tc_rank: {ARM_T_LOOKBACK} }} }}",
            hub_js_src,
        )
        # 四头字段对齐：模型 d_model/layers/loss · 训练 batch/epochs/T采样%；仅 τc* 多 tau_end
        # nhead/mlp/dropout/lr 不进页面（CLI/草稿可配）
        self.assertIn("T采样%", html)
        self.assertIn('data-param="t_sample_pct"', html)
        self.assertNotIn('data-param="max_train_rows"', html)
        self.assertNotIn('data-arm="', html)  # 臂勾选已下线
        for hidden in ("nhead", "mlp_hidden", "dropout", "lr", "patience"):
            self.assertNotIn(f'data-param="{hidden}"', html)
        self.assertNotIn('data-gate-item="hard_gate"', html)
        self.assertNotIn("data-slots-for=", html)
        self.assertNotIn("data-gate-for=", html)
        self.assertNotIn("data-result-summary-for=", html)
        self.assertNotIn('data-act="save-draft"', html)
        self.assertNotIn('data-act="promote-research"', html)
        self.assertNotIn("yhat-tf-more", html)
        self.assertNotIn("data-overview=", html)
        self.assertNotIn("data-head-summary-slots=", html)
        self.assertNotIn("草稿槽", html)
        self.assertNotIn("Holdout 有结果", html)
        self.assertNotIn("禁 live", html)
        self.assertNotIn("不写 active / score", html)
        self.assertIn("研究轨 · 不写 live", html)

        # 旧 CLI / 列标准化路径已下线
        script = os.path.join(root, "scripts", "run_yhat_transformer_holdout.py")
        with open(script, encoding="utf-8") as f:
            self.assertNotIn("--max-train-rows", f.read())
        fac_py = os.path.join(
            root, "core", "research", "yhat_transformer", "factor_panel.py"
        )
        with open(fac_py, encoding="utf-8") as f:
            self.assertNotIn("def column_standardize_train", f.read())
        js = os.path.join(root, "web", "static", "js", "yhat_tf.js")
        with open(js, encoding="utf-8") as f:
            js_src = f.read()
        self.assertNotIn("function readArms", js_src)
        self.assertNotIn("function applyArms", js_src)
        self.assertNotIn("function parseArms", js_src)
        for head in ("oo", "tc", "oo_rank", "tc_rank"):
            panel = html.split(f'data-head-panel="{head}"', 1)[1].split(
                "data-head-panel=", 1
            )[0]
            for key in (
                "lookback",
                "holdout_trading_days",
                "d_model",
                "nlayers",
                "loss",
                "batch_size",
                "epochs",
                "t_sample_pct",
                "seq_len",
            ):
                self.assertIn(f'data-param="{key}"', panel, msg=f"{head}.{key}")
            self.assertIn('type="checkbox" data-pool="pool"', panel, msg=f"{head}.pool")
            self.assertIn('data-pool="hs300"', panel, msg=f"{head}.hs300")
            self.assertNotIn("yhat-tf-arm-picks", panel, msg=f"{head}.no_arm_picks")
            if head in ("tc", "tc_rank"):
                self.assertIn('data-param="tau_end"', panel, msg=f"{head}.tau_end")
            else:
                self.assertNotIn('data-param="tau_end"', panel, msg=f"{head}.no_tau")
        self.assertNotIn("yhat-tf-result-grid", html)
        self.assertIn("yhat-tf-tech", html)
        self.assertNotIn("OOS 指标 · 待回传", html)
        self.assertIn("生产禁用", html)
        self.assertEqual(html.count('data-act="fit"'), 4)
        self.assertEqual(html.count("data-version-for"), 4)
        version_box = (
            '<span class="yhat-tf-version-box"><button type="button" class="yhat-tf-version-btn"'
        )
        for page in (html_t, html_l, html_r):
            self.assertEqual(page.count("yhat-tf-version-box"), 4)
            self.assertEqual(page.count(version_box), 4)
            self.assertEqual(
                page.count(
                    '</button><div class="yhat-tf-version-menu" role="listbox" hidden></div></span>'
                ),
                4,
            )
        self.assertNotIn("工作配置", html)
        self.assertIn("尚无版本", html)
        self.assertIn(">拟合</button>", html)
        self.assertEqual(html.count("yhat-tf-output-sec--console"), 4)
        self.assertNotIn("生成拟合命令", html)
        self.assertIn('data-fit-info-for="oo"', html)
        self.assertIn('data-fit-info-for="tc_rank"', html)
        self.assertIn('data-fit-info-note', html)
        with open(
            os.path.join(root, "web/static/js/yhat_tf.js"), encoding="utf-8"
        ) as f:
            yhat_js = f.read()
        self.assertIn("再点一次展开全部四头", yhat_js)
        self.assertIn('viewMode = "all"', yhat_js)
        self.assertNotIn("yhat-tf-toggle-view", yhat_js)
        self.assertIn("function currentFitMetrics", yhat_js)
        self.assertIn("function setFitInfoNote", yhat_js)
        self.assertIn(
            'Object.prototype.hasOwnProperty.call(pool, "train_stage")',
            yhat_js,
        )
        self.assertIn("勿回退 research/draft", yhat_js)
        # 未在跑时优先工作配置（隐式 draft），避免旧 job 盖回表单
        self.assertIn("st.draft?.params || lastParams || st.job?.params", yhat_js)
        self.assertNotIn("nDraft", yhat_js)
        self.assertNotIn("/api/quant/yhat-tf/draft", yhat_js)
        self.assertNotIn("/api/quant/yhat-tf/promote-research", yhat_js)
        self.assertIn("/api/quant/yhat-l", yhat_js)
        self.assertIn("/api/quant/yhat-t", yhat_js)
        self.assertIn("/api/quant/yhat-r", yhat_js)
        place = yhat_js.split("function placeVersionMenu", 1)[1].split(
            "function ", 1
        )[0]
        self.assertIn('menu.style.left = ""', place)
        self.assertIn('menu.style.right = ""', place)
        self.assertNotIn("menu.style.width", place)
        self.assertNotIn("menu.style.minWidth", place)
        self.assertIn("/versions?head=", yhat_js)
        self.assertIn("&suite=research", yhat_js)
        self.assertIn("function hideFinishedExecution", yhat_js)
        self.assertIn('String(st.job?.suite || "") !== "live"', yhat_js)
        self.assertIn("/versions/preview?", yhat_js)
        self.assertIn('method: "DELETE"', yhat_js)
        self.assertIn("data-version-delete", yhat_js)
        self.assertNotIn("/api/quant/model-versions", yhat_js)
        self.assertIn("initYhatTf", yhat_js)
        self.assertIn("pageFactory", yhat_js)
        self.assertNotIn(
            "st.job?.params ||\n      (st.last?.params",
            yhat_js,
        )
        self.assertIn("holdout_failed", yhat_js)
        self.assertIn("isCurrentFail", yhat_js)
        self.assertIn("st.job?.params", yhat_js)
        self.assertIn("quiet: true", yhat_js)
        self.assertIn("function fitPollDelayMs", yhat_js)
        self.assertIn("document.hidden", yhat_js)
        self.assertIn("visibilitychange", yhat_js)
        self.assertIn('if (act === "fit")', yhat_js)
        self.assertIn("const ENTER = 96", yhat_js)
        self.assertNotIn("function metricsOf", yhat_js)
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
        btn_css = css.split("\n.yhat-tf-version-btn {", 1)[1].split("}", 1)[0]
        self.assertIn("width: max-content", btn_css)
        self.assertIn("min-width: 24rem", btn_css)
        menu_css = css.split(".yhat-tf-version-menu {", 1)[1].split("}", 1)[0]
        self.assertIn("left: 0", menu_css)
        self.assertIn("right: 0", menu_css)
        self.assertIn("width: auto", menu_css)
        self.assertNotIn("28rem", menu_css)
        self.assertNotIn("max(100%", menu_css)


if __name__ == "__main__":
    unittest.main()
