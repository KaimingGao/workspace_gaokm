"""独立 L1/L2 物化：catalog / flags / orchestrate 护栏。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

import numpy as np


class MaterializeTcBarsTests(unittest.TestCase):
    def test_tc_rank_reuses_loaded_bars(self) -> None:
        from core.research.materialize import l1_jobs

        loads = []
        heads = []
        lengths = []

        def _load(codes, **_kwargs):
            loads.append(list(codes))
            bars = [{"date": "2024-01-02", "open": 1, "close": 1}]
            return (
                {"000001": list(bars), "000002": list(bars)},
                [],
                [],
            )

        def _collect(packs, _names, **kwargs):
            heads.append(kwargs.get("cache_head"))
            lengths.append(len(packs[0]["bars"]))
            packs[0]["bars"].append({"date": "mutated"})
            stats = kwargs.get("stats_out")
            if isinstance(stats, dict):
                stats["n_rows"] = 2
                stats["n_stocks"] = 2
                stats["n_cols"] = 1
                stats["hits"] = 0
            return None, [], [0.1, 0.2], [], [], 2

        with mock.patch.object(l1_jobs, "_stock_bar_items", _load), mock.patch(
            "core.research.panel_matrix.collect_tau_compact", _collect
        ):
            row = l1_jobs._materialize_tc(
                ["000001", "000002"], lookback=10, progress_cb=None
            )
        self.assertEqual(len(loads), 1)
        self.assertEqual(heads, ["tc", "tc_rank"])
        self.assertEqual(lengths, [1, 1])
        self.assertTrue(row["tc_rank"]["ok"])
        self.assertTrue(row["ok"])

    def test_tc_short_bars_load_once(self) -> None:
        from core.research.materialize import l1_jobs

        loads = []

        def _load(codes, **_kwargs):
            loads.append(list(codes))
            return ({"000001": [{"date": "2024-01-02"}]}, [], [])

        with mock.patch.object(l1_jobs, "_stock_bar_items", _load), mock.patch(
            "core.research.panel_matrix.collect_tau_compact",
            side_effect=AssertionError("should not collect"),
        ):
            row = l1_jobs._materialize_tc(["000001"], lookback=10, progress_cb=None)
        self.assertEqual(len(loads), 1)
        self.assertFalse(row["ok"])
        self.assertFalse(row["tc_rank"]["ok"])

class MaterializeTrackTests(unittest.TestCase):
    def test_open_track_headline_and_deps(self):
        from core.research.materialize.track import build_materialize_track

        track = build_materialize_track(
            {
                "ok": True,
                "do_l1": True,
                "do_l1_tau_grid": False,
                "do_l2": True,
                "codes_n": 800,
                "elapsed_s": 62.5,
                "l1": {
                    "ok": True,
                    "elapsed_s": 40.0,
                    "n_codes": 800,
                    "families": ["oo", "tc"],
                    "by_family": {
                        "oo": {"ok": True, "elapsed_s": 12.0, "n_stocks": 800},
                        "tc": {"ok": True, "elapsed_s": 18.0, "n_stocks": 800},
                    },
                },
                "l2": {
                    "ok": True,
                    "elapsed_s": 22.0,
                    "dates": ["2024-01-02"] * 60,
                    "clocks": ["open"],
                    "clocks_ok": 1,
                    "by_clock": {
                        "open": {
                            "ok": True,
                            "cubes_hit": 58,
                            "cubes_built": 2,
                            "slim_days": 60,
                        }
                    },
                },
            }
        )
        self.assertEqual(track["id"], "materialize_open")
        self.assertEqual(track["steps_ok"], "2/2")
        self.assertIn("日线物化", track["headline"])
        self.assertIn("hit 58/built 2", track["headline"])
        self.assertEqual(track["stages"][0]["depends_on"], ["l0_daily_bars"])
        self.assertEqual(track["stages"][1]["depends_on"], ["l1_open"])
        self.assertEqual(
            track["stages"][0]["metrics"]["families_ok"],
            ["oo", "tc"],
        )

    def test_tau_track_marks_minute_deps(self):
        from core.research.materialize.track import build_materialize_track

        track = build_materialize_track(
            {
                "ok": False,
                "do_l1": False,
                "do_l1_tau_grid": True,
                "do_l2": True,
                "elapsed_s": 10.0,
                "error": "materialize failed: l1_tau_grid",
                "l1_tau_grid": {
                    "ok": False,
                    "elapsed_s": 9.0,
                    "clocks": ["09:35", "09:40"],
                    "clocks_ok": 0,
                    "error": "本地 5m 不足",
                },
                "l2": None,
            }
        )
        self.assertEqual(track["id"], "materialize_tau")
        self.assertFalse(track["ok"])
        self.assertEqual(track["steps_ok"], "0/2")
        self.assertEqual(
            track["stages"][0]["depends_on"],
            ["l0_daily_bars", "l0_minute_5m"],
        )
        self.assertEqual(track["stages"][0]["status"], "fail")
        self.assertEqual(track["stages"][1]["status"], "fail")
        self.assertIn("FAIL", track["headline"])


class MaterializeCatalogTests(unittest.TestCase):
    def test_catalog_has_p0_families(self):
        from core.research.materialize import catalog_payload

        doc = catalog_payload()
        ids = {f["id"] for f in doc["families"]}
        self.assertIn("oo", ids)
        self.assertIn("tc", ids)
        self.assertEqual(doc["clocks"], ["open"])
        self.assertIn("suggested_tau_clocks", doc)
        self.assertIsInstance(doc["suggested_tau_clocks"], list)
        clocks = list(doc["suggested_tau_clocks"])
        # 分钟物化文案（k/N）：N=网格钟数；默认 09:35…11:00 每 5m → 18，不含 open/09:30
        self.assertEqual(len(clocks), 18, clocks)
        self.assertEqual(clocks[0], "09:35")
        self.assertEqual(clocks[-1], "11:00")
        self.assertNotIn("09:30", clocks)
        self.assertNotIn("open", clocks)
        self.assertIn("flags", doc)
        self.assertIn("require_day_cube", doc["flags"])
        self.assertNotIn("fit_use_day_cube", doc["flags"])
        self.assertIn("tau_grid", doc)
        self.assertEqual(doc["tau_grid"].get("option"), "do_l1_tau_grid")
        self.assertTrue(doc["tau_grid"].get("requires_minute"))
        self.assertEqual(doc["tau_grid"].get("clocks"), clocks)
        self.assertEqual(doc["tau_grid"].get("bars_fp_scope"), "daily")
        self.assertTrue(doc["tau_grid"].get("meta_prices"))
        note = str(doc["tau_grid"].get("note") or "")
        self.assertIn("M/price_tau", note)
        self.assertIn("materialize_required", note)
        self.assertIn("fail-closed", str(doc.get("note") or ""))
        hz = doc.get("horizon_head_l1") or {}
        self.assertIn("hz_t30", hz.get("families") or [])

    def test_normalize_families_default_p0(self):
        from core.research.materialize.catalog import normalize_families

        self.assertEqual(normalize_families(None), ["oo", "tc"])
        self.assertEqual(normalize_families(["oo", "tc", "zz"]), ["oo", "tc"])
        self.assertEqual(normalize_families(["tc", "oo", "nope"]), ["tc", "oo"])

    def test_horizon_l1_names_nonempty(self):
        from core.research.materialize import horizon_l1_feature_names

        names = horizon_l1_feature_names()
        self.assertGreaterEqual(len(names), 4)

class MaterializeFlagsTests(unittest.TestCase):
    def test_require_day_cube_env(self):
        from core.research.materialize import flags as fl

        old = os.environ.get("QUANTLAB_MATERIALIZE_REQUIRE_CUBE")
        try:
            os.environ["QUANTLAB_MATERIALIZE_REQUIRE_CUBE"] = "1"
            self.assertTrue(fl.require_day_cube())
            os.environ["QUANTLAB_MATERIALIZE_REQUIRE_CUBE"] = "0"
            self.assertFalse(fl.require_day_cube())
        finally:
            if old is None:
                os.environ.pop("QUANTLAB_MATERIALIZE_REQUIRE_CUBE", None)
            else:
                os.environ["QUANTLAB_MATERIALIZE_REQUIRE_CUBE"] = old

class MaterializeOrchestrateTests(unittest.TestCase):
    def test_run_materialize_short_codes(self):
        from core.research.materialize import run_materialize

        out = run_materialize(codes=["000001"], do_l1=True, do_l2=False)
        self.assertFalse(out.get("ok"))
        self.assertIn("不足", str(out.get("error") or ""))

    def test_run_materialize_l2_only_mocks_warmup(self):
        from core.research.materialize import run_materialize

        with mock.patch(
            "core.t0.score_policy.warmup_watching_day_cubes",
            return_value={
                "prefetch_codes": 2,
                "cubes_hit": 2,
                "cubes_built": 0,
                "days": 2,
            },
        ), mock.patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["000001", "000002"],
        ):
            out = run_materialize(
                codes=["000001", "000002"],
                do_l1=False,
                do_l2=True,
                dates=["2026-01-02", "2026-01-03"],
                clocks=["open"],
            )
        self.assertTrue(out.get("ok"))
        self.assertEqual(out["l2"]["by_clock"]["open"]["cubes_hit"], 2)

    def test_resolve_l2_lookback_one_knob(self):
        """日线跟 lookback（不再二次 +pad）；分钟用内部短窗。"""
        from core.research.materialize.orchestrate import (
            L2_MINUTE_LOOKBACK,
            request_has_minute_clock,
            resolve_l2_lookback,
        )

        # open L2 = 请求 lookback；垫窗只在 L1 拉日线时加一次
        self.assertEqual(resolve_l2_lookback(1060, minute=False), 1060)
        self.assertEqual(resolve_l2_lookback(0, minute=False), 1060)
        self.assertEqual(resolve_l2_lookback(1060, minute=True), L2_MINUTE_LOOKBACK)
        self.assertFalse(request_has_minute_clock(["open"]))
        self.assertTrue(request_has_minute_clock(["09:35"]))
        self.assertTrue(request_has_minute_clock(["open"], do_l1_tau_grid=True))

    def test_open_l2_shares_lookback(self):
        """日线 open 立方跟 L1 lookback。"""
        from core.research.materialize import run_materialize

        with mock.patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["000001", "000002"],
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l2",
            return_value={"ok": True, "by_clock": {"open": {"ok": True}}},
        ) as l2_fn:
            out = run_materialize(
                codes=["000001", "000002"],
                do_l1=False,
                do_l2=True,
                clocks=["open"],
                lookback=1000,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(l2_fn.call_args.kwargs.get("lookback_days"), 1000)

        with mock.patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["000001", "000002"],
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l2",
            return_value={"ok": True, "by_clock": {"09:35": {"ok": True}}},
        ) as l2_fn:
            from core.research.materialize.orchestrate import L2_MINUTE_LOOKBACK

            out = run_materialize(
                codes=["000001", "000002"],
                do_l1=False,
                do_l2=True,
                clocks=["09:35"],
                lookback=1000,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(l2_fn.call_args.kwargs.get("lookback_days"), L2_MINUTE_LOOKBACK)

    def test_implicit_oo_rank_failure_keeps_p0_ok(self):
        """未显式请求 oo_rank 时，rank 失败不把 oo/tc 整次打失败。"""
        from core.research.lake_bridge import FAMILY_OO, FAMILY_OO_RANK, FAMILY_TC
        from core.research.materialize.l1_jobs import _materialize_l1_body

        def _ok_fam(*_a, **_k):
            return {"ok": True, "n": 2}

        with mock.patch(
            "core.research.materialize.l1_jobs._materialize_oo", side_effect=_ok_fam
        ), mock.patch(
            "core.research.materialize.l1_jobs._materialize_tc", side_effect=_ok_fam
        ), mock.patch(
            "core.research.materialize.l1_jobs._materialize_oo_rank",
            return_value={"ok": False, "error": "rank boom"},
        ):
            out = _materialize_l1_body(
                [FAMILY_OO, FAMILY_TC, "zz"],
                ["000001", "000002"],
                lookback=10,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("oo_rank_optional_failed"))
        self.assertTrue((out["by_family"].get(FAMILY_OO) or {}).get("ok"))
        self.assertFalse((out["by_family"].get(FAMILY_OO_RANK) or {}).get("ok"))

    def test_implicit_oo_rank_cancel_keeps_p0_ok(self):
        """可选 oo_rank 进行中取消：P0 已齐则 L1 仍成功。"""
        from core.research.lake_bridge import FAMILY_OO, FAMILY_TC
        from core.research.materialize.l1_jobs import _materialize_l1_body

        def _ok_fam(*_a, **_k):
            return {"ok": True, "n": 2}

        calls = {"n": 0}

        def _cancel() -> bool:
            # 顺序 oo→tc→oo_rank：第 3 次检查进入可选 rank
            calls["n"] += 1
            return calls["n"] >= 3

        with mock.patch(
            "core.research.materialize.l1_jobs._materialize_oo", side_effect=_ok_fam
        ), mock.patch(
            "core.research.materialize.l1_jobs._materialize_tc", side_effect=_ok_fam
        ), mock.patch(
            "core.research.materialize.l1_jobs._materialize_oo_rank",
            side_effect=AssertionError("oo_rank should not run after cancel"),
        ):
            out = _materialize_l1_body(
                [FAMILY_OO, FAMILY_TC, "zz"],
                ["000001", "000002"],
                lookback=10,
                cancel_cb=_cancel,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertIn("optional oo_rank", str(out.get("note") or ""))

    def test_orchestrate_forwards_nested_progress(self):
        """子阶段的 done/total 要送到 Job；阶段边界仍是 1/2。"""
        from core.research.materialize import run_materialize

        events = []

        def _pcb(c, t, m=""):
            events.append((int(c), int(t), str(m or "")))

        def _fake_l1(*_a, progress_cb=None, **_k):
            if progress_cb:
                progress_cb(2, 3, "L1 oo：assemble…")
                progress_cb(0, 1, "L1 tc…")
            return {"ok": True, "elapsed_s": 0.01}

        def _fake_l2(*_a, progress_cb=None, **_k):
            if progress_cb:
                progress_cb(50, 800, "L2：日线 50/800")
            return {"ok": True, "by_clock": {"open": {"cubes_hit": 1}}}

        with mock.patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["000001", "000002"],
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l1",
            side_effect=_fake_l1,
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l2",
            side_effect=_fake_l2,
        ):
            out = run_materialize(
                codes=["000001", "000002"],
                do_l1=True,
                do_l2=True,
                clocks=["open"],
                progress_cb=_pcb,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertIn((2, 3, "L1 oo：assemble…"), events)
        self.assertIn((0, 1, "L1 tc…"), events)
        self.assertIn((1, 2, "L1 完成"), events)
        self.assertIn((50, 800, "L2：日线 50/800"), events)
        self.assertTrue(any(e[0] == 2 and e[1] == 2 and "物化完成" in e[2] for e in events))
        track = out.get("track") or {}
        self.assertEqual(track.get("id"), "materialize_open")
        self.assertTrue(track.get("ok"))
        self.assertEqual(track.get("steps_ok"), "2/2")
        self.assertIn("headline", track)
        stage_ids = [s.get("id") for s in (track.get("stages") or [])]
        self.assertEqual(stage_ids, ["l1_open", "l2_open"])
        self.assertEqual(
            (track["stages"][0].get("depends_on") or []),
            ["l0_daily_bars"],
        )

    def test_fold_keeps_macro_total_and_moves_pct(self):
        from core.research.materialize.progress import fold_materialize_progress

        # L1 内部 2/3 不得把 Job total 改成 3；百分比落在第一步里
        c, t, pct = fold_materialize_progress(
            planned=2, prev_current=0, prev_pct=0, cur=2, tot=3
        )
        self.assertEqual((c, t), (0, 2))
        self.assertAlmostEqual(pct, round(100.0 * (2 / 3) / 2, 1))
        # 票级心跳 (0, 1) 不能把条拉回 0
        c2, t2, pct2 = fold_materialize_progress(
            planned=2, prev_current=c, prev_pct=pct, cur=0, tot=1
        )
        self.assertEqual((c2, t2), (0, 2))
        self.assertGreaterEqual(pct2, pct)
        # L1 结束走到 1/2 后，L2 的 50/800 继续往前
        c3, t3, pct3 = fold_materialize_progress(
            planned=2, prev_current=0, prev_pct=pct2, cur=1, tot=2
        )
        self.assertEqual((c3, t3), (1, 2))
        c4, t4, pct4 = fold_materialize_progress(
            planned=2, prev_current=c3, prev_pct=pct3, cur=50, tot=800
        )
        self.assertEqual((c4, t4), (1, 2))
        self.assertGreater(pct4, 50.0)
        self.assertLess(pct4, 100.0)
        # 迟到的 0/2 不能把已经走到 L2 的 current 打回去
        c5, t5, pct5 = fold_materialize_progress(
            planned=2, prev_current=c4, prev_pct=pct4, cur=0, tot=2
        )
        self.assertEqual((c5, t5), (1, 2))
        self.assertGreaterEqual(pct5, pct4)
        # 坏数字、NaN 只当 0，不抛
        c6, t6, pct6 = fold_materialize_progress(
            planned="x",
            prev_current="nope",
            prev_pct=float("nan"),
            cur=object(),
            tot=None,
        )
        self.assertEqual((c6, t6), (0, 1))
        self.assertEqual(pct6, 0.0)
        # 族序号 3/4 钉在格尾时，消息里的票数要把这一格填开，并且继续往前
        c7, t7, pct7 = fold_materialize_progress(
            planned=2,
            prev_current=0,
            prev_pct=0,
            cur=3,
            tot=4,
            message="ŷ_τc 面板 440/800",
        )
        self.assertEqual((c7, t7), (0, 2))
        self.assertAlmostEqual(pct7, 31.9)
        c8, t8, pct8 = fold_materialize_progress(
            planned=2,
            prev_current=c7,
            prev_pct=pct7,
            cur=3,
            tot=4,
            message="ŷ_τc 面板 480/800",
        )
        self.assertEqual((c8, t8), (0, 2))
        self.assertGreater(pct8, pct7)
        self.assertLess(pct8, 37.5)

    def test_scaled_progress_ignores_bad_ticks(self):
        from core.research.materialize.progress import STEP_SCALE, emit_scaled

        seen = []
        emit_scaled(seen.append, 0, 4, 1, 2, "nope")
        self.assertEqual(seen, [])
        emit_scaled(lambda c, t, m: seen.append((c, t, m)), 0, 4, "bad", None, "L1")
        self.assertEqual(len(seen), 1)
        cur, tot, msg = seen[0]
        self.assertGreaterEqual(tot, STEP_SCALE)
        self.assertNotEqual(tot, 2)
        self.assertEqual(msg, "L1")
        self.assertGreaterEqual(cur, 0)

        def _boom(*_a):
            raise RuntimeError("ui")

        emit_scaled(_boom, 1, 4, 10, 10, "x")

    def test_run_materialize_tau_grid_wires_l1(self):
        from core.research.materialize import run_materialize

        with mock.patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["000001", "000002"],
        ), mock.patch(
            "core.research.materialize.orchestrate.suggested_tau_clocks",
            return_value=["10:30", "11:00"],
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l1_tau_grid",
            return_value={
                "ok": True,
                "clocks": ["10:30"],
                "clocks_ok": 1,
                "by_clock": {"10:30": {"ok": True}},
                "elapsed_s": 0.1,
            },
        ) as tau_fn, mock.patch(
            "core.research.materialize.orchestrate.materialize_l2",
            return_value={"ok": True, "by_clock": {"10:30": {"cubes_hit": 1}}},
        ) as l2_fn:
            out = run_materialize(
                codes=["000001", "000002"],
                do_l1=False,
                do_l1_tau_grid=True,
                do_l2=True,
                dates=["2026-01-02"],
                clocks=["open"],  # UI 占位；纯 τ 轨扩成 suggested τ（不含 open）
            )
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("do_l1_tau_grid"))
        tau_fn.assert_called_once()
        kwargs = l2_fn.call_args.kwargs
        self.assertTrue(kwargs.get("require_horizon_names"))
        clks = list(kwargs.get("clocks") or [])
        self.assertNotIn("open", clks)
        self.assertIn("10:30", clks)
        self.assertIn("11:00", clks)

class MaterializeRequireCubeWarmupTests(unittest.TestCase):
    def test_warmup_skips_ensure_when_require_cube(self):
        from core.t0 import score_policy as sp

        old = os.environ.get("QUANTLAB_MATERIALIZE_REQUIRE_CUBE")
        try:
            os.environ["QUANTLAB_MATERIALIZE_REQUIRE_CUBE"] = "1"
            with mock.patch(
                "core.research.lake_replay.prefetch_l1_coverage", return_value={"oo": 2}
            ), mock.patch(
                "core.research.day_cube.enabled", return_value=True
            ), mock.patch(
                "core.research.feature_lake.enabled", return_value=True
            ), mock.patch(
                "core.research.day_cube.day_cube_status", return_value="miss"
            ), mock.patch(
                "core.research.batch_score.ensure_day_cube_for_items"
            ) as ensure:
                out = sp.warmup_watching_day_cubes(
                    ["000001", "000002"],
                    ["2026-01-02"],
                    min_lake_frac=0.5,
                )
            ensure.assert_not_called()
            self.assertEqual(out.get("cubes_built"), 0)
        finally:
            if old is None:
                os.environ.pop("QUANTLAB_MATERIALIZE_REQUIRE_CUBE", None)
            else:
                os.environ["QUANTLAB_MATERIALIZE_REQUIRE_CUBE"] = old

class MaterializePackageExportTests(unittest.TestCase):
    def test_public_exports(self):
        from core.research import materialize as m

        for name in (
            "catalog_payload",
            "require_day_cube",
            "run_materialize",
            "build_materialize_track",
            "cs_z_matrix_prefer_cube",
            "cs_z_dicts_prefer_cube",
            "align_fit_rows_to_l2",
            "groups_with_day_cube",
            "apply_day_cube_mu_sigma_matrix",
            "horizon_l1_feature_names",
            "names_cover_horizon",
        ):
            self.assertTrue(callable(getattr(m, name, None)), name)
        self.assertFalse(hasattr(m, "fit_use_day_cube"))

class PreferCubeHelpersTests(unittest.TestCase):
    def test_cs_z_matrix_prefer_cube_always_tries_l2(self):
        """有 Cube 即吃 L2 μ/σ（恒走 L2，无开关）。"""
        from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

        class _Cube:
            names = ["f1"]
            z_mean = np.array([0.0])
            z_std = np.array([1.0])

        x = np.array([[1.0], [3.0]], dtype=np.float64)
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", return_value=_Cube()
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            z = cs_z_matrix_prefer_cube(x, ["f1"], ["2026-01-02", "2026-01-02"])
        self.assertAlmostEqual(float(z[0, 0]), 1.0, places=5)
        self.assertAlmostEqual(float(z[1, 0]), 3.0, places=5)

    def test_align_fit_rows_drops_days_without_cube(self):
        """单票 L1 早到日无池立方时丢掉，不整单 materialize_required。"""
        from core.research.materialize.cube_z import align_fit_rows_to_l2

        class _Cube:
            names = ["f1"]
            z_mean = np.array([0.0])
            z_std = np.array([1.0])

        flags = []

        def _load(day, codes, clock="open", **_kw):
            flags.append(dict(_kw))
            if str(day)[:10] == "2022-08-31":
                return _Cube()
            return None

        x = np.array([[1.0], [2.0], [3.0]], dtype=np.float64)
        y = np.array([0.1, 0.2, 0.3], dtype=np.float64)
        dates = ["2022-08-23", "2022-08-31", "2022-08-31"]
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            x2, y2, d2, mask = align_fit_rows_to_l2(x, y, dates, clock="open")
        self.assertEqual(list(mask), [False, True, True])
        self.assertEqual(x2.shape[0], 2)
        self.assertEqual(list(d2), ["2022-08-31", "2022-08-31"])
        np.testing.assert_allclose(y2, [0.2, 0.3])
        self.assertTrue(flags)
        self.assertTrue(all(kw.get("names_only") is True for kw in flags))

    def test_filter_rows_drops_only_missing_day_clock(self):
        """同一天只丢掉没有立方的那一口钟，其他钟留下。全日都没有才报物化。"""
        from core.research.materialize.consumer import MaterializeRequired
        from core.research.materialize.cube_z import filter_rows_to_day_cube

        class _Cube:
            pass

        def _load(day, codes, clock="open", **_kw):
            key = (str(day)[:10], str(clock))
            if key in {("2026-04-01", "09:40"), ("2026-08-03", "09:35")}:
                return _Cube()
            return None

        groups = [
            "2026-04-01|09:35",
            "2026-04-01|09:40",
            "2026-08-03|09:35",
        ]
        ys = [1.0, 2.0, 3.0]
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            kept_g, kept_cols, miss = filter_rows_to_day_cube(
                groups, [ys], clock="09:35", require_any=True
            )
            with self.assertRaises(MaterializeRequired):
                filter_rows_to_day_cube(
                    ["2026-04-01|09:35"],
                    [[9.0]],
                    clock="09:35",
                    require_any=True,
                )
        self.assertEqual(kept_g, ["2026-04-01|09:40", "2026-08-03|09:35"])
        self.assertEqual(kept_cols[0], [2.0, 3.0])
        self.assertEqual(miss, ["2026-04-01|09:35"])

class CubeZPerClockTests(unittest.TestCase):
    def test_date_tau_loads_matching_clock(self):
        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["f1"]
            z_mean = np.array([1.0])
            z_std = np.array([2.0])

        loads = []

        def _load(day, codes, clock="open", **_kw):
            loads.append((str(day)[:10], str(clock), dict(_kw)))
            return _Cube()

        x = np.array([[3.0], [5.0], [7.0], [9.0]], dtype=np.float64)
        groups = [
            "2026-01-02|09:35",
            "2026-01-02|09:35",
            "2026-01-02|10:30",
            "2026-01-02|10:30",
        ]
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            z, meta = apply_day_cube_mu_sigma_matrix(
                x, ["f1"], groups, clock="open", fallback_open=False
            )
        self.assertTrue(meta.get("ok"), meta)
        self.assertIsNotNone(z)
        # (3-1)/2 = 1； (5-1)/2 = 2
        self.assertAlmostEqual(float(z[0, 0]), 1.0, places=5)
        self.assertAlmostEqual(float(z[1, 0]), 2.0, places=5)
        clocks = {c for _, c, _kw in loads}
        self.assertIn("09:35", clocks)
        self.assertIn("10:30", clocks)
        self.assertTrue(loads)
        self.assertTrue(all(kw.get("with_matrix") is False for *_, kw in loads))
        self.assertTrue(all(kw.get("remember") is False for *_, kw in loads))
        np.testing.assert_allclose(x, [[3.0], [5.0], [7.0], [9.0]])
        self.assertIsNot(z, x)

    def test_inplace_cs_z_reuses_float64_buffer(self):
        """截面 z 可以写回调用方的 float64，缺测填 0 不再另分配整块。"""
        from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

        class _Cube:
            names = ["f1"]
            z_mean = np.array([1.0])
            z_std = np.array([2.0])

        x = np.array([[3.0], [5.0], [np.nan]], dtype=np.float64)
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", return_value=_Cube()
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            z = cs_z_matrix_prefer_cube(
                x,
                ["f1"],
                ["2026-01-02", "2026-01-02", "2026-01-02"],
                inplace=True,
            )
        self.assertIs(z, x)
        self.assertAlmostEqual(float(z[0, 0]), 1.0, places=5)
        self.assertAlmostEqual(float(z[1, 0]), 2.0, places=5)
        self.assertEqual(float(z[2, 0]), 0.0)

    def test_align_keeps_float64_object_when_no_row_dropped(self):
        from core.research.materialize.cube_z import align_fit_rows_to_l2

        x = np.arange(6, dtype=np.float64).reshape(3, 2)
        y = np.arange(3, dtype=np.float64)
        dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
        with mock.patch(
            "core.research.materialize.cube_z.groups_with_day_cube",
            return_value=(np.ones(3, dtype=bool), []),
        ):
            x2, y2, d2, mask = align_fit_rows_to_l2(x, y, dates, clock="open")
        self.assertIs(x2, x)
        self.assertIs(y2, y)
        self.assertEqual(d2, dates)
        self.assertEqual(mask.tolist(), [True, True, True])

    def test_align_keeps_float32_object_when_requested(self):
        from core.research.materialize.cube_z import align_fit_rows_to_l2

        x = np.arange(6, dtype=np.float32).reshape(3, 2)
        y = np.arange(3, dtype=np.float64)
        dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
        with mock.patch(
            "core.research.materialize.cube_z.groups_with_day_cube",
            return_value=(np.ones(3, dtype=bool), []),
        ):
            x2, y2, _d2, _mask = align_fit_rows_to_l2(
                x, y, dates, clock="open", x_dtype=np.float32
            )
        self.assertIs(x2, x)
        self.assertIs(y2, y)
        self.assertEqual(x2.dtype, np.float32)

    def test_inplace_cs_z_reuses_float32_buffer(self):
        from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

        class _Cube:
            names = ["f1"]
            z_mean = np.array([1.0])
            z_std = np.array([2.0])

        x = np.array([[3.0], [5.0], [np.nan]], dtype=np.float32)
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", return_value=_Cube()
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            z = cs_z_matrix_prefer_cube(
                x,
                ["f1"],
                ["2026-01-02", "2026-01-02", "2026-01-02"],
                inplace=True,
            )
        self.assertIs(z, x)
        self.assertEqual(z.dtype, np.float32)
        self.assertAlmostEqual(float(z[0, 0]), 1.0, places=5)
        self.assertAlmostEqual(float(z[1, 0]), 2.0, places=5)
        self.assertEqual(float(z[2, 0]), 0.0)

    def test_clock_miss_falls_back_to_open(self):
        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["f1"]
            z_mean = np.array([0.0])
            z_std = np.array([1.0])

        def _load(day, codes, clock="open", **_kw):
            if str(clock) == "open":
                return _Cube()
            return None

        x = np.array([[1.0], [3.0]], dtype=np.float64)
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            z, meta = apply_day_cube_mu_sigma_matrix(
                x,
                ["f1"],
                ["2026-01-02|10:00", "2026-01-02|10:00"],
                clock="open",
                fallback_open=True,
            )
        self.assertTrue(meta.get("ok"), meta)
        self.assertEqual(meta.get("open_fallback"), 1)
        self.assertAlmostEqual(float(z[0, 0]), 1.0, places=5)
        self.assertAlmostEqual(float(z[1, 0]), 3.0, places=5)

    def test_date_tau_default_no_silent_open(self):
        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["f1"]
            z_mean = np.array([0.0])
            z_std = np.array([1.0])

        def _load(day, codes, clock="open", **_kw):
            if str(clock) == "open":
                return _Cube()
            return None

        x = np.array([[1.0], [3.0]], dtype=np.float64)
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            z, meta = apply_day_cube_mu_sigma_matrix(
                x,
                ["f1"],
                ["2026-01-02|10:00", "2026-01-02|10:00"],
                clock="open",
            )
        self.assertIsNone(z)
        self.assertFalse(meta.get("ok"))
        self.assertFalse(meta.get("fallback_open"))
        self.assertIn("cube miss", str(meta.get("error") or ""))

    def test_partial_cube_miss_fails_without_matrix_fallback(self):
        """部分日缺 Cube：整单失败，不再桶内现算。"""
        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["f1"]
            z_mean = np.array([0.0])
            z_std = np.array([1.0])

        def _load(day, codes, clock="open", **_kw):
            if str(day)[:10] == "2026-08-04" and str(clock) == "09:35":
                return _Cube()
            return None

        x = np.array([[1.0], [3.0], [10.0], [30.0]], dtype=np.float64)
        groups = [
            "2026-08-04|09:35",
            "2026-08-04|09:35",
            "2026-03-27|09:35",
            "2026-03-27|09:35",
        ]
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            z, meta = apply_day_cube_mu_sigma_matrix(
                x, ["f1"], groups, clock="open", fallback_open=False
            )
        self.assertIsNone(z)
        self.assertFalse(meta.get("ok"), meta)
        self.assertEqual(meta.get("hit_days"), 1)
        self.assertEqual(meta.get("miss_days"), 1)
        self.assertIn("cube miss", str(meta.get("error") or ""))

    def test_missing_model_column_zeros_not_batch_cs(self):
        """Cube 缺列：填 0，不进批现算；有 Cube μ/σ 的列照常。"""
        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["f1", "raw_ath_x"]
            z_mean = np.array([0.0, 1.0])
            z_std = np.array([1.0, 1.0])

        x = np.array([[1.0, 2.0, 3.0], [3.0, 4.0, 5.0]], dtype=np.float64)
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", return_value=_Cube()
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            z, meta = apply_day_cube_mu_sigma_matrix(
                x, ["f1", "f2", "raw_ath_x"], ["2026-01-02", "2026-01-02"]
            )
        self.assertTrue(meta.get("ok"), meta)
        self.assertIsNotNone(z)
        self.assertIn("f2", meta.get("missing_cols") or [])
        # f1 用 Cube：(1-0)/1=1；(3-0)/1=3
        self.assertAlmostEqual(float(z[0, 0]), 1.0, places=5)
        self.assertAlmostEqual(float(z[1, 0]), 3.0, places=5)
        # f2 缺列填 0
        self.assertAlmostEqual(float(z[0, 1]), 0.0, places=5)
        self.assertAlmostEqual(float(z[1, 1]), 0.0, places=5)
        # raw_ath_x 在 Cube 内：(3-1)/1=2；(5-1)/1=4
        self.assertAlmostEqual(float(z[0, 2]), 2.0, places=5)
        self.assertAlmostEqual(float(z[1, 2]), 4.0, places=5)

    def test_head_lag_missing_from_cube_uses_bucket_z(self):
        """本头滞后不在立方里：有截面差就现算 z；其它缺列仍填 0。"""
        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["f1"]
            z_mean = np.array([0.0])
            z_std = np.array([1.0])

        # 两只股票同一日同一钟。H_LAG1 有差；H_MA5 常数；f2 缺列。
        x = np.array(
            [
                [1.0, 1.0, 2.0, 9.0],
                [3.0, 3.0, 2.0, 8.0],
            ],
            dtype=np.float64,
        )
        with mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.research.day_cube.load_day_cube", return_value=_Cube()
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ):
            z, meta = apply_day_cube_mu_sigma_matrix(
                x,
                ["f1", "raw_ath_H_LAG1", "raw_ath_H_MA5", "f2"],
                ["2026-01-02|10:00", "2026-01-02|10:00"],
                clock="10:00",
                fallback_open=False,
            )
        self.assertTrue(meta.get("ok"), meta)
        self.assertIsNotNone(z)
        self.assertAlmostEqual(float(z[0, 1]), -1.0, places=5)
        self.assertAlmostEqual(float(z[1, 1]), 1.0, places=5)
        self.assertAlmostEqual(float(z[0, 2]), 0.0, places=5)
        self.assertAlmostEqual(float(z[1, 2]), 0.0, places=5)
        self.assertAlmostEqual(float(z[0, 3]), 0.0, places=5)
        self.assertAlmostEqual(float(z[1, 3]), 0.0, places=5)
        self.assertIn("f2", meta.get("missing_cols") or [])
        self.assertNotIn("raw_ath_H_LAG1", meta.get("missing_cols") or [])

class NamesCoverHorizonTests(unittest.TestCase):
    def test_slim_rejected(self):
        from core.research.materialize.horizon_names import names_cover_horizon

        class _Slim:
            names = ["gap", "gap_pct"]

        self.assertFalse(names_cover_horizon(_Slim()))

class HorizonL1StitchTests(unittest.TestCase):
    def test_try_stitch_l1_tau_panel(self):
        from core.research.horizon_l1 import META_PX_KEYS, meta_price_matrix_from_rows
        from core.research.tc_model_compact import _try_stitch_l1_tau_panel

        X = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]], dtype=np.float64)
        y = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float64)
        dates = ["2026-01-02", "2026-01-03", "2026-01-06", "2026-01-07"]
        names = ["f1", "f2"]
        metas = []
        for d in dates:
            metas.append(
                {
                    "date": d,
                    "tau": "10:30",
                    "price_tau": 10.0,
                    "price_tau30": 10.1,
                    "price_tau45": 10.2,
                    "price_tau60": 10.3,
                    "price_tau75": 10.4,
                    "price_tau90": 10.5,
                }
            )
        M, m_names = meta_price_matrix_from_rows(metas)

        def _pack(family, code, fingerprint=None):
            return {
                "X": X,
                "y": y,
                "dates": dates,
                "names": names,
                "M": M,
                "_meta": {"m_names": m_names},
            }

        bars = [{"date": d, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for d in dates]
        bars += [{"date": "2026-01-08", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}] * 10
        with mock.patch(
            "core.research.feature_lake.enabled", return_value=True
        ), mock.patch(
            "core.research.feature_lake.load_stock_panel", side_effect=_pack
        ), mock.patch(
            "core.research.materialize.horizon_names.horizon_l1_feature_names",
            return_value=names,
        ), mock.patch(
            "core.research.panel_matrix._tc_matrix_seed_names",
            return_value=names,
        ):
            panel = _try_stitch_l1_tau_panel(
                "000001",
                bars,
                [],
                ["10:30", "11:00"],
                min_history=12,
                include_alpha_tc=True,
                include_alpha_th=True,
            )
        self.assertIsNotNone(panel)
        self.assertTrue(panel.get("l1_stitched"))
        self.assertEqual(len(panel["xs"]), 8)  # 4 days × 2 clocks
        self.assertEqual(panel["metas"][0].get("tau"), "10:30")
        self.assertAlmostEqual(float(panel["metas"][0]["price_tau"]), 10.0)
        self.assertNotIn("minute_bars", panel or {})
        # 无 M → miss（逼重物化或 5m）
        def _pack_no_m(family, code, fingerprint=None):
            return {"X": X, "y": y, "dates": dates, "names": names, "_meta": {}}

        with mock.patch(
            "core.research.feature_lake.enabled", return_value=True
        ), mock.patch(
            "core.research.feature_lake.load_stock_panel", side_effect=_pack_no_m
        ), mock.patch(
            "core.research.materialize.horizon_names.horizon_l1_feature_names",
            return_value=names,
        ), mock.patch(
            "core.research.panel_matrix._tc_matrix_seed_names",
            return_value=names,
        ):
            miss = _try_stitch_l1_tau_panel(
                "000001",
                bars,
                [],
                ["10:30"],
                min_history=12,
                include_alpha_tc=True,
                include_alpha_th=True,
            )
        self.assertIsNone(miss)
        self.assertEqual(len(META_PX_KEYS), 6)

    def test_stitch_slices_shorter_fit_lookback(self):
        """物化 长窗、拟合短窗：同末日真后缀切重叠日，并带上 price_tau*。"""
        import tempfile
        from unittest.mock import patch

        from core.research.feature_lake import write_stock_panel
        from core.research.horizon_l1 import (
            horizon_tau_clock_fingerprint,
            try_stitch_horizon_l1_panel,
        )
        from core.research.incremental_panel import stem_extra_meta
        from core.research.lake_bridge import family_for_tc
        from tests.conftest import _panel_bars as _bars

        full = _bars(40, seed=3)
        short = full[-16:]
        clock = "10:30"
        names = ["f1", "f2"]
        dates = [str(b["date"])[:10] for b in full]
        meta = stem_extra_meta(full)
        fp = horizon_tau_clock_fingerprint(
            full,
            clock,
            min_history=2,
            include_alpha_tc=True,
            include_alpha_th=True,
            bars_fp=meta["bars_fp"],
        )
        X = np.asarray([[float(i), float(i) + 0.5] for i in range(len(dates))])
        y = np.asarray([0.1] * len(dates))
        M = np.column_stack(
            [
                np.full(len(dates), 1030.0),
                np.full(len(dates), 10.0),
                np.full(len(dates), 10.3),
                np.full(len(dates), np.nan),
                np.full(len(dates), np.nan),
                np.full(len(dates), np.nan),
                np.full(len(dates), np.nan),
            ]
        )
        m_names = [
            "tau_hm",
            "price_tau",
            "price_tau30",
            "price_tau45",
            "price_tau60",
            "price_tau75",
            "price_tau90",
        ]
        meta["m_names"] = list(m_names)
        prev = {
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_FEATURE_LAKE": os.environ.get("QUANTLAB_FEATURE_LAKE"),
        }
        tmp = tempfile.TemporaryDirectory()
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = tmp.name
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        try:
            self.assertTrue(
                write_stock_panel(
                    family_for_tc(tau_hm=clock),
                    "300750",
                    dates=dates,
                    X=X,
                    names=names,
                    fingerprint=fp,
                    y=y,
                    extra_meta=meta,
                    M=M,
                    m_names=m_names,
                )
            )
            with patch(
                "core.research.incremental_panel._offline_daily_bars",
                return_value=list(full),
            ), patch(
                "core.research.materialize.horizon_names.horizon_l1_feature_names",
                return_value=names,
            ), patch(
                "core.research.panel_matrix._tc_matrix_seed_names",
                return_value=names,
            ):
                panel = try_stitch_horizon_l1_panel(
                    "300750",
                    short,
                    [clock],
                    min_history=2,
                    include_alpha_tc=True,
                    include_alpha_th=True,
                )
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            tmp.cleanup()
        self.assertIsNotNone(panel)
        assert panel is not None
        self.assertTrue(panel.get("l1_stitched"))
        self.assertEqual(len(panel["xs"]), len(short))
        self.assertEqual(panel["dates"][0], str(short[0]["date"])[:10])
        self.assertAlmostEqual(float(panel["metas"][0]["price_tau"]), 10.0)
        self.assertAlmostEqual(float(panel["metas"][0]["price_tau30"]), 10.3)
        self.assertNotIn("minute_bars", panel)

    def test_stitch_keeps_other_clock_when_one_fails(self):
        """某一钟读盘失败时留下其余钟，命中过半仍算 L1。"""
        from core.research.horizon_l1 import try_stitch_horizon_l1_panel
        from core.research.lake_bridge import family_for_tc

        dates = ["2026-01-02", "2026-01-03", "2026-01-06", "2026-01-07"]
        names = ["f1", "f2"]
        X = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]])
        y = np.array([0.1, 0.2, 0.3, 0.4])
        M = np.column_stack(
            [
                np.full(4, 1030.0),
                np.full(4, 10.0),
                np.full(4, 10.2),
                np.full(4, np.nan),
                np.full(4, np.nan),
                np.full(4, np.nan),
                np.full(4, np.nan),
            ]
        )
        m_names = [
            "tau_hm",
            "price_tau",
            "price_tau30",
            "price_tau45",
            "price_tau60",
            "price_tau75",
            "price_tau90",
        ]
        bad = family_for_tc(tau_hm="11:00")

        def _pack(family, code, fingerprint=None):
            if str(family) == bad:
                raise RuntimeError("bad npz")
            return {
                "X": X,
                "y": y,
                "dates": dates,
                "names": names,
                "M": M,
                "_meta": {"m_names": m_names},
            }

        bars = [
            {"date": d, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for d in dates
        ]
        bars += [
            {"date": "2026-01-08", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        ] * 12
        with mock.patch(
            "core.research.feature_lake.enabled", return_value=True
        ), mock.patch(
            "core.research.feature_lake.load_stock_panel", side_effect=_pack
        ), mock.patch(
            "core.research.materialize.horizon_names.horizon_l1_feature_names",
            return_value=names,
        ), mock.patch(
            "core.research.panel_matrix._tc_matrix_seed_names",
            return_value=names,
        ):
            panel = try_stitch_horizon_l1_panel(
                "000001",
                bars,
                ["10:30", "11:00"],
                min_history=2,
                include_alpha_tc=True,
                include_alpha_th=True,
            )
        self.assertIsNotNone(panel)
        assert panel is not None
        self.assertEqual(panel.get("l1_clocks_hit"), 1)
        self.assertEqual(panel.get("l1_clocks_req"), 2)
        self.assertEqual(len(panel["xs"]), 4)
        self.assertTrue(all(m.get("tau") == "10:30" for m in panel["metas"]))

    def test_stitch_realigns_column_order(self):
        from core.research.horizon_l1 import try_stitch_horizon_l1_panel

        dates = ["2026-01-02", "2026-01-03", "2026-01-06", "2026-01-07"]
        # 湖里列序是 f2, f1；拟合要 f1, f2。值：f2=行号，f1=行号+10
        X = np.array([[0.0, 10.0], [1.0, 11.0], [2.0, 12.0], [3.0, 13.0]])
        y = np.array([0.1, 0.2, 0.3, 0.4])
        M = np.column_stack(
            [
                np.full(4, 1030.0),
                np.full(4, 9.0),
                np.full(4, 9.5),
                np.full(4, np.nan),
                np.full(4, np.nan),
                np.full(4, np.nan),
                np.full(4, np.nan),
            ]
        )
        m_names = [
            "tau_hm",
            "price_tau",
            "price_tau30",
            "price_tau45",
            "price_tau60",
            "price_tau75",
            "price_tau90",
        ]

        def _pack(family, code, fingerprint=None):
            return {
                "X": X,
                "y": y,
                "dates": dates,
                "names": ["f2", "f1"],
                "M": M,
                "_meta": {"m_names": m_names},
            }

        bars = [
            {"date": d, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for d in dates
        ]
        bars += [
            {"date": "2026-01-08", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        ] * 12
        with mock.patch(
            "core.research.feature_lake.enabled", return_value=True
        ), mock.patch(
            "core.research.feature_lake.load_stock_panel", side_effect=_pack
        ), mock.patch(
            "core.research.materialize.horizon_names.horizon_l1_feature_names",
            return_value=["f1", "f2"],
        ), mock.patch(
            "core.research.panel_matrix._tc_matrix_seed_names",
            return_value=["f1", "f2"],
        ):
            panel = try_stitch_horizon_l1_panel(
                "000001",
                bars,
                ["10:30"],
                min_history=2,
                include_alpha_tc=True,
                include_alpha_th=True,
            )
        self.assertIsNotNone(panel)
        assert panel is not None
        self.assertAlmostEqual(float(panel["xs"][0]["f1"]), 10.0)
        self.assertAlmostEqual(float(panel["xs"][0]["f2"]), 0.0)
        self.assertAlmostEqual(float(panel["xs"][3]["f1"]), 13.0)

    def test_stitch_slice_allows_end_lag(self):
        from core.research.horizon_l1 import try_stitch_horizon_l1_panel

        seen = {}

        def _slice(*_args, **kwargs):
            seen["allow_end_lag"] = kwargs.get("allow_end_lag")
            return None

        names = ["f1"]
        bars = [
            {
                "date": f"2026-01-{i:02d}",
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            }
            for i in range(2, 22)
        ]
        with mock.patch(
            "core.research.feature_lake.enabled", return_value=True
        ), mock.patch(
            "core.research.feature_lake.load_stock_panel", return_value=None
        ), mock.patch(
            "core.research.incremental_panel.try_slice_shorter_lookback",
            side_effect=_slice,
        ), mock.patch(
            "core.research.materialize.horizon_names.horizon_l1_feature_names",
            return_value=names,
        ), mock.patch(
            "core.research.panel_matrix._tc_matrix_seed_names",
            return_value=names,
        ):
            panel = try_stitch_horizon_l1_panel(
                "000001",
                bars,
                ["10:30"],
                min_history=2,
                include_alpha_tc=True,
                include_alpha_th=True,
            )
        self.assertIsNone(panel)
        self.assertTrue(seen.get("allow_end_lag"))

    def test_stitch_matrix_does_not_expand_dicts(self):
        """全池拟合走 float64 块，不把每一列摊成 dict。"""
        from core.research.horizon_compact import fold_horizon_enriched
        from core.research.horizon_l1 import try_stitch_horizon_l1_panel
        from core.research.tau_panel import relabel_tau_panels_as_t30

        dates = ["2026-01-02", "2026-01-03", "2026-01-06", "2026-01-07"]
        names = ["f1", "gap_pct", "ret_last_30m"]
        X = np.array(
            [
                [1.0, 2.0, 0.5],
                [3.0, -1.0, 0.2],
                [5.0, 4.0, -0.1],
                [7.0, 0.0, 0.3],
            ]
        )
        y = np.array([0.1, 0.2, 0.3, 0.4])
        M = np.column_stack(
            [
                np.full(4, 1030.0),
                np.full(4, 10.0),
                np.full(4, 10.4),
                np.full(4, np.nan),
                np.full(4, np.nan),
                np.full(4, np.nan),
                np.full(4, np.nan),
            ]
        )
        m_names = [
            "tau_hm",
            "price_tau",
            "price_tau30",
            "price_tau45",
            "price_tau60",
            "price_tau75",
            "price_tau90",
        ]

        def _pack(family, code, fingerprint=None):
            return {
                "X": X,
                "y": y,
                "dates": dates,
                "names": names,
                "M": M,
                "_meta": {"m_names": m_names},
            }

        bars = [
            {"date": d, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for d in dates
        ]
        bars += [
            {"date": "2026-01-08", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        ] * 12
        with mock.patch(
            "core.research.feature_lake.enabled", return_value=True
        ), mock.patch(
            "core.research.feature_lake.load_stock_panel", side_effect=_pack
        ), mock.patch(
            "core.research.materialize.horizon_names.horizon_l1_feature_names",
            return_value=names,
        ), mock.patch(
            "core.research.panel_matrix._tc_matrix_seed_names",
            return_value=names,
        ):
            panel = try_stitch_horizon_l1_panel(
                "000001",
                bars,
                ["10:30", "11:00"],
                min_history=2,
                include_alpha_tc=True,
                include_alpha_th=True,
                as_matrix=True,
                project_names=["f1", "gap_pct", "ret_last_30m", "t30_lag1"],
            )
        self.assertIsNotNone(panel)
        assert panel is not None
        self.assertEqual(panel.get("xs"), [])
        self.assertEqual(panel["X"].shape, (8, 4))
        self.assertEqual(panel["x_names"][0], "f1")
        self.assertEqual(panel["metas"][0].get("tau"), "10:30")
        self.assertNotIn("l1_family", panel["metas"][0])
        self.assertNotIn("stock_code", panel["metas"][0])
        self.assertAlmostEqual(float(panel["metas"][0]["price_tau"]), 10.0)
        labeled = relabel_tau_panels_as_t30([panel])
        self.assertEqual(len(labeled), 1)
        self.assertEqual(labeled[0].get("xs"), [])
        self.assertEqual(labeled[0]["X"].shape[0], 8)
        self.assertIn("t30_lag1", labeled[0]["x_names"])
        compact = fold_horizon_enriched(labeled, ["f1", "gap_pct"], clear_panels=True)
        self.assertEqual(compact["X"].shape, (8, 2))
        self.assertAlmostEqual(float(compact["X"][0, 0]), 1.0)

    def test_breadth_matrix_stamps_theme_on_slim_meta(self):
        from core.research.tau_panel import attach_cross_section_breadth

        panel = {
            "code": "000001",
            "xs": [],
            "X": np.array([[3.0, 1.0], [0.2, 0.4]], dtype=np.float64),
            "x_names": ["gap_pct", "ret_open_to_tau"],
            "ys": [0.1, 0.2],
            "dates": ["2026-01-02", "2026-01-02"],
            "metas": [
                {"tau": "10:30", "price_tau": 10.0},
                {"tau": "11:00", "price_tau": 10.1},
            ],
        }
        out = attach_cross_section_breadth([panel], gap_trigger_pct=2.0)
        self.assertEqual(out[0].get("xs"), [])
        # 拟合矩阵缺列不扩表。主题记在 meta；入模列 z_τc_theme_day 已在表里才写回去。
        self.assertEqual(out[0]["x_names"], ["gap_pct", "ret_open_to_tau"])
        self.assertEqual(out[0]["X"].shape, (2, 2))
        self.assertIn("theme_day", out[0]["metas"][0])
        self.assertIn("gap_pct", out[0]["metas"][0])

    def test_memory_stop_before_the_process_is_killed(self):
        from core.research.horizon_l1 import (
            TAU_PANEL_BYTE_BUDGET,
            fit_memory_stop_reason,
        )

        self.assertEqual(
            fit_memory_stop_reason(panel_bytes=0, rss_bytes=0, ram_bytes=8 * 1024**3),
            "",
        )
        self.assertIn(
            "内存保护",
            fit_memory_stop_reason(
                panel_bytes=TAU_PANEL_BYTE_BUDGET,
                rss_bytes=0,
                ram_bytes=8 * 1024**3,
            ),
        )
        # 8GB 机器上进程到大约 5GB 就停，给系统和浏览器留余量
        self.assertIn(
            "内存保护",
            fit_memory_stop_reason(
                panel_bytes=0,
                rss_bytes=6 * 1024**3,
                ram_bytes=8 * 1024**3,
            ),
        )
        from core.research.horizon_l1 import physical_ram_bytes, process_rss_bytes

        rss = process_rss_bytes()
        ram = physical_ram_bytes()
        self.assertGreater(rss, 1024 * 1024)
        self.assertLess(rss, ram)

    def test_release_allocator_cache_drops_fit_day_cubes(self):
        """拟合打 z 留下的日立方必须卸掉，下一头才量得到真实水位。"""
        from core.research.day_cube import DayCube, clear_memory, load_day_cube, remember
        from core.research.feature_registry import universe_hash
        from core.research.horizon_l1 import release_allocator_cache

        codes = ["000001", "000002"]
        clear_memory()
        remember(
            DayCube(
                date="1900-01-01",
                codes=list(codes),
                names=["f1"],
                X_raw=np.ones((2, 1), dtype=np.float64),
                z_mean=np.zeros(1, dtype=np.float64),
                z_std=np.ones(1, dtype=np.float64),
                universe_hash=universe_hash(codes),
                clock="1000",
            )
        )
        self.assertIsNotNone(
            load_day_cube("1900-01-01", codes, clock="10:00")
        )
        release_allocator_cache()
        self.assertIsNone(
            load_day_cube("1900-01-01", codes, clock="10:00")
        )

    def test_release_allocator_cache_drops_minute_and_score_caches(self):
        """整段 5m 和做 T 特征行也要卸，否则下一头按旧水位直接停。"""
        from core.research.horizon_l1 import release_allocator_cache
        from core.research.tau_panel import (
            _MINUTE_BY_CODE,
            _REALIZED_BY_CODE_HM,
            clear_minute_lag_cache,
        )
        from core.signal.minute_tau_feats import _PEER_MINUTE_BARS, clear_peer_minute_bars
        from core.t0.score_policy import (
            _PEER_BARS_MEMO,
            _POOL_FEAT_CACHE,
            _TAU_LAKE_PEER_CACHE,
            clear_score_model_cache,
        )

        _MINUTE_BY_CODE["000001"] = {"2026-01-01": [{"close": 1.0}]}
        _REALIZED_BY_CODE_HM[("000001", "10:00", "t45")] = {"2026-01-01": 0.1}
        _POOL_FEAT_CACHE["2026-01-01"] = [{"stock_code": "000001"}]
        _TAU_LAKE_PEER_CACHE["2026-01-01|10:00"] = [{"stock_code": "000001"}]
        _PEER_BARS_MEMO["k"] = {"000001": [{"close": 1.0}]}
        _PEER_MINUTE_BARS["000001"] = [{"close": 1.0}]
        try:
            release_allocator_cache()
            self.assertEqual(_MINUTE_BY_CODE, {})
            self.assertEqual(_REALIZED_BY_CODE_HM, {})
            self.assertEqual(_POOL_FEAT_CACHE, {})
            self.assertEqual(_TAU_LAKE_PEER_CACHE, {})
            self.assertEqual(_PEER_BARS_MEMO, {})
            self.assertEqual(_PEER_MINUTE_BARS, {})
        finally:
            clear_minute_lag_cache()
            clear_score_model_cache()
            clear_peer_minute_bars()

    def test_release_allocator_cache_drops_l1_packs(self):
        """回测预热的整池 L1 必须卸掉，否则 800 只 × 三族大约 5 GB 一直占着。"""
        from core.research.horizon_l1 import release_allocator_cache
        from core.research.lake_replay import _COVERAGE, _L1_PACKS, clear_coverage_cache

        clear_coverage_cache()
        _L1_PACKS["oo"] = {
            "000001": {
                "X": np.ones((4, 2), dtype=np.float64),
                "dates": ["2020-01-02"] * 4,
            }
        }
        _COVERAGE["oo"] = {"000001": {"2020-01-02"}}
        try:
            release_allocator_cache()
            self.assertEqual(_L1_PACKS, {})
            self.assertEqual(_COVERAGE, {})
        finally:
            clear_coverage_cache()

    def test_minute_lag_cache_keeps_only_recent_codes(self):
        from core.research.tau_panel import (
            _MINUTE_BY_CODE,
            _MINUTE_BY_CODE_MAX,
            _REALIZED_BY_CODE_HM,
            _remember_minute_by_code,
            clear_minute_lag_cache,
        )

        clear_minute_lag_cache()
        try:
            kept = ""
            for i in range(int(_MINUTE_BY_CODE_MAX) + 2):
                code = f"{i:06d}"
                kept = code
                _REALIZED_BY_CODE_HM[(code, "10:00", "t45")] = {"2026-01-02": 0.1}
                _remember_minute_by_code(code, {"2026-01-02": [{"close": 1.0}]})
            self.assertLessEqual(len(_MINUTE_BY_CODE), int(_MINUTE_BY_CODE_MAX))
            self.assertIn(kept, _MINUTE_BY_CODE)
            self.assertNotIn("000000", _MINUTE_BY_CODE)
            self.assertNotIn(("000000", "10:00", "t45"), _REALIZED_BY_CODE_HM)
            self.assertIn((kept, "10:00", "t45"), _REALIZED_BY_CODE_HM)
        finally:
            clear_minute_lag_cache()

    def test_score_caches_drop_oldest_keys(self):
        import datetime

        from core.t0.score_policy import (
            _POOL_FEAT_CACHE,
            _POOL_FEAT_CACHE_MAX,
            _remember_bounded,
            clear_score_model_cache,
            seed_watching_pool_feature_rows,
        )

        clear_score_model_cache()
        try:
            items = [
                {"stock_code": "000001", "features_tau": {"gap_pct": 1.0}},
                {"stock_code": "000002", "features_tau": {"gap_pct": 2.0}},
            ]
            base = datetime.date(2020, 1, 1)
            last = ""
            for i in range(int(_POOL_FEAT_CACHE_MAX) + 3):
                last = (base + datetime.timedelta(days=i)).isoformat()
                self.assertTrue(seed_watching_pool_feature_rows(last, items))
            self.assertLessEqual(len(_POOL_FEAT_CACHE), int(_POOL_FEAT_CACHE_MAX))
            self.assertIn(last, _POOL_FEAT_CACHE)
            self.assertNotIn("2020-01-01", _POOL_FEAT_CACHE)
            box: dict = {}
            for i in range(5):
                _remember_bounded(box, i, i, 2)
            self.assertEqual(list(box), [3, 4])
        finally:
            clear_score_model_cache()

    def test_stale_footprint_is_reread_after_releasing_pages(self):
        from core.research.horizon_l1 import (
            confirm_fit_memory_stop,
            release_allocator_cache,
        )

        release_allocator_cache()
        ram = 8 * 1024**3
        # 上一头留下的水位：归还空闲页后再量，已经回到安全区就继续
        self.assertEqual(
            confirm_fit_memory_stop(
                panel_bytes=0,
                rss_bytes=6 * 1024**3,
                ram_bytes=ram,
                reread_rss=lambda: 1024**3,
            ),
            "",
        )
        # 归还之后进程仍撑满，才停
        self.assertIn(
            "内存保护",
            confirm_fit_memory_stop(
                panel_bytes=1024,
                rss_bytes=6 * 1024**3,
                ram_bytes=ram,
                reread_rss=lambda: 6 * 1024**3,
            ),
        )

    def test_meta_price_matrix_roundtrip(self):
        from core.research.horizon_l1 import (
            meta_price_matrix_from_rows,
            metas_from_price_matrix,
        )

        metas = [
            {
                "date": "2026-04-01",
                "tau": "10:30",
                "price_tau": 12.5,
                "price_tau60": 12.8,
            }
        ]
        M, names = meta_price_matrix_from_rows(metas)
        back = metas_from_price_matrix(
            M, names, dates=["2026-04-01"], code="000001"
        )
        self.assertEqual(back[0].get("tau"), "10:30")
        self.assertAlmostEqual(float(back[0]["price_tau"]), 12.5)
        self.assertAlmostEqual(float(back[0]["price_tau60"]), 12.8)

    def test_family_for_horizon(self):
        from core.research.horizon_l1 import family_for_horizon, list_hz_l1_families
        from core.research.lake_bridge import family_for_horizon as bridge_hz

        self.assertEqual(family_for_horizon("t60"), "hz_t60")
        self.assertEqual(bridge_hz("τ30"), "hz_t30")
        self.assertEqual(len(list_hz_l1_families()), 5)

    def test_probe_tau_grid_l1_coverage(self):
        from core.research.horizon_l1 import probe_tau_grid_l1_coverage

        with mock.patch(
            "core.research.feature_lake.enabled", return_value=True
        ), mock.patch(
            "core.research.feature_lake.read_stock_meta",
            side_effect=lambda fam, code: (
                {"m_names": ["tau_hm", "price_tau", "price_tau30"]}
                if str(fam).endswith("0935")
                else None
            ),
        ):
            cov = probe_tau_grid_l1_coverage(
                ["000001", "000002"],
                ["09:35", "10:30"],
                sample_n=10,
            )
        self.assertEqual(cov["n_slots"], 4)
        self.assertEqual(cov["with_meta"], 2)
        self.assertAlmostEqual(cov["rate"], 0.5)
        self.assertTrue(cov["cold"])
        self.assertTrue(cov.get("probe_ok"))
        self.assertIn("物化", cov.get("hint") or "")

    def test_tau_l1_coverage_helpers_fail_closed(self):
        from core.research.horizon_l1 import (
            tau_l1_coverage_blocks_fit,
            tau_l1_hit_rate_too_low,
        )

        self.assertTrue(tau_l1_coverage_blocks_fit(None))
        self.assertTrue(tau_l1_coverage_blocks_fit({}))
        self.assertTrue(
            tau_l1_coverage_blocks_fit({"cold": False, "probe_ok": False})
        )
        self.assertTrue(
            tau_l1_coverage_blocks_fit(
                {"cold": False, "probe_ok": True, "lake_enabled": False}
            )
        )
        self.assertFalse(
            tau_l1_coverage_blocks_fit(
                {"cold": False, "probe_ok": True, "lake_enabled": True}
            )
        )
        self.assertTrue(
            tau_l1_hit_rate_too_low(
                l1_head_hit=0, l1_stitch_hit=2, rebuild_5m=22, min_attempted=24
            )
        )
        self.assertFalse(
            tau_l1_hit_rate_too_low(
                l1_head_hit=20, l1_stitch_hit=0, rebuild_5m=4, min_attempted=24
            )
        )

    def test_horizon_fit_blocks_when_l1_tau_cold(self):
        """产品只读路径：τ 网格 L1 cold → materialize_required，不啃 5m。"""
        from core.research.materialize.consumer import (
            MaterializeRequired,
            materialized_consumer,
        )
        from quant.services.quant_service_factors import QuantFactorMixin

        svc = QuantFactorMixin()
        with mock.patch(
            "core.research_universe.resolve_model_fit_codes",
            return_value={
                "ok": True,
                "codes": ["000001", "000002"],
                "source": "watching",
            },
        ), mock.patch(
            "core.research.materialize.cover_policy.resolve_fit_minute_covered_dates",
            return_value=["2026-04-01", "2026-04-02"],
        ), mock.patch(
            "core.research.horizon_l1.probe_tau_grid_l1_coverage",
            return_value={
                "rate": 0.0,
                "present": 10,
                "with_meta": 0,
                "n_probed": 2,
                "n_slots": 36,
                "cold": True,
                "probe_ok": True,
                "hint": "τ 网格 L1 热覆盖约 0%；请先跑分钟线「物化特征（L1/L2）」",
                "lake_enabled": True,
            },
        ), mock.patch(
            "core.store.load_minute_cache",
        ) as load_min, mock.patch(
            "core.research.tc_model_compact.build_tau_panels_from_bars",
        ) as build_panels:
            with self.assertRaises(MaterializeRequired) as ctx:
                with materialized_consumer():
                    svc._run_horizon_ridge_experiment(
                        "t30",
                        watching_limit=2,
                        lookback=40,
                        tau_end_hm="11:00",
                        cross_section_zscore=True,
                    )
        payload = ctx.exception.to_dict()
        self.assertEqual(payload.get("error_code"), "materialize_required")
        self.assertEqual(payload.get("reason"), "cold")
        self.assertIn("分钟线物化", str(payload.get("error") or ""))
        self.assertEqual(payload.get("hint"), "分钟线物化")
        self.assertTrue((payload.get("request") or {}).get("do_l1_tau_grid"))
        load_min.assert_not_called()
        build_panels.assert_not_called()

    def test_horizon_fit_blocks_when_probe_raises(self):
        """探测异常 → fail-closed，不静默啃 5m。"""
        from core.research.materialize.consumer import (
            MaterializeRequired,
            materialized_consumer,
        )
        from quant.services.quant_service_factors import QuantFactorMixin

        svc = QuantFactorMixin()
        with mock.patch(
            "core.research_universe.resolve_model_fit_codes",
            return_value={
                "ok": True,
                "codes": ["000001", "000002"],
                "source": "watching",
            },
        ), mock.patch(
            "core.research.materialize.cover_policy.resolve_fit_minute_covered_dates",
            return_value=["2026-04-01"],
        ), mock.patch(
            "core.research.horizon_l1.probe_tau_grid_l1_coverage",
            side_effect=RuntimeError("boom"),
        ), mock.patch(
            "core.store.load_minute_cache",
        ) as load_min:
            with self.assertRaises(MaterializeRequired) as ctx:
                with materialized_consumer():
                    svc._run_horizon_ridge_experiment(
                        "t30",
                        watching_limit=2,
                        lookback=40,
                        tau_end_hm="11:00",
                    )
        self.assertEqual(ctx.exception.to_dict().get("reason"), "probe_failed")
        load_min.assert_not_called()

    def test_horizon_fit_early_aborts_on_fingerprint_miss(self):
        """探测热但 stitch 全 miss：攒满 early 门槛后阻断，不启动 5m 池。"""
        from core.research.materialize.consumer import (
            MaterializeRequired,
            materialized_consumer,
        )
        from quant.services.quant_service_factors import QuantFactorMixin

        dates = [f"2026-04-{d:02d}" for d in range(1, 20)]
        bars = [
            {
                "date": d,
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "volume": 1e5,
            }
            for d in dates
        ]
        codes = [f"{i:06d}" for i in range(1, 30)]
        svc = QuantFactorMixin()
        with mock.patch(
            "core.research_universe.resolve_model_fit_codes",
            return_value={"ok": True, "codes": codes, "source": "watching"},
        ), mock.patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            side_effect=lambda codes, **_kw: (
                {str(c): list(bars) for c in codes},
                [],
                {},
            ),
        ), mock.patch(
            "core.research.materialize.cover_policy.resolve_fit_minute_covered_dates",
            return_value=dates[:5],
        ), mock.patch(
            "core.research.horizon_l1.probe_tau_grid_l1_coverage",
            return_value={
                "rate": 0.95,
                "present": 100,
                "with_meta": 95,
                "cold": False,
                "probe_ok": True,
                "lake_enabled": True,
                "hint": "热",
            },
        ), mock.patch(
            "core.research.horizon_l1.try_load_horizon_head_panel",
            return_value=None,
        ), mock.patch(
            "core.research.horizon_l1.try_stitch_horizon_l1_panel",
            return_value=None,
        ), mock.patch(
            "core.store.load_minute_cache",
            return_value=([{"datetime": "2026-04-01 09:35"}], {}),
        ), mock.patch(
            "core.research.tc_model_compact.build_tau_panels_from_bars",
        ) as build_panels:
            with self.assertRaises(MaterializeRequired) as ctx:
                with materialized_consumer():
                    svc._run_horizon_ridge_experiment(
                        "t30",
                        watching_limit=len(codes),
                        lookback=40,
                        tau_end_hm="11:00",
                        cross_section_zscore=True,
                    )
        payload = ctx.exception.to_dict()
        self.assertEqual(payload.get("reason"), "early_abort")
        self.assertGreaterEqual(int(payload.get("rebuild_5m") or 0), 24)
        build_panels.assert_not_called()

    def test_horizon_fit_l1_stitch_skips_minute_cache(self):
        """L1 stitch 全命中时不读 load_minute_cache。"""
        from quant.services.quant_service_factors import QuantFactorMixin

        dates = [f"2026-04-{d:02d}" for d in range(1, 20)]
        bars = [
            {
                "date": d,
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "volume": 1e5,
            }
            for d in dates
        ]

        def _stitch(code, bars_in, clocks, **kwargs):
            n = 6
            return {
                "code": code,
                "xs": [{"gap_pct": 1.0}] * n,
                "ys": [0.01] * n,
                "dates": dates[:n],
                "metas": [
                    {
                        "date": dates[i],
                        "tau": "09:35",
                        "price_tau": 10.0,
                        "price_tau30": 10.1,
                        "l1_hit": True,
                    }
                    for i in range(n)
                ],
                "l1_stitched": True,
            }

        dual = {
            "success": True,
            "timing": {},
            "y_spec": {},
            "oos": {"auc": 0.55},
        }
        msgs = []
        svc = QuantFactorMixin()
        with mock.patch(
            "core.research_universe.resolve_model_fit_codes",
            return_value={
                "ok": True,
                "codes": ["000001", "000002"],
                "source": "watching",
            },
        ), mock.patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            side_effect=lambda codes, **_kw: (
                {str(c): list(bars) for c in codes},
                [],
                {},
            ),
        ), mock.patch(
            "core.research.materialize.cover_policy.resolve_fit_minute_covered_dates",
            return_value=dates[:5],
        ), mock.patch(
            "core.research.horizon_l1.probe_tau_grid_l1_coverage",
            return_value={
                "rate": 1.0,
                "present": 4,
                "with_meta": 4,
                "cold": False,
                "hint": "τ 网格 L1 就绪约 100%",
                "lake_enabled": True,
            },
        ), mock.patch(
            "core.research.horizon_l1.try_load_horizon_head_panel",
            return_value=None,
        ), mock.patch(
            "core.research.horizon_l1.try_stitch_horizon_l1_panel",
            side_effect=_stitch,
        ), mock.patch(
            "core.store.load_minute_cache",
        ) as load_min, mock.patch(
            "core.research.horizon_ridge.enrich_horizon_raw_panels",
            side_effect=lambda h, raw, **kw: list(raw),
        ), mock.patch(
            "core.research.horizon_l1.sync_horizon_head_panel",
            return_value=True,
        ), mock.patch(
            "core.research.horizon_ridge.prepare_horizon_compact",
            return_value={
                "compact": {"X": None},
                "live_hm": "11:00",
                "tau_grid": ["09:35", "10:00"],
                "head": "t30",
            },
        ), mock.patch(
            "core.research.horizon_ridge.fit_horizon_dual_report",
            return_value=dict(dual),
        ), mock.patch(
            "core.research.horizon_ridge.save_ridge_last_report",
        ), mock.patch(
            "core.research.horizon_ridge.ridge_model_path",
            return_value="/tmp/t30.json",
        ), mock.patch(
            "core.research.horizon_ridge.load_ridge_model",
            return_value=None,
        ), mock.patch(
            "quant.services.quant_service_factors._attach_ridge_role_flags",
            side_effect=lambda r, *a, **k: r,
        ):
            out = svc._run_horizon_ridge_experiment(
                "t30",
                watching_limit=2,
                lookback=40,
                tau_end_hm="10:00",
                cross_section_zscore=True,
                progress_cb=lambda c, t, m: msgs.append(str(m)),
            )
        load_min.assert_not_called()
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("l1_stitch_hit"), 2)
        self.assertEqual(out.get("rebuild_5m"), 0)
        self.assertGreaterEqual(float(out.get("l1_hit_rate") or 0), 0.99)
        self.assertTrue(any("L1命中" in m for m in msgs))

if __name__ == "__main__":
    unittest.main()
