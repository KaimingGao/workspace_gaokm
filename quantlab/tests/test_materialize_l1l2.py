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
                    "families": ["oo", "tc", "co"],
                    "by_family": {
                        "oo": {"ok": True, "elapsed_s": 12.0, "n_stocks": 800},
                        "tc": {"ok": True, "elapsed_s": 18.0, "n_stocks": 800},
                        "co": {"ok": True, "elapsed_s": 10.0, "n_stocks": 800},
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
            ["oo", "tc", "co"],
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
        self.assertIn("co", ids)
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
        self.assertIn("fit_use_day_cube", doc["flags"])
        self.assertIn("tau_grid", doc)
        self.assertEqual(doc["tau_grid"].get("option"), "do_l1_tau_grid")
        self.assertTrue(doc["tau_grid"].get("requires_minute"))
        self.assertEqual(doc["tau_grid"].get("clocks"), clocks)

    def test_normalize_families_default_p0(self):
        from core.research.materialize.catalog import normalize_families

        self.assertEqual(normalize_families(None), ["oo", "tc", "co"])
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

    def test_l2_lookback_prefers_l2_lookback_days(self):
        """L2 窗用 l2_lookback_days，不被 L1 lookback=1000 盖掉。"""
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
                l2_lookback_days=5,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(l2_fn.call_args.kwargs.get("lookback_days"), 5)

    def test_implicit_oo_rank_failure_keeps_p0_ok(self):
        """未显式请求 oo_rank 时，rank 失败不把 oo/tc/co 整次打失败。"""
        from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_OO_RANK, FAMILY_TC
        from core.research.materialize.l1_jobs import _materialize_l1_body

        def _ok_fam(*_a, **_k):
            return {"ok": True, "n": 2}

        with mock.patch(
            "core.research.materialize.l1_jobs._materialize_oo", side_effect=_ok_fam
        ), mock.patch(
            "core.research.materialize.l1_jobs._materialize_tc", side_effect=_ok_fam
        ), mock.patch(
            "core.research.materialize.l1_jobs._materialize_co", side_effect=_ok_fam
        ), mock.patch(
            "core.research.materialize.l1_jobs._materialize_oo_rank",
            return_value={"ok": False, "error": "rank boom"},
        ):
            out = _materialize_l1_body(
                [FAMILY_OO, FAMILY_TC, FAMILY_CO],
                ["000001", "000002"],
                lookback=10,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("oo_rank_optional_failed"))
        self.assertTrue((out["by_family"].get(FAMILY_OO) or {}).get("ok"))
        self.assertFalse((out["by_family"].get(FAMILY_OO_RANK) or {}).get("ok"))

    def test_implicit_oo_rank_cancel_keeps_p0_ok(self):
        """可选 oo_rank 进行中取消：P0 已齐则 L1 仍成功。"""
        from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC
        from core.research.materialize.l1_jobs import _materialize_l1_body

        def _ok_fam(*_a, **_k):
            return {"ok": True, "n": 2}

        calls = {"n": 0}

        def _cancel() -> bool:
            # 顺序 oo→tc→co→oo_rank：第 4 次检查进入可选 rank
            calls["n"] += 1
            return calls["n"] >= 4

        with mock.patch(
            "core.research.materialize.l1_jobs._materialize_oo", side_effect=_ok_fam
        ), mock.patch(
            "core.research.materialize.l1_jobs._materialize_tc", side_effect=_ok_fam
        ), mock.patch(
            "core.research.materialize.l1_jobs._materialize_co", side_effect=_ok_fam
        ), mock.patch(
            "core.research.materialize.l1_jobs._materialize_oo_rank",
            side_effect=AssertionError("oo_rank should not run after cancel"),
        ):
            out = _materialize_l1_body(
                [FAMILY_OO, FAMILY_TC, FAMILY_CO],
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
            "fit_use_day_cube",
            "run_materialize",
            "build_materialize_track",
            "cs_z_matrix_prefer_cube",
            "cs_z_dicts_prefer_cube",
            "apply_day_cube_mu_sigma_matrix",
            "horizon_l1_feature_names",
            "names_cover_horizon",
        ):
            self.assertTrue(callable(getattr(m, name, None)), name)

class PreferCubeHelpersTests(unittest.TestCase):
    def test_cs_z_matrix_prefer_cube_uses_flag(self):
        import numpy as np

        from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

        class _Cube:
            names = ["f1"]
            z_mean = np.array([0.0])
            z_std = np.array([1.0])

        x = np.array([[1.0], [3.0]], dtype=np.float64)
        with mock.patch(
            "core.research.materialize.flags.fit_use_day_cube", return_value=True
        ), mock.patch(
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

class CubeZPerClockTests(unittest.TestCase):
    def test_date_tau_loads_matching_clock(self):
        import numpy as np

        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["f1"]
            z_mean = np.array([1.0])
            z_std = np.array([2.0])

        loads = []

        def _load(day, codes, clock="open"):
            loads.append((str(day)[:10], str(clock)))
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
        clocks = {c for _, c in loads}
        self.assertIn("09:35", clocks)
        self.assertIn("10:30", clocks)

    def test_clock_miss_falls_back_to_open(self):
        import numpy as np

        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["f1"]
            z_mean = np.array([0.0])
            z_std = np.array([1.0])

        def _load(day, codes, clock="open"):
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
        import numpy as np

        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["f1"]
            z_mean = np.array([0.0])
            z_std = np.array([1.0])

        def _load(day, codes, clock="open"):
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

    def test_partial_cube_miss_uses_matrix_fallback(self):
        """5m 近端有 Cube、窗外缺日：有多少用多少，不整单失败。"""
        import numpy as np

        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["f1"]
            z_mean = np.array([0.0])
            z_std = np.array([1.0])

        def _load(day, codes, clock="open"):
            if str(day)[:10] == "2026-08-04" and str(clock) == "09:35":
                return _Cube()
            return None

        # 两日各两行：有 Cube 的日用 L2；(x-0)/1；缺日桶内 z
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
        self.assertTrue(meta.get("ok"), meta)
        self.assertEqual(meta.get("hit_days"), 1)
        self.assertEqual(meta.get("miss_days"), 1)
        self.assertEqual(meta.get("matrix_fallback_days"), 1)
        self.assertAlmostEqual(float(z[0, 0]), 1.0, places=5)
        self.assertAlmostEqual(float(z[1, 0]), 3.0, places=5)
        # 桶内 μ=20 σ=10 → -1 / +1
        self.assertAlmostEqual(float(z[2, 0]), -1.0, places=5)
        self.assertAlmostEqual(float(z[3, 0]), 1.0, places=5)

    def test_missing_model_column_matrix_fallback(self):
        """Cube 缺列（如截面广度 z_τh_sector_*）→ 桶内 z，不整单失败。"""
        import numpy as np

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
        self.assertIn("f2", meta.get("matrix_fallback_cols") or [])
        # f1 用 Cube：(1-0)/1=1；(3-0)/1=3
        self.assertAlmostEqual(float(z[0, 0]), 1.0, places=5)
        self.assertAlmostEqual(float(z[1, 0]), 3.0, places=5)
        # f2 桶内 μ=3 σ=1 → -1 / +1
        self.assertAlmostEqual(float(z[0, 1]), -1.0, places=5)
        self.assertAlmostEqual(float(z[1, 1]), 1.0, places=5)

class NamesCoverHorizonTests(unittest.TestCase):
    def test_slim_rejected(self):
        from core.research.materialize.horizon_names import names_cover_horizon

        class _Slim:
            names = ["gap", "gap_pct"]

        self.assertFalse(names_cover_horizon(_Slim()))

class HorizonL1StitchTests(unittest.TestCase):
    def test_try_stitch_l1_tau_panel(self):
        import numpy as np

        from core.research.tc_ridge_compact import _try_stitch_l1_tau_panel

        class _Hit:
            pass

        X = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]], dtype=np.float64)
        y = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float64)
        dates = ["2026-01-02", "2026-01-03", "2026-01-06", "2026-01-07"]
        names = ["f1", "f2"]

        def _load(family, code, fp, names=None, require_y=True):
            return X, y, names or ["f1", "f2"], dates, "l1"

        bars = [{"date": d, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for d in dates]
        bars += [{"date": "2026-01-08", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}] * 10
        with mock.patch(
            "core.research.lake_bridge.load_training_matrix", side_effect=_load
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
                [{"datetime": "2026-01-02 10:30:00", "close": 10.0}],
                ["10:30", "11:00"],
                min_history=12,
                include_alpha_tc=True,
                include_alpha_th=True,
            )
        self.assertIsNotNone(panel)
        self.assertTrue(panel.get("l1_stitched"))
        self.assertEqual(len(panel["xs"]), 8)  # 4 days × 2 clocks
        self.assertEqual(panel["metas"][0].get("tau"), "10:30")
        self.assertTrue(panel.get("minute_bars"))

class MaterializeL2UniverseHashTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_gc = os.environ.get("QUANTLAB_DAY_CUBE_GC")
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"

    def tearDown(self) -> None:
        if self._prev_gc is None:
            os.environ.pop("QUANTLAB_DAY_CUBE_GC", None)
        else:
            os.environ["QUANTLAB_DAY_CUBE_GC"] = self._prev_gc

    def test_missing_lake_row_keeps_requested_universe(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        present = {"000001": {"x": 1.0}, "000002": {"x": 2.0}}

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            if "tc" not in str(family):
                return {}
            out = {}
            for code in codes:
                if code not in present:
                    continue
                out[code] = (
                    ["x"],
                    {
                        str(d)[:10]: np.asarray([present[code]["x"]], dtype=np.float64)
                        for d in dates
                    },
                )
            return out

        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            return_value=True,
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["raw_ath_x"]),
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002", "001232"],
                dates=["2026-10-16"],
                clocks=["09:35"],
            )
        self.assertTrue(out.get("ok"), out)
        ensure.assert_called_once()
        self.assertEqual(
            list(ensure.call_args.kwargs["universe_codes"]),
            ["000001", "000002", "001232"],
        )
        self.assertFalse(ensure.call_args.kwargs.get("replace"))

    def test_short_cube_is_replaced_once(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        class _Cube:
            def __init__(self, names):
                self.names = list(names)

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            if "tc" not in str(family):
                return {}
            return {
                code: (
                    ["raw_ath_x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
                for code in ("000001", "000002")
                if code in codes
            }

        with mock.patch(
            "core.research.day_cube.load_day_cube",
            return_value=_Cube(["gap_pct"]),
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            side_effect=lambda cube, **_k: "raw_ath_x" in set(getattr(cube, "names", []) or []),
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=_Cube(["raw_ath_x", "a", "b"]),
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002", "001232"],
                dates=["2026-10-16"],
                clocks=["09:35"],
                require_horizon_names=True,
            )
        self.assertTrue(out.get("ok"), out)
        ensure.assert_called_once()
        self.assertTrue(ensure.call_args.kwargs.get("replace"))
        clock = out["by_clock"]["09:35"]
        self.assertEqual(clock["cubes_built"], 1)
        self.assertEqual(clock["slim_reject"], 0)
        self.assertEqual(clock["cubes_hit"], 0)

    def test_cube_load_error_skips_day(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        with mock.patch(
            "core.research.day_cube.load_day_cube",
            side_effect=RuntimeError("io"),
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35"],
            )
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "立方读取失败")
        ensure.assert_not_called()
        self.assertEqual(out["by_clock"]["09:35"]["error"], "立方读取失败")
        self.assertEqual(out["by_clock"]["09:35"]["days_skipped"], 1)
        self.assertEqual(out["by_clock"]["09:35"]["cubes_built"], 0)

    def test_horizon_check_error_still_replaces(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        class _Cube:
            names = ["raw_ath_x", "a", "b"]

        seen = {"n": 0}

        def _cover(_cube, **_kwargs):
            seen["n"] += 1
            if seen["n"] == 1:
                raise RuntimeError("bad names")
            return True

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            if "tc" not in str(family):
                return {}
            return {
                code: (
                    ["x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
                for code in codes
            }

        with mock.patch(
            "core.research.day_cube.load_day_cube",
            return_value=_Cube(),
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            side_effect=_cover,
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=_Cube(),
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35"],
                require_horizon_names=True,
            )
        self.assertTrue(out.get("ok"), out)
        ensure.assert_called_once()
        self.assertTrue(ensure.call_args.kwargs.get("replace"))
        self.assertEqual(out["by_clock"]["09:35"]["cubes_built"], 1)

    def test_open_warmup_error_does_not_stop_later_clock(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            if "tc" not in str(family):
                return {}
            return {
                code: (
                    ["x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
                for code in codes
            }

        with mock.patch(
            "core.t0.score_policy.warmup_watching_day_cubes",
            side_effect=RuntimeError("warmup boom"),
        ), mock.patch(
            "core.research.day_cube.load_day_cube",
            return_value=None,
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            return_value=True,
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["x"]),
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["open", "09:35"],
                min_lake_frac="nope",
            )
        self.assertFalse(out.get("ok"))
        self.assertIn("warmup boom", out["by_clock"]["open"]["error"])
        self.assertTrue(out["by_clock"]["09:35"]["ok"])
        self.assertEqual(out["by_clock"]["09:35"]["cubes_built"], 1)
        ensure.assert_called_once()

    def test_cancel_mid_clock_is_not_success(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        state = {"n": 0}

        def _load(*_a, **_k):
            state["n"] += 1
            return None

        with mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35", "10:30"],
                cancel_cb=lambda: state["n"] >= 1,
            )
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "cancelled")
        self.assertFalse(out["by_clock"]["09:35"]["ok"])
        self.assertEqual(out["by_clock"]["09:35"]["error"], "cancelled")
        self.assertNotIn("10:30", out["by_clock"])
        ensure.assert_not_called()

    def test_cancel_after_first_clock_probe_does_not_report_it_done(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        state = {"n": 0}

        def _cancel() -> bool:
            state["n"] += 1
            return state["n"] >= 3

        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35", "10:30"],
                cancel_cb=_cancel,
            )
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "cancelled")
        self.assertEqual(out["by_clock"]["09:35"]["error"], "cancelled")
        self.assertNotIn("10:30", out["by_clock"])
        ensure.assert_not_called()

    def test_cancel_during_write_marks_later_clocks(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        state = {"n": 0}

        def _build(*_args, **_kwargs):
            state["n"] += 1
            return {"cubes_built": 1, "days_skipped": 0, "slim_reject": 0}

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            return {
                code: (
                    ["x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
                for code in codes
            }

        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.materialize.cube_batch.build_day_cubes_from_lake",
            side_effect=_build,
        ):
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35", "10:30"],
                cancel_cb=lambda: state["n"] >= 1,
            )
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "cancelled")
        self.assertEqual(out["by_clock"]["09:35"]["error"], "cancelled")
        self.assertEqual(out["by_clock"]["10:30"]["error"], "cancelled")
        self.assertEqual(state["n"], 1)

    def test_one_load_error_keeps_a_hit_day(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        def _load(day, *_args, **_kwargs):
            if str(day)[:10] == "2026-10-16":
                raise RuntimeError("io")
            return object()

        with mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16", "2026-10-17"],
                clocks=["09:35"],
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["by_clock"]["09:35"]["days_skipped"], 1)
        self.assertEqual(out["by_clock"]["09:35"]["cubes_hit"], 1)
        ensure.assert_not_called()

    def test_minute_clock_fills_the_covered_days_once(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        seen = []

        def _build(codes, days, **kwargs):
            day_list = list(days)
            seen.append((day_list, kwargs.get("chunk_days")))
            # 覆盖闸按 hit+built 计；mock 须回报建成数
            return {
                "cubes_built": len(day_list),
                "days_skipped": 0,
                "slim_reject": 0,
            }

        dates = [f"2024-01-{day:02d}" for day in range(1, 22)]
        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), mock.patch(
            "core.research.materialize.cube_batch.build_day_cubes_from_lake",
            side_effect=_build,
        ):
            out = materialize_l2(
                ["000001", "000002"],
                dates=dates,
                clocks=["09:35"],
            )
        self.assertTrue(out.get("ok"), out)
        # 近端优先：一次扫完整窗
        self.assertEqual(seen, [(list(reversed(dates)), None)])

    def test_auto_minute_window_skips_days_without_bars(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        calendar = [f"2024-06-{day:02d}" for day in range(1, 31)]
        covered = calendar[:5]
        seen = []

        def _load(day, *_a, **_k):
            seen.append(str(day)[:10])
            return object()

        with mock.patch(
            "core.research.materialize.l2_jobs._resolve_dates", return_value=calendar
        ), mock.patch(
            "core.store.list_minute_covered_dates", return_value=covered
        ), mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ):
            out = materialize_l2(
                ["000001", "000002"],
                clocks=["09:35", "10:30"],
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("minute_days"), 5)
        self.assertEqual(out.get("minute_span"), "2024-06-01→2024-06-05")
        # L2 烘立方近端优先（新→旧）；两钟各扫一遍
        near_first = list(reversed(covered))
        self.assertEqual(seen, near_first + near_first)
        self.assertEqual(out["by_clock"]["09:35"]["cubes_hit"], 5)
        self.assertEqual(out["by_clock"]["10:30"]["cubes_hit"], 5)

    def test_explicit_dates_do_not_query_minute_store(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        def _dates(*_a, **_k):
            raise AssertionError("explicit dates must not query the minute store")

        with mock.patch(
            "core.store.list_minute_covered_dates", side_effect=_dates
        ), mock.patch(
            "core.research.day_cube.load_day_cube", return_value=object()
        ):
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35"],
            )
        self.assertTrue(out.get("ok"), out)
        self.assertNotIn("minute_days", out)
        self.assertEqual(out["by_clock"]["09:35"]["cubes_hit"], 1)

    def test_empty_minute_store_does_not_scan_calendar(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        calendar = [f"2024-06-{day:02d}" for day in range(1, 31)]

        def _load(*_a, **_k):
            raise AssertionError("empty minute window must not probe cubes")

        with mock.patch(
            "core.research.materialize.l2_jobs._resolve_dates", return_value=calendar
        ), mock.patch(
            "core.store.list_minute_covered_dates", return_value=[]
        ), mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ):
            out = materialize_l2(
                ["000001", "000002"],
                clocks=["09:35"],
            )
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "本地 5m 没有够格的交易日")
        self.assertEqual(out.get("minute_days"), 0)
        self.assertEqual(out["by_clock"]["09:35"]["error"], "本地 5m 没有够格的交易日")

    def test_bad_lookback_still_resolves_dates(self) -> None:
        from core.research.materialize.l2_jobs import _resolve_dates

        days = _resolve_dates(None, lookback_days="nope", use_trading_days=False)
        self.assertGreaterEqual(len(days), 2)
        self.assertLessEqual(len(days), 1500)

    def test_tau_grid_lake_uses_unscaled_span(self) -> None:
        from core.research.materialize.l1_jobs import materialize_l1_tau_grid
        from core.research.panel_matrix import _TAU_POOL_BATCH

        n = max(48, int(_TAU_POOL_BATCH) * 2)
        codes = [f"{i:06d}" for i in range(n)]
        days = [f"2026-01-{d:02d}" for d in range(1, 29)]  # 28 个日历日当探针跨度

        def _items(cs, lookback=0):
            bars = {c: [{"date": d, "close": 1.0} for d in days] for c in cs}
            return (bars, [], [])

        def _mins(packs):
            for row in packs:
                row["minute_bars"] = [{"date": d} for d in days]
            return len(packs)

        def _collect(packs, *_a, **_k):
            return {
                "minute_attached": len(packs),
                "by_clock": {
                    "09:35": {
                        "ok": True,
                        "n_stocks": len(packs),
                        "n_dates": len(days),
                        "n_rows": len(packs) * len(days),
                    }
                },
            }

        with mock.patch(
            "core.research.materialize.l1_jobs._stock_bar_items", side_effect=_items
        ), mock.patch(
            "core.research.materialize.l1_jobs._attach_minute_bars", side_effect=_mins
        ), mock.patch(
            "core.research.panel_matrix.collect_tau_grid_panels", side_effect=_collect
        ):
            out = materialize_l1_tau_grid(codes, clocks=["09:35"])
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("lake_span_days"), len(days))
        self.assertTrue(out["by_clock"]["09:35"].get("ok"))

    def test_tau_grid_cancel_is_not_success(self) -> None:
        from core.research.materialize.l1_jobs import materialize_l1_tau_grid

        def _items(codes, lookback=0):
            return ({c: [{"date": "2026-01-02"}] for c in codes}, [], [])

        def _mins(packs):
            for row in packs:
                row["minute_bars"] = [{"datetime": "2026-01-02 09:35:00"}]
            return len(packs)

        with mock.patch(
            "core.research.materialize.l1_jobs._stock_bar_items", side_effect=_items
        ), mock.patch(
            "core.research.materialize.l1_jobs._attach_minute_bars", side_effect=_mins
        ):
            out = materialize_l1_tau_grid(
                ["000001", "000002"],
                clocks=["09:35", "10:30"],
                cancel_cb=lambda: True,
            )
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "cancelled")

    def test_tau_grid_bars_error_returns_report(self) -> None:
        from core.research.materialize.l1_jobs import materialize_l1_tau_grid

        with mock.patch(
            "core.research.materialize.l1_jobs._stock_bar_items",
            side_effect=RuntimeError("bars down"),
        ):
            out = materialize_l1_tau_grid(
                ["000001", "000002"],
                clocks=["09:35"],
            )
        self.assertFalse(out.get("ok"))
        self.assertIn("bars down", str(out.get("error")))

class DayMatrixCsEnrichTests(unittest.TestCase):
    def test_matrix_matches_panel_breadth(self) -> None:
        from core.research.tau_panel import (
            attach_cross_section_breadth,
            enrich_day_matrix_cross_section,
        )

        day = "2026-01-05"
        rows = [
            ("000001", 3.0, 2.0),
            ("000002", 1.0, 0.5),
            ("000003", 0.5, -1.0),
            ("600519", 4.0, 1.5),
        ]
        sm = {
            "000001": "银行",
            "000002": "银行",
            "000003": "银行",
            "600519": "白酒",
        }
        panels = []
        for code, gap, rot in rows:
            panels.append(
                {
                    "code": code,
                    "xs": [{"gap_pct": gap, "ret_open_to_tau": rot}],
                    "ys": [0.0],
                    "dates": [day],
                    "metas": [
                        {
                            "date": day,
                            "stock_code": code,
                            "tau": "09:35",
                            "gap_pct": gap,
                        }
                    ],
                }
            )
        enriched = attach_cross_section_breadth(
            panels, gap_trigger_pct=2.0, sector_map=sm
        )
        codes = [r[0] for r in rows]
        names = ["gap_pct", "ret_open_to_tau"]
        x = np.asarray([[r[1], r[2]] for r in rows], dtype=np.float64)
        out_names, out_x, added = enrich_day_matrix_cross_section(
            codes, names, x, clock="09:35", gap_trigger_pct=2.0, sector_map=sm
        )
        self.assertIn("sector_gap_breadth", added)
        self.assertIn("z_τh_sector_gap_breadth", out_names)
        pos = {n: j for j, n in enumerate(out_names)}
        for i, p in enumerate(enriched):
            xs0 = p["xs"][0]
            for key in (
                "sector_gap_breadth",
                "theme_day",
                "gap_vs_sector",
                "sector_ret_to_tau",
                "ret_vs_sector",
            ):
                j = pos[key]
                self.assertAlmostEqual(
                    float(out_x[i, j]),
                    float(xs0[key]),
                    places=5,
                    msg=f"{codes[i]} {key}",
                )

    def test_missing_gap_source_is_noop(self) -> None:
        from core.research.tau_panel import enrich_day_matrix_cross_section

        names = ["ret_open_to_tau"]
        x = np.asarray([[1.0], [2.0], [3.0]], dtype=np.float64)
        out_names, out_x, added = enrich_day_matrix_cross_section(
            ["000001", "000002", "000003"], names, x, clock="09:35"
        )
        self.assertEqual(added, [])
        self.assertEqual(out_names, names)
        self.assertEqual(out_x.shape, x.shape)

    def test_vectorized_l2_enriches_cs_into_aligned(self) -> None:
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        day = "2024-06-03"
        codes = ["000001", "000002", "000003"]

        def _slice(family, codes_in, dates, progress_cb=None, cancel_cb=None):
            out = {}
            for i, code in enumerate(codes_in):
                if str(family).startswith("tc"):
                    feats = {
                        "gap_pct": float(1.0 + i),
                        "ret_open_to_tau": float(0.5 * i - 0.2),
                        "z_τh_gap_pct": float(1.0 + i),
                        "z_τh_ret_open_to_tau": float(0.5 * i - 0.2),
                    }
                elif family == "oo":
                    feats = {"gap_pct": float(1.0 + i)}
                else:
                    feats = {"ret_oc": 0.1}
                names = list(feats)
                out[code] = (
                    names,
                    {
                        str(d)[:10]: np.asarray(
                            [float(feats[n]) for n in names], dtype=np.float64
                        )
                        for d in dates
                    },
                )
            return out

        captured = {}

        def _ensure(*_a, **kw):
            aligned = kw.get("aligned")
            if aligned is not None:
                captured["names"] = list(aligned[1])
            return mock.Mock(names=list(aligned[1]) if aligned else ["x"])

        with mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items", side_effect=_ensure
        ), mock.patch(
            "core.research.materialize.cube_batch._keep_cube", return_value=True
        ):
            stats = build_day_cubes_from_lake(
                codes,
                [day],
                family_oo="oo",
                family_tc="tc_0935",
                family_co="co",
                clock="09:35",
                universe_codes=codes,
                min_items=2,
                require_tc_day=True,
                swallow_ensure_errors=True,
            )
        self.assertEqual(stats["cubes_built"], 1)
        got = set(captured.get("names") or [])
        for key in (
            "z_τh_sector_gap_breadth",
            "z_τh_theme_day",
            "z_τh_gap_vs_sector",
            "z_τh_sector_ret_to_tau",
            "z_τh_ret_vs_sector",
            "z_τc_sector_gap_breadth",
            "z_co_sector_gap_breadth",
        ):
            self.assertIn(key, got, msg=key)

    def test_open_clock_enriches_tc_and_co_cs(self) -> None:
        """open Cube：τc/co 入模截面进 names（含 z_co_*）。"""
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        day = "2024-06-03"
        codes = ["000001", "000002", "000003"]

        def _slice(family, codes_in, dates, progress_cb=None, cancel_cb=None):
            out = {}
            for i, code in enumerate(codes_in):
                feats = {"gap_pct": float(2.0 + i), "z_co_gap_pct": float(2.0 + i)}
                names = list(feats)
                out[code] = (
                    names,
                    {
                        str(d)[:10]: np.asarray(
                            [float(feats[n]) for n in names], dtype=np.float64
                        )
                        for d in dates
                    },
                )
            return out

        captured = {}

        def _ensure(*_a, **kw):
            aligned = kw.get("aligned")
            if aligned is not None:
                captured["names"] = list(aligned[1])
            return mock.Mock(names=list(aligned[1]) if aligned else ["x"])

        with mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items", side_effect=_ensure
        ), mock.patch(
            "core.research.materialize.cube_batch._keep_cube", return_value=True
        ):
            stats = build_day_cubes_from_lake(
                codes,
                [day],
                family_oo="oo",
                family_tc="tc",
                family_co="co",
                clock="open",
                universe_codes=codes,
                min_items=2,
                require_oo_item=True,
            )
        self.assertEqual(stats["cubes_built"], 1)
        got = set(captured.get("names") or [])
        for key in (
            "z_τc_sector_gap_breadth",
            "z_τc_theme_day",
            "z_τc_gap_vs_sector",
            "z_co_sector_gap_breadth",
            "z_co_theme_day",
            "z_co_gap_vs_sector",
        ):
            self.assertIn(key, got, msg=key)
        # open 无 ret_open_to_tau 源 → 不硬塞板块收益列
        self.assertNotIn("z_τc_sector_ret_to_tau", got)

    def test_enrich_mirrors_z_co_open_keys(self) -> None:
        from core.research.tau_panel import enrich_day_matrix_cross_section

        codes = ["000001", "000002", "000003"]
        names = ["gap_pct"]
        x = np.asarray([[3.0], [1.0], [0.5]], dtype=np.float64)
        out_names, _out_x, added = enrich_day_matrix_cross_section(
            codes, names, x, clock="open"
        )
        self.assertIn("sector_gap_breadth", added)
        for key in (
            "z_co_sector_gap_breadth",
            "z_co_theme_day",
            "z_co_gap_vs_sector",
            "z_τc_sector_gap_breadth",
        ):
            self.assertIn(key, out_names, msg=key)
        self.assertNotIn("z_co_sector_ret_to_tau", out_names)

    def test_enrich_bad_shape_and_trigger_are_safe(self) -> None:
        from core.research.tau_panel import enrich_day_matrix_cross_section

        names1, _x1, added1 = enrich_day_matrix_cross_section(
            ["000001"], ["gap_pct"], np.asarray([[1.0]]), clock="open"
        )
        self.assertEqual(added1, [])
        self.assertEqual(names1, ["gap_pct"])

        _names2, out_x, added2 = enrich_day_matrix_cross_section(
            ["000001", "000002"],
            ["gap_pct"],
            np.asarray([[1.0], [2.0]]),
            gap_trigger_pct=float("nan"),
        )
        self.assertIn("sector_gap_breadth", added2)
        self.assertEqual(out_x.shape[0], 2)

        # 行列错配 → 原样
        _names3, x3, added3 = enrich_day_matrix_cross_section(
            ["000001", "000002"],
            ["gap_pct", "extra"],
            np.asarray([[1.0], [2.0]]),
        )
        self.assertEqual(added3, [])
        self.assertEqual(x3.shape, (2, 1))

    def test_cube_z_alias_lookup_uses_bare_name(self) -> None:
        """拟合要 z_τh_*、Cube 只有裸名时仍能命中 μ/σ。"""
        from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix

        class _Cube:
            names = ["gap_pct", "sector_gap_breadth"]
            z_mean = np.array([1.0, 0.5])
            z_std = np.array([1.0, 2.0])

        x = np.array([[3.0, 2.5], [5.0, 4.5]], dtype=np.float64)
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
                ["z_τh_gap_pct", "z_τh_sector_gap_breadth"],
                ["2026-01-02", "2026-01-02"],
            )
        self.assertTrue(meta.get("ok"), meta)
        self.assertIsNotNone(z)
        # (3-1)/1=2；(2.5-0.5)/2=1
        self.assertAlmostEqual(float(z[0, 0]), 2.0, places=5)
        self.assertAlmostEqual(float(z[0, 1]), 1.0, places=5)
        self.assertEqual(meta.get("matrix_fallback_cols") or [], [])


