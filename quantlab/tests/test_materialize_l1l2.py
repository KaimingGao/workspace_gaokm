"""独立 L1/L2 物化：catalog / flags / orchestrate 护栏。"""

from __future__ import annotations

import os
import unittest
from unittest import mock


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
        self.assertIn("flags", doc)
        self.assertIn("fit_use_day_cube", doc["flags"])
        self.assertIn("tau_grid", doc)
        self.assertEqual(doc["tau_grid"].get("option"), "do_l1_tau_grid")
        self.assertTrue(doc["tau_grid"].get("requires_minute"))

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
                clocks=["open"],  # schema 默认；纯 τ 轨应剥掉 open
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
                "core.research.day_cube.load_day_cube", return_value=None
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
            "core.research.materialize.cube_z._universe_codes",
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
            "core.research.materialize.cube_z._universe_codes",
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
            "core.research.materialize.cube_z._universe_codes",
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
            "core.research.materialize.cube_z._universe_codes",
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


if __name__ == "__main__":
    unittest.main()
