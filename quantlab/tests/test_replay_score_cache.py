"""调仓回测开盘日打分磁盘缓存。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bars(n: int = 40, start: float = 10.0, seed: int = 0):
    px = start
    out = []
    for i in range(n):
        o = px * (1.0 + (0.01 if (i + seed) % 5 == 0 else 0.0))
        c = o * (1.0 + ((i % 7) - 3) * 0.002)
        out.append(
            {
                "date": f"2024-01-{(i % 28) + 1:02d}",
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
            }
        )
        px = c
    # 去重日期：用递增日
    out = []
    px = start
    for i in range(n):
        day = f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        o = px * (1.0 + (0.01 if (i + seed) % 5 == 0 else 0.0))
        c = o * (1.0 + ((i % 7) - 3) * 0.002)
        out.append(
            {
                "date": day,
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
            }
        )
        px = c
    return out


class TestReplayScoreCache(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_dir = os.environ.get("QUANTLAB_REPLAY_SCORE_CACHE_DIR")
        self._prev_en = os.environ.get("QUANTLAB_REPLAY_SCORE_CACHE")
        self._prev_keep = os.environ.get("QUANTLAB_REPLAY_SCORE_CACHE_KEEP")
        os.environ["QUANTLAB_REPLAY_SCORE_CACHE_DIR"] = self._tmpdir.name
        os.environ["QUANTLAB_REPLAY_SCORE_CACHE"] = "1"
        os.environ.pop("QUANTLAB_REPLAY_SCORE_CACHE_KEEP", None)

    def tearDown(self) -> None:
        if self._prev_dir is None:
            os.environ.pop("QUANTLAB_REPLAY_SCORE_CACHE_DIR", None)
        else:
            os.environ["QUANTLAB_REPLAY_SCORE_CACHE_DIR"] = self._prev_dir
        if self._prev_en is None:
            os.environ.pop("QUANTLAB_REPLAY_SCORE_CACHE", None)
        else:
            os.environ["QUANTLAB_REPLAY_SCORE_CACHE"] = self._prev_en
        if self._prev_keep is None:
            os.environ.pop("QUANTLAB_REPLAY_SCORE_CACHE_KEEP", None)
        else:
            os.environ["QUANTLAB_REPLAY_SCORE_CACHE_KEEP"] = self._prev_keep
        self._tmpdir.cleanup()

    def test_save_load_roundtrip_and_slim(self) -> None:
        from core.backtest.replay_score_cache import load_day, save_day, slim_day_items

        rows = [
            {
                "stock_code": "600000",
                "y_oo": 0.01,
                "y_τc": 0.02,
                "y_co": 0.0,
                "y_oo_rank": 1,
                "features": {"raw_aoo_x": 1.0},
                "formula_terms_tau": [{"k": "a"}],
            }
        ]
        slim = slim_day_items(rows)
        self.assertEqual(slim[0]["stock_code"], "600000")
        self.assertNotIn("features", slim[0])
        self.assertEqual(slim[0]["formula_terms_tau"], [{"k": "a"}])
        self.assertEqual(slim[0]["y_oo"], 0.01)

        self.assertTrue(save_day("abc123runkey", "2024-02-01", rows))
        hit = load_day("abc123runkey", "2024-02-01")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit[0]["stock_code"], "600000")
        self.assertEqual(hit[0]["y_oo"], 0.01)
        self.assertNotIn("features", hit[0])
        self.assertIsNone(load_day("abc123runkey", "2024-02-02"))
        self.assertEqual(hit[0].get("formula_terms_tau"), [{"k": "a"}])

    def test_slim_keeps_capped_formula_terms_for_tips(self) -> None:
        from core.backtest.replay_score_cache import slim_day_items

        terms = [{"key": f"f{i}", "beta": 0.1, "z": 0.2, "contrib": float(i)} for i in range(40)]
        terms.append({"key": "gap_pct", "beta": 0.1, "z": 0.0, "contrib": 0.01})
        rows = [
            {
                "stock_code": "600000",
                "features": {"raw": 1},
                "factor_coefficients": {"f39": 0.2},
                "formula_terms": {
                    "intercept": 0.01,
                    "total": 1.2,
                    "score_model_role": "research",
                    "terms": list(terms),
                },
                "formula_terms_tau": {
                    "intercept": 0.02,
                    "total": 0.8,
                    "model_role": "research",
                    "terms": list(terms),
                },
            }
        ]
        slim = slim_day_items(rows)[0]
        self.assertNotIn("features", slim)
        self.assertNotIn("factor_coefficients", slim)
        oo = slim["formula_terms"]
        self.assertEqual(len(oo["terms"]), 24)
        self.assertEqual(oo["terms"][0]["key"], "f39")
        self.assertEqual(oo["score_model_role"], "research")
        tau_keys = {t["key"] for t in slim["formula_terms_tau"]["terms"]}
        self.assertIn("gap_pct", tau_keys)
        self.assertLessEqual(len(tau_keys), 24 + 1)

    def test_bars_fingerprint_changes(self) -> None:
        from core.backtest.replay_score_cache import bars_universe_fingerprint

        a = {"600000": _bars(30, seed=1), "000001": _bars(30, seed=2)}
        b = {
            "600000": list(a["600000"]),
            "000001": list(a["000001"])
            + [
                {
                    "date": "2024-03-01",
                    "open": 11.0,
                    "high": 11.2,
                    "low": 10.8,
                    "close": 11.1,
                    "volume": 2_000_000,
                }
            ],
        }
        self.assertNotEqual(bars_universe_fingerprint(a), bars_universe_fingerprint(b))

    def test_run_key_stable_and_sensitive(self) -> None:
        from core.backtest.replay_score_cache import make_run_key

        k1 = make_run_key(
            codes_fp="c1",
            model_fp="m1",
            score_model_role="research",
            score_backend="ridge",
            max_window=40,
            horizon_days=1,
            required_factor_keys=["a", "b"],
        )
        k2 = make_run_key(
            codes_fp="c1",
            model_fp="m1",
            score_model_role="research",
            score_backend="ridge",
            max_window=40,
            horizon_days=1,
            required_factor_keys=["b", "a"],
        )
        self.assertEqual(k1, k2)
        k3 = make_run_key(
            codes_fp="c1",
            model_fp="m1",
            score_model_role="live",
            score_backend="ridge",
            max_window=40,
            horizon_days=1,
            required_factor_keys=["a", "b"],
        )
        self.assertNotEqual(k1, k3)
        k4 = make_run_key(
            codes_fp="c1",
            model_fp="m1",
            score_model_role="research",
            score_backend="ridge",
            max_window=40,
            horizon_days=1,
            required_factor_keys=["a", "b"],
            score_codes_fp="tierA",
        )
        self.assertNotEqual(k1, k4)

    def test_build_run_key_ignores_bars_tip(self) -> None:
        from core.backtest.replay_score_cache import build_run_key_for_bars

        bars = {"600000": _bars(20, seed=1), "000001": _bars(20, seed=2)}
        tipped = {
            "600000": list(bars["600000"])
            + [
                {
                    "date": "2024-03-01",
                    "open": 11.0,
                    "high": 11.2,
                    "low": 10.8,
                    "close": 11.1,
                    "volume": 2_000_000,
                }
            ],
            "000001": list(bars["000001"]),
        }
        with patch(
            "core.backtest.replay_score_cache.model_suite_fingerprint",
            return_value="fixed_model",
        ):
            a = build_run_key_for_bars(
                bars,
                score_model_role="research",
                score_backend="ridge",
                max_window=40,
                horizon_days=1,
            )
            b = build_run_key_for_bars(
                tipped,
                score_model_role="research",
                score_backend="ridge",
                max_window=40,
                horizon_days=1,
            )
            c = build_run_key_for_bars(
                bars,
                score_model_role="research",
                score_backend="ridge",
                max_window=41,
                horizon_days=1,
            )
        self.assertEqual(a["run_key"], b["run_key"])
        self.assertEqual(a["codes_fp"], b["codes_fp"])
        self.assertNotEqual(a["bars_fp"], b["bars_fp"])
        self.assertEqual(a["model_fp"], "fixed_model")
        self.assertNotEqual(a["run_key"], c["run_key"])

    def test_day_inputs_fp_gate(self) -> None:
        from core.backtest.replay_score_cache import load_day, save_day

        rows = [{"stock_code": "600000", "y_oo": 0.01}]
        self.assertTrue(
            save_day("rkday", "2024-02-01", rows, inputs_fp="fp_a")
        )
        self.assertIsNotNone(load_day("rkday", "2024-02-01", inputs_fp="fp_a"))
        self.assertIsNone(load_day("rkday", "2024-02-01", inputs_fp="fp_b"))
        # 未传 inputs_fp：兼容读取（不校验）
        self.assertIsNotNone(load_day("rkday", "2024-02-01"))

    def test_disabled_skips_io(self) -> None:
        from core.backtest.replay_score_cache import load_day, save_day

        os.environ["QUANTLAB_REPLAY_SCORE_CACHE"] = "0"
        self.assertFalse(save_day("rk", "2024-01-02", [{"stock_code": "1", "y_oo": 1}]))
        self.assertIsNone(load_day("rk", "2024-01-02"))
        os.environ["QUANTLAB_REPLAY_SCORE_CACHE"] = "1"

    def test_write_run_meta_prunes_old_runs_and_schemas(self) -> None:
        import time

        from core.backtest import replay_score_cache as rsc

        root = self._tmpdir.name
        v1 = os.path.join(root, "v1", "oldschema")
        os.makedirs(v1, exist_ok=True)
        open(os.path.join(v1, "x.txt"), "w").write("1")

        # 三个同 schema run；keep=2 时应删掉最旧的
        paths = []
        for i, key in enumerate(("aaa111", "bbb222", "ccc333")):
            p = os.path.join(root, f"v{rsc.REPLAY_SCORE_CACHE_SCHEMA}", key)
            os.makedirs(p, exist_ok=True)
            open(os.path.join(p, "meta.json"), "w").write("{}")
            os.utime(p, (time.time() - 100 + i, time.time() - 100 + i))
            paths.append(p)

        os.environ["QUANTLAB_REPLAY_SCORE_CACHE_KEEP"] = "2"
        rsc.write_run_meta("ccc333", {"n_codes": 1})
        self.assertFalse(os.path.isdir(os.path.join(root, "v1")))
        self.assertFalse(os.path.isdir(paths[0]))  # aaa111 oldest
        self.assertTrue(os.path.isdir(paths[1]))
        self.assertTrue(os.path.isdir(paths[2]))
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    root, f"v{rsc.REPLAY_SCORE_CACHE_SCHEMA}", "ccc333", "meta.json"
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