class CubeBatchFillTests(unittest.TestCase):
    def test_chunk_opens_each_family_once(self) -> None:
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        seen = []

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            seen.append((str(family), tuple(dates)))
            rows = {}
            for code in codes:
                rows[code] = (
                    ["x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
            return rows

        days = ["2024-01-02", "2024-01-03", "2024-01-04"]
        with mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["x"]),
        ) as ensure:
            stats = build_day_cubes_from_lake(
                ["000001", "000002"],
                days,
                family_oo="oo",
                family_tc="tc",
                family_co="co",
                clock="open",
                universe_codes=["000001", "000002"],
                min_items=2,
                require_oo_item=True,
                chunk_days=80,
            )
        self.assertEqual(stats["cubes_built"], 3)
        self.assertEqual([fam for fam, _dates in seen], ["oo", "tc", "co"])
        self.assertEqual(seen[0][1], tuple(days))
        self.assertEqual(ensure.call_count, 3)
        self.assertEqual(
            list(ensure.call_args.kwargs["universe_codes"]),
            ["000001", "000002"],
        )

    def test_chunk_split_does_not_reread_earlier_days(self) -> None:
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        seen = []

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            if family == "oo":
                seen.append(tuple(dates))
            return {
                code: (
                    ["x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
                for code in codes
            }

        with mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["x"]),
        ):
            build_day_cubes_from_lake(
                ["000001", "000002"],
                ["2024-01-02", "2024-01-03", "2024-01-04"],
                family_oo="oo",
                family_tc="tc",
                family_co="co",
                clock="open",
                universe_codes=["000001", "000002"],
                min_items=2,
                require_oo_item=True,
                chunk_days=2,
            )
        self.assertEqual(
            seen,
            [("2024-01-02", "2024-01-03"), ("2024-01-04",)],
        )

    def test_cancel_during_slice_does_not_ensure(self) -> None:
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        calls = {"n": 0}

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            calls["n"] += 1
            if calls["n"] >= 2:
                return None
            return {
                code: (
                    ["x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
                for code in codes
            }

        with mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
        ) as ensure:
            stats = build_day_cubes_from_lake(
                ["000001", "000002"],
                ["2024-01-02"],
                family_oo="oo",
                family_tc="tc",
                family_co="co",
                clock="open",
                universe_codes=["000001", "000002"],
                min_items=2,
                require_oo_item=True,
            )
        ensure.assert_not_called()
        self.assertEqual(stats["cubes_built"], 0)
        self.assertEqual(stats["days_skipped"], 0)
        self.assertEqual(calls["n"], 2)

    def test_ensure_error_skips_that_day_only(self) -> None:
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            return {
                code: (
                    ["x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
                for code in codes
            }

        def _ensure(items, date=None, **_kwargs):
            if str(date)[:10] == "2024-01-02":
                raise RuntimeError("disk full")
            return mock.Mock(names=["x"])

        with mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items", side_effect=_ensure
        ):
            stats = build_day_cubes_from_lake(
                ["000001", "000002"],
                ["2024-01-02", "2024-01-03"],
                family_oo="oo",
                family_tc="tc",
                family_co="co",
                clock="open",
                universe_codes=["000001", "000002"],
                min_items=2,
                require_oo_item=True,
                swallow_ensure_errors=True,
            )
        self.assertEqual(stats["cubes_built"], 1)
        self.assertEqual(stats["days_skipped"], 1)

    def test_bad_panel_still_writes_the_other_codes(self) -> None:
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            rows = {
                code: (
                    ["x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
                for code in codes
            }
            rows["000002"] = (7, {"2024-01-02": np.asarray([1.0])})
            return rows

        with mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["x"]),
        ) as ensure:
            stats = build_day_cubes_from_lake(
                ["000001", "000002", "000003"],
                ["2024-01-02"],
                family_oo="oo",
                family_tc="tc",
                family_co="co",
                clock="09:35",
                universe_codes=["000001", "000002", "000003"],
                min_items=2,
                require_tc_day=True,
                swallow_ensure_errors=True,
            )
        self.assertEqual(stats["cubes_built"], 1)
        self.assertEqual(ensure.call_count, 1)
        kept = ensure.call_args.kwargs["aligned"][0]
        self.assertEqual(kept, ["000001", "000003"])

    def test_align_error_skips_only_that_day(self) -> None:
        from core.research.materialize.cube_batch import (
            align_day_matrix,
            build_day_cubes_from_lake,
        )

        real = align_day_matrix

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            return {
                code: (
                    ["x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
                for code in codes
            }

        seen = {"n": 0}

        def _wrap(*args, **kwargs):
            seen["n"] += 1
            if seen["n"] == 1:
                raise RuntimeError("bad matrix")
            return real(*args, **kwargs)

        with mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.materialize.cube_batch.align_day_matrix", side_effect=_wrap
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["x"]),
        ) as ensure:
            stats = build_day_cubes_from_lake(
                ["000001", "000002"],
                ["2024-01-02", "2024-01-03"],
                family_oo="oo",
                family_tc="tc",
                family_co="co",
                clock="09:35",
                universe_codes=["000001", "000002"],
                min_items=2,
                require_tc_day=True,
                swallow_ensure_errors=True,
            )
        self.assertEqual(stats["days_skipped"], 1)
        self.assertEqual(stats["cubes_built"], 1)
        self.assertEqual(ensure.call_count, 1)
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        seen = []

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            seen.append(str(family))
            return {
                code: (
                    ["x"],
                    {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
                )
                for code in codes
            }

        shared = {
            code: (
                ["x"],
                {"2024-01-02": np.asarray([1.0], dtype=np.float64)},
            )
            for code in ("000001", "000002")
        }
        with mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["x"]),
        ) as ensure:
            stats = build_day_cubes_from_lake(
                ["000001", "000002"],
                ["2024-01-02"],
                family_oo="oo",
                family_tc="tc_0935",
                family_co="co",
                clock="09:35",
                universe_codes=["000001", "000002"],
                min_items=2,
                require_tc_day=True,
                shared_oo=shared,
                shared_co=shared,
            )
        self.assertEqual(seen, ["tc_0935"])
        self.assertEqual(stats["cubes_built"], 1)
        ensure.assert_called_once()

    def test_align_day_matches_stamped_items(self) -> None:
        from core.research.batch_score import features_by_code_from_items
        from core.research.day_cube import build_aligned_cube, build_day_cube
        from core.research.lake_replay import assemble_item_from_lake_rows
        from core.research.materialize.cube_batch import _stack_days, align_day_matrix

        day = "2024-06-03"
        codes = ["000002", "000001", "000003", "000004"]
        universe = ["000009", "000001", "000002", "000003", "000004"]
        oo = {
            "000002": {"gap_pct": 1.0, "x": 5.0},
            "000001": {"gap_pct": 3.0},
            "000004": {"x": 4.0},
        }
        tc = {
            "000002": {"gap_pct": 8.0, "raw_ath_x": 1.5},
            "000001": {"gap_pct": 9.0},
        }
        co = {
            "000002": {"ret_oc": 0.1},
            "000001": {"ret_oc": 0.2},
            "000003": {"ret_oc": 0.9},
            "000004": {"ret_oc": 0.4},
        }

        def _sliced(rows: dict) -> dict:
            out = {}
            for code, feats in rows.items():
                names = list(feats)
                out[code] = (
                    names,
                    {day: np.asarray([float(feats[n]) for n in names], dtype=np.float64)},
                )
            return out

        oo_names, oo_x = _stack_days(_sliced(oo), codes, [day])
        tc_names, tc_x = _stack_days(_sliced(tc), codes, [day])
        co_names, co_x = _stack_days(_sliced(co), codes, [day])
        # 000001 的 raw_ath_x 是 NaN，不应写成有限值，列仍因 000002 保留。
        raw_j = tc_names.index("raw_ath_x")
        tc_x[codes.index("000001"), 0, raw_j] = np.nan
        aligned = align_day_matrix(
            codes,
            oo_names=oo_names,
            oo_block=oo_x[:, 0, :],
            tc_names=tc_names,
            tc_block=tc_x[:, 0, :],
            co_names=co_names,
            co_block=co_x[:, 0, :],
            require_tc_day=True,
        )
        self.assertIsNotNone(aligned)
        assert aligned is not None
        kept, names, matrix = aligned
        cube_new = build_aligned_cube(
            day,
            kept,
            matrix,
            names,
            clock="09:35",
            universe_codes=universe,
        )
        items = []
        for code in codes:
            o = oo.get(code)
            t = dict(tc.get(code) or {})
            if code == "000001":
                t.pop("raw_ath_x", None)
            c = co.get(code)
            if not o and not t:
                continue
            it = assemble_item_from_lake_rows(
                code, day, oo=o, tc=t or None, co=c
            )
            if it is not None:
                items.append(it)
        by_code = features_by_code_from_items(items)
        cube_old = build_day_cube(
            day, by_code, clock="09:35", universe_codes=universe
        )
        self.assertIsNotNone(cube_new)
        self.assertIsNotNone(cube_old)
        assert cube_new is not None and cube_old is not None
        self.assertEqual(cube_new.codes, cube_old.codes)
        self.assertEqual(list(cube_new.names), list(cube_old.names))
        self.assertNotIn("000003", cube_new.codes)
        np.testing.assert_allclose(cube_new.X_raw, cube_old.X_raw, equal_nan=True)
        np.testing.assert_allclose(cube_new.z_mean, cube_old.z_mean, equal_nan=True)
        np.testing.assert_allclose(cube_new.z_std, cube_old.z_std, equal_nan=True)
        self.assertEqual(cube_new.universe_hash, cube_old.universe_hash)
        self.assertEqual(cube_new.clock, cube_old.clock)

class MinuteL2ShareTests(unittest.TestCase):
    def _rows(self, codes, dates):
        return {
            code: (
                ["raw_ath_x"],
                {str(d)[:10]: np.asarray([1.0], dtype=np.float64) for d in dates},
            )
            for code in codes
        }

    def test_two_clocks_slice_oo_co_once(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        seen = []

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            seen.append(str(family))
            return self._rows(codes, dates)

        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            return_value=True,
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["raw_ath_x"]),
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35", "10:30"],
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(seen.count("oo"), 1)
        self.assertEqual(seen.count("co"), 1)
        self.assertEqual(seen.count("tc_0935"), 1)
        self.assertEqual(seen.count("tc_1030"), 1)
        self.assertEqual(ensure.call_count, 2)
        flags = [call.kwargs.get("replace") for call in ensure.call_args_list]
        self.assertEqual(flags, [False, False])

    def test_long_window_slices_each_family_once(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        seen = []

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            seen.append((str(family), len(list(dates or []))))
            return self._rows(codes, dates)

        dates = [f"2024-01-{day:02d}" for day in range(1, 25)]
        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            return_value=True,
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["raw_ath_x"]),
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=dates,
                clocks=["09:35", "10:30"],
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(seen.count(("oo", 24)), 1)
        self.assertEqual(seen.count(("co", 24)), 1)
        self.assertEqual(seen.count(("tc_0935", 24)), 1)
        self.assertEqual(seen.count(("tc_1030", 24)), 1)
        self.assertEqual(len(seen), 4)
        self.assertEqual(ensure.call_count, 48)

    def test_ready_clocks_do_not_open_l1(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        def _slice(*_a, **_k):
            raise AssertionError("should not slice")

        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=object()
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35", "10:30"],
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["by_clock"]["09:35"]["cubes_hit"], 1)
        self.assertEqual(out["by_clock"]["10:30"]["cubes_hit"], 1)
        ensure.assert_not_called()

    def test_only_short_clock_is_replaced(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        class _Cube:
            names = ["gap_pct"]

        def _load(_day, _codes, clock=None, **_k):
            if str(clock) == "10:30":
                return _Cube()
            return None

        def _cover(cube, **_kwargs):
            return "raw_ath_x" in set(getattr(cube, "names", []) or [])

        seen = []

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            seen.append(str(family))
            return self._rows(codes, dates)

        def _ensure(*_a, **kwargs):
            return type("C", (), {"names": ["raw_ath_x", "a", "b"]})()

        with mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            side_effect=_cover,
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items", side_effect=_ensure
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35", "10:30"],
                require_horizon_names=True,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(seen.count("oo"), 1)
        self.assertEqual(seen.count("co"), 1)
        self.assertEqual(seen.count("tc_0935"), 1)
        self.assertEqual(seen.count("tc_1030"), 1)
        flags = {
            call.kwargs.get("clock"): bool(call.kwargs.get("replace"))
            for call in ensure.call_args_list
        }
        self.assertEqual(flags, {"09:35": False, "10:30": True})
        self.assertEqual(out["by_clock"]["09:35"]["cubes_hit"], 0)
        self.assertEqual(out["by_clock"]["10:30"]["cubes_hit"], 0)

    def test_oo_slice_cancel_does_not_ensure(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            if str(family) == "oo":
                return None
            return self._rows(codes, dates)

        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35", "10:30"],
            )
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "cancelled")
        self.assertEqual(out["by_clock"]["09:35"]["error"], "cancelled")
        self.assertEqual(out["by_clock"]["10:30"]["error"], "cancelled")
        ensure.assert_not_called()

    def test_one_clock_failure_does_not_stop_the_other(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        calls = []

        def _build(_codes, days, **kwargs):
            clock = str(kwargs.get("clock"))
            calls.append(clock)
            if clock == "09:35" and calls.count("09:35") == 1:
                raise RuntimeError("disk")
            day_list = list(days)
            return {
                "cubes_built": len(day_list),
                "days_skipped": 0,
                "slim_reject": 0,
            }

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            return self._rows(codes, dates)

        dates = [f"2024-01-{day:02d}" for day in range(1, 22)]
        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.materialize.cube_batch.build_day_cubes_from_lake",
            side_effect=_build,
        ):
            out = materialize_l2(
                ["000001", "000002"],
                dates=dates,
                clocks=["09:35", "10:30"],
            )
        self.assertFalse(out.get("ok"))
        self.assertEqual(out["by_clock"]["09:35"]["error"], "disk")
        self.assertTrue(out["by_clock"]["10:30"]["ok"], out["by_clock"]["10:30"])
        self.assertEqual(calls.count("10:30"), 1)
        self.assertEqual(calls.count("09:35"), 1)

    def test_oo_slice_error_continues_tc_only(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            if str(family) == "oo":
                raise RuntimeError("oo io")
            return self._rows(codes, dates)

        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            return_value=True,
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["raw_ath_x"]),
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35", "10:30"],
            )
        self.assertNotEqual(out.get("error"), "oo io")
        self.assertNotEqual(out["by_clock"]["09:35"].get("error"), "oo io")
        self.assertNotEqual(out["by_clock"]["10:30"].get("error"), "oo io")
        self.assertTrue(ensure.called)
        # require_oo_item=False path
        for call in ensure.call_args_list:
            kwargs = call.kwargs if call.kwargs else {}
            # ensure_day_cube_for_items may be called via cube_batch; clock must be minute
            clock = kwargs.get("clock")
            if clock is not None:
                self.assertIn(str(clock), ("09:35", "10:30"))

    def test_tc_slice_error_leaves_the_other_clock_running(self) -> None:
        from core.research.materialize.l2_jobs import materialize_l2

        def _slice(family, codes, dates, progress_cb=None, cancel_cb=None):
            if str(family) == "tc_0935":
                raise RuntimeError("tc io")
            return self._rows(codes, dates)

        with mock.patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            return_value=True,
        ), mock.patch(
            "core.research.feature_lake.slice_stock_panels", side_effect=_slice
        ), mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=mock.Mock(names=["raw_ath_x"]),
        ) as ensure:
            out = materialize_l2(
                ["000001", "000002"],
                dates=["2026-10-16"],
                clocks=["09:35", "10:30"],
            )
        self.assertFalse(out.get("ok"))
        self.assertEqual(out["by_clock"]["09:35"]["error"], "tc io")
        self.assertEqual(out["by_clock"]["09:35"]["days_skipped"], 1)
        self.assertTrue(out["by_clock"]["10:30"]["ok"], out["by_clock"]["10:30"])
        self.assertGreaterEqual(int(out["by_clock"]["10:30"]["cubes_built"]), 1)
        self.assertEqual(ensure.call_args.kwargs.get("clock"), "10:30")

