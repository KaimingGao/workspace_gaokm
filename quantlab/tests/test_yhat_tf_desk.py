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
    def test_page_d_model_realigns_auto_mlp_hidden(self):
        """页面改 d_model 时，草稿里「自动对齐」的 mlp_hidden 不得粘住旧值。"""
        from core.research.yhat_tf_desk import (
            _merge_request_over_draft,
            _normalize_fit_params,
        )

        draft = _normalize_fit_params("oo", {"d_model": 64})
        self.assertEqual(draft["mlp_hidden"], 64)
        page = {"d_model": 128, "pool_n": 800}
        merged = _merge_request_over_draft(draft, page)
        out = _normalize_fit_params("oo", merged)
        self.assertEqual(out["d_model"], 128)
        self.assertEqual(out["mlp_hidden"], 128)
        # 显式覆盖（≠旧 d_model）保留
        draft2 = _normalize_fit_params(
            "oo", {"d_model": 64, "mlp_hidden": 32}
        )
        self.assertEqual(draft2["mlp_hidden"], 32)
        out2 = _normalize_fit_params(
            "oo",
            _merge_request_over_draft(draft2, {"d_model": 128}),
        )
        self.assertEqual(out2["mlp_hidden"], 32)

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
        self.assertEqual(out["arms"], "L,T")  # 非法臂码回落 L,T
        self.assertNotIn("seeds", out)  # 固定 seed=42，不再入参
        self.assertFalse(any("长窗" in w for w in warns))
        def_p, _ = harden_fit_params("oo", {})
        self.assertNotIn("seeds", def_p)
        norm = _normalize_fit_params("tc", {"lookback": 900, "holdout_trading_days": 60})
        self.assertEqual(norm["lookback"], 900)
        self.assertEqual(norm["holdout_trading_days"], 60)
        self.assertEqual(norm["arms"], "L,T")
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
        self.assertEqual(oo["seq_feat_set"], "seq32")
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

        oo, warns = harden_fit_params("oo", {"loss": "mse", "lr": 5e-4})
        self.assertEqual(oo["loss"], "smooth_l1")
        self.assertTrue(any("loss:" in w and "mse→smooth_l1" in w for w in warns))
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

        self.assertEqual(default_fit_params("oo")["t_sample_pct"], 10.0)
        keep, _ = harden_fit_params("oo", {"t_sample_pct": 25})
        self.assertEqual(keep["t_sample_pct"], 25)
        self.assertIn("--t-sample-frac 0.25", build_holdout_cli("oo", keep))
        full, warns = harden_fit_params("oo", {"t_sample_pct": 100, "d_model": 512})
        self.assertEqual(full["t_sample_pct"], 100)
        self.assertIn("--t-sample-frac 1", build_holdout_cli("oo", full))
        self.assertTrue(any("OOM" in w for w in warns))
        tiny, tw = harden_fit_params("oo", {"t_sample_pct": 0.01})
        self.assertEqual(tiny["t_sample_pct"], 0.1)
        self.assertTrue(any("t_sample_pct:" in w for w in tw))
        # 旧绝对行上限丢弃；臂固定 L,T
        legacy, _lw = harden_fit_params("oo", {"max_train_rows": 40000, "arms": "T"})
        self.assertNotIn("max_train_rows", legacy)
        self.assertEqual(legacy["arms"], "L,T")
        self.assertEqual(legacy["t_sample_pct"], 10.0)
        # Holdout args.t_sample_frac → desk t_sample_pct（避免 ingest 掉回默认 10）
        via_frac = _normalize_fit_params("oo", {"t_sample_frac": 1.0, "watching_limit": 800})
        self.assertEqual(via_frac["t_sample_pct"], 100.0)
        self.assertNotIn("t_sample_frac", via_frac)
        self.assertIn("--t-sample-frac 1", build_holdout_cli("oo", via_frac))

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
                        "    transformer fit start loss=smooth_l1 batch=32 "
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

    def test_interrupt_hint_from_mid_train_log(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td):
                from core.research.yhat_tf_desk import _fit_exit_error_from_log

                log_path = os.path.join(td, "tc_fit.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(
                        "    transformer fit start loss=smooth_l1 batch=32\n"
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
                job = None
                for _ in range(3):
                    job = refresh_fit_job("oo")
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

                save_fit_draft(
                    "oo",
                    {"d_model": 32, "batch_size": 16, "pool_n": 200, "loss": "mse"},
                    note="t",
                )
                draft_path = os.path.join(td, "oo_draft.json")
                with open(draft_path, encoding="utf-8") as f:
                    draft_blob = json.load(f)
                self.assertEqual(draft_blob["params"]["watching_limit"], 200)
                self.assertEqual(draft_blob["params"]["loss"], "smooth_l1")

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
                    st0["scheme"]["per_head"]["oo"]["gate_label"], "Arm·L"
                )
                self.assertEqual(
                    st0["scheme"]["per_head"]["oo_rank"]["gate_label"], "Gate lift"
                )

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

                # batch 地板 16；factor_pack 跟 T 列宽；模型超参进 CLI
                p8, warns = harden_fit_params("oo", {"batch_size": 8, "full_aoo": False})
                self.assertEqual(p8["batch_size"], 16)
                self.assertTrue(any("batch" in w for w in warns))
                self.assertEqual(p8["factor_pack"], "aoo_core")
                # 显式配置保留；无 mlp_hidden 时默认对齐 d_model
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
                self.assertEqual(p_full["mlp_hidden"], 128)
                self.assertEqual(p_full["nhead"], 8)
                self.assertEqual(p_full["dropout"], 0.2)
                self.assertEqual(p_full["patience"], 6)
                p_def, _ = harden_fit_params(
                    "oo", {"d_model": 128, "full_aoo": True}
                )
                self.assertEqual(p_def["nhead"], 4)
                self.assertEqual(p_def["mlp_hidden"], 128)
                self.assertEqual(p_def["dropout"], 0.1)
                self.assertEqual(p_def["lr"], 1e-3)
                self.assertEqual(p_def["patience"], 6)
                from core.research.yhat_tf_desk import build_holdout_cli

                cli = build_holdout_cli("oo", p_full)
                self.assertIn("--mlp-hidden 128", cli)
                self.assertIn("--dropout 0.2", cli)
                self.assertIn("--patience 6", cli)
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

                # 旧 last.fit_params 缺 patience/dropout/lr 不应误拦晋级
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
                        "loss": "smooth_l1",
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
                        "loss": "smooth_l1",
                        # 故意缺 dropout / lr / patience
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
        self.assertNotIn('id="yhat-tf-toggle-view"', html)
        self.assertNotIn("全部展开", html)
        self.assertIn("yhat-tf-protocol", html)
        self.assertNotIn('data-param="protocol_preset"', html)
        self.assertIn("data-fit-contrast", html)
        self.assertNotIn("快速消融", html)
        self.assertNotIn("默认对照", html)
        self.assertNotIn("贴 live", html)
        self.assertIn('data-param-radio="seq_feat_set"', html)
        self.assertIn('data-param-radio="full_aoo"', html)
        self.assertIn('data-param-radio="full_atc"', html)
        self.assertIn("core · 55", html)
        self.assertIn("full · 230", html)
        self.assertNotIn(">atc_core<", html)
        self.assertIn("yhat-tf-param-group", html)
        self.assertNotIn("yhat-tf-arm-picks", html)
        self.assertIn("yhat-tf-arms-title", html)
        self.assertIn("实验臂与 Gate", html)
        self.assertNotIn("实验臂与 Gate 说明", html)
        self.assertNotIn("yhat-tf-arms-fold", html)
        self.assertIn('data-arm-console="L"', html)
        self.assertIn('data-arm-console="T"', html)
        self.assertIn("双支路晚融合", html)
        self.assertEqual(html.count("双支路晚融合"), 7)
        self.assertNotIn('data-arm="0"', html)
        self.assertNotIn('data-arm="A"', html)
        self.assertIn('data-param="loss"', html)
        self.assertIn("<option value=\"listwise\"", html)
        self.assertIn("<option value=\"smooth_l1\"", html)
        self.assertNotIn('data-param="arms" type="text"', html)
        self.assertNotIn('data-param="loss" type="text"', html)
        # 四头字段对齐：模型 d_model/layers/loss · 训练 batch/epochs/T采样%；仅 τc* 多 tau_end
        # nhead/mlp/dropout/lr/patience 不进页面（CLI/草稿可配）
        self.assertIn("T采样%", html)
        self.assertIn('data-param="t_sample_pct"', html)
        self.assertNotIn('data-param="max_train_rows"', html)
        self.assertNotIn('data-arm="', html)  # 臂勾选已下线
        for hidden in ("nhead", "mlp_hidden", "dropout", "lr", "patience"):
            self.assertNotIn(f'data-param="{hidden}"', html)
        self.assertIn('data-gate-item="hard_gate"', html)
        self.assertIn("硬门过闸", html)

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
                "pool_n",
                "lookback",
                "holdout_trading_days",
                "d_model",
                "nlayers",
                "loss",
                "batch_size",
                "epochs",
                "t_sample_pct",
            ):
                self.assertIn(f'data-param="{key}"', panel, msg=f"{head}.{key}")
            self.assertNotIn("yhat-tf-arm-picks", panel, msg=f"{head}.no_arm_picks")
            if head in ("tc", "tc_rank"):
                self.assertIn('data-param="tau_end"', panel, msg=f"{head}.tau_end")
            else:
                self.assertNotIn('data-param="tau_end"', panel, msg=f"{head}.no_tau")
        self.assertIn("yhat-tf-result-grid", html)
        self.assertIn("yhat-tf-tech", html)
        self.assertIn("OOS 指标 · 待回传", html)
        self.assertIn("生产禁用", html)
        self.assertEqual(html.count('data-act="fit"'), 4)
        self.assertIn(">拟合</button>", html)
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
        self.assertIn("勿回退 research/draft", yhat_js)
        # 未在跑时优先 draft，避免旧 job 盖回「刚存的草稿」
        self.assertIn("st.draft?.params || lastParams || st.job?.params", yhat_js)
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


if __name__ == "__main__":
    unittest.main()