class TauGridPanelTests(unittest.TestCase):
    def test_one_pass_writes_each_clock_family(self) -> None:
        from core.research.panel_matrix import collect_tau_grid_panels

        calls = []
        written = []

        def _panel(_bars, _minute, **kwargs):
            calls.append(list(kwargs.get("tau_grid") or []))
            xs, ys, dates, metas = [], [], [], []
            for clock in kwargs.get("tau_grid") or []:
                for i in range(4):
                    xs.append({"gap_pct": float(i + 1)})
                    ys.append(0.01 * (i + 1))
                    dates.append(f"2024-02-{10 + i:02d}")
                    metas.append({"tau": clock})
            return xs, ys, dates, metas

        def _sync(family, code, **kwargs):
            written.append((str(family), str(code), kwargs["X"].shape[0]))
            return True

        bars = [
            {
                "date": f"2024-01-{day:02d}",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "volume": 1000.0,
            }
            for day in range(2, 20)
        ]
        items = [
            {"code": code, "bars": list(bars), "minute_bars": [{"date": "2024-01-02"}]}
            for code in ("000001", "000002")
        ]
        with mock.patch(
            "core.research.lake_bridge.load_training_matrix", return_value=None
        ), mock.patch(
            "core.research.tau_panel.collect_tau_intraday_panel", side_effect=_panel
        ), mock.patch(
            "core.research.lake_bridge.sync_panel_to_lake", side_effect=_sync
        ):
            out = collect_tau_grid_panels(
                items,
                ["gap_pct"],
                ["09:35", "10:30"],
                workers=1,
                include_alpha_tc=False,
                include_alpha_th=False,
            )
        self.assertEqual(calls, [["09:35", "10:30"], ["09:35", "10:30"]])
        self.assertEqual(
            sorted(written),
            [
                ("tc_0935", "000001", 4),
                ("tc_0935", "000002", 4),
                ("tc_1030", "000001", 4),
                ("tc_1030", "000002", 4),
            ],
        )
        self.assertTrue(out["by_clock"]["09:35"]["ok"])
        self.assertTrue(out["by_clock"]["10:30"]["ok"])
        self.assertFalse(out["cancelled"])

    def test_l1_hit_skips_that_clock(self) -> None:
        from core.research.panel_matrix import collect_tau_grid_panels

        calls = []

        def _load(family, _code, _fp, names=None):
            cols = list(names or ["x"])
            if str(family).endswith("0935"):
                return (
                    np.ones((4, len(cols)), dtype=np.float64),
                    np.ones(4, dtype=np.float64),
                    cols,
                    ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
                    "l1",
                )
            return None

        def _panel(_bars, _minute, **kwargs):
            calls.append(list(kwargs.get("tau_grid") or []))
            return [], [], [], []

        bars = [
            {
                "date": f"2024-01-{day:02d}",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "volume": 1000.0,
            }
            for day in range(2, 20)
        ]
        items = [
            {"code": "000001", "bars": bars, "minute_bars": []},
            {"code": "000002", "bars": list(bars), "minute_bars": []},
        ]
        with mock.patch(
            "core.research.lake_bridge.load_training_matrix", side_effect=_load
        ), mock.patch(
            "core.research.tau_panel.collect_tau_intraday_panel", side_effect=_panel
        ), mock.patch(
            "core.research.lake_bridge.sync_panel_to_lake", return_value=True
        ):
            out = collect_tau_grid_panels(
                items,
                ["gap_pct"],
                ["09:35", "10:30"],
                workers=1,
                include_alpha_tc=False,
                include_alpha_th=False,
            )
        self.assertEqual(calls, [["10:30"], ["10:30"]])
        self.assertEqual(out["by_clock"]["09:35"]["n_stocks"], 2)
        self.assertGreaterEqual(out["by_clock"]["09:35"]["n_rows"], 4)

    def test_grid_matches_single_clock_matrix(self) -> None:
        from core.research.materialize.horizon_names import horizon_l1_feature_names
        from core.research.panel_matrix import (
            _tau_stock_job,
            _tc_matrix_seed_names,
            collect_tau_grid_panels,
        )
        from tests.conftest import _dated_bars_and_minutes

        bars, minutes = _dated_bars_and_minutes(80)
        names = _tc_matrix_seed_names(
            horizon_l1_feature_names(),
            include_alpha_tc=True,
            include_alpha_th=True,
        )
        written: dict = {}

        def _sync(family, _code, **kwargs):
            written[str(family)] = (
                np.array(kwargs["X"], copy=True),
                list(kwargs["dates"]),
                list(kwargs["names"]),
                str(kwargs["fingerprint"]),
            )
            return True

        item = {
            "code": "000001",
            "bars": bars,
            "minute_bars": minutes,
        }
        with mock.patch(
            "core.research.lake_bridge.load_training_matrix", return_value=None
        ), mock.patch(
            "core.research.lake_bridge.sync_panel_to_lake", side_effect=_sync
        ):
            collect_tau_grid_panels(
                [item],
                horizon_l1_feature_names(),
                ["09:35", "10:00"],
                workers=1,
                include_alpha_tc=True,
                include_alpha_th=True,
            )
            grid = dict(written)
            written.clear()
            for clock in ("09:35", "10:00"):
                fresh = {
                    "code": "000001",
                    "bars": [dict(b) for b in bars],
                    "minute_bars": [dict(b) for b in minutes],
                }
                _tau_stock_job(
                    fresh,
                    names,
                    (),
                    use_minute=True,
                    tau_hm=clock,
                    grid=None,
                    min_history=12,
                    include_alpha_tc=True,
                    include_alpha_th=True,
                )
            single = dict(written)
        self.assertEqual(set(grid), set(single))
        for family in grid:
            gx, gd, gn, gfp = grid[family]
            sx, sd, sn, sfp = single[family]
            self.assertEqual(gd, sd)
            self.assertEqual(gn, sn)
            self.assertEqual(gfp, sfp)
            self.assertTrue(np.allclose(gx, sx, equal_nan=True))

    def test_fingerprint_hashes_minute_bars_once(self) -> None:
        from core.research.panel_fingerprint import bars_fingerprint
        from core.research.panel_matrix import _tau_grid_clock_fp, _tau_grid_stock_job

        bars = [
            {
                "date": f"2024-01-{day:02d}",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "volume": 1000.0,
            }
            for day in range(2, 20)
        ]
        minutes = [{"date": "2024-01-02", "datetime": "2024-01-02 09:35:00", "close": 10.0}]
        calls = {"n": 0}
        real = bars_fingerprint

        def _wrap(*args, **kwargs):
            calls["n"] += 1
            return real(*args, **kwargs)

        with mock.patch(
            "core.research.panel_fingerprint.bars_fingerprint", side_effect=_wrap
        ):
            shared = bars_fingerprint(bars, minute_bars=minutes)
            one = _tau_grid_clock_fp(
                bars,
                minutes,
                "09:35",
                min_history=12,
                include_alpha_tc=True,
                include_alpha_th=True,
                bars_fp=shared,
            )
            two = _tau_grid_clock_fp(
                bars,
                minutes,
                "10:00",
                min_history=12,
                include_alpha_tc=True,
                include_alpha_th=True,
                bars_fp=shared,
            )
            lone = _tau_grid_clock_fp(
                bars,
                minutes,
                "09:35",
                min_history=12,
                include_alpha_tc=True,
                include_alpha_th=True,
            )
        self.assertEqual(one, lone)
        self.assertNotEqual(one, two)
        self.assertEqual(calls["n"], 1)

        hit = (
            np.ones((4, 1), dtype=np.float64),
            np.ones(4, dtype=np.float64),
            ["gap_pct"],
            ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            "l1",
        )
        calls["n"] = 0
        item = {"code": "000001", "bars": bars, "minute_bars": list(minutes)}
        with mock.patch(
            "core.research.panel_fingerprint.bars_fingerprint", side_effect=_wrap
        ), mock.patch(
            "core.research.lake_bridge.load_training_matrix", return_value=hit
        ):
            _tau_grid_stock_job(
                item,
                ["gap_pct"],
                ["09:35", "10:00", "10:30"],
                min_history=12,
                include_alpha_tc=False,
                include_alpha_th=False,
            )
        self.assertEqual(calls["n"], 1)
        self.assertNotIn("minute_bars", item)

    def test_batch_atc_matches_each_clock(self) -> None:
        from core.research.tau_panel import (
            _index_minute_bars_by_date,
            _minute_clocks_atc_items,
            _scan_one_minute_clock_atc,
        )
        from tests.conftest import _dated_bars_and_minutes

        bars, minutes = _dated_bars_and_minutes(80)
        by_day = _index_minute_bars_by_date(minutes)
        clocks = ["09:35", "10:00", "10:30"]
        batch = _minute_clocks_atc_items(bars, by_day, hist=60, clocks=clocks)
        self.assertEqual(set(batch), set(clocks))
        for clock in clocks:
            single = _scan_one_minute_clock_atc(bars, by_day, hist=60, clock=clock)
            self.assertIsNotNone(single)
            got = {key: col for key, col in batch[clock]}
            expect = {key: col for key, col in single}
            self.assertEqual(set(got), set(expect))
            for key, col in expect.items():
                np.testing.assert_allclose(got[key], col, equal_nan=True)

    def test_process_pool_finishes_four_stocks(self) -> None:
        from core.research.panel_matrix import collect_tau_grid_panels

        bars = [
            {
                "date": f"2024-04-{day:02d}",
                "open": 10.0,
                "high": 10.4,
                "low": 9.8,
                "close": 10.2,
                "volume": 1000.0,
            }
            for day in range(1, 22)
        ]
        minutes = []
        for bar in bars:
            day = bar["date"]
            for hm, px in (("09:35", 10.05), ("10:00", 10.15)):
                minutes.append(
                    {
                        "date": day,
                        "datetime": f"{day} {hm}:00",
                        "open": px,
                        "high": px + 0.1,
                        "low": px - 0.1,
                        "close": px,
                        "volume": 300.0,
                    }
                )
        items = [
            {
                "code": f"60000{i}",
                "bars": [dict(b) for b in bars],
                "minute_bars": [dict(b) for b in minutes],
            }
            for i in range(1, 5)
        ]
        out = collect_tau_grid_panels(
            items,
            ["gap_pct"],
            ["09:35", "10:00"],
            workers=4,
            include_alpha_tc=False,
            include_alpha_th=False,
            min_history=12,
        )
        self.assertFalse(out["cancelled"])
        self.assertGreaterEqual(out["by_clock"]["09:35"]["n_stocks"], 4)
        self.assertGreaterEqual(out["by_clock"]["10:00"]["n_stocks"], 4)
        self.assertTrue(all("minute_bars" not in it for it in items))

if __name__ == "__main__":
    unittest.main()
