"""沪深 300 / 1600 两套截面名单。"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.universe.feature_sets import (
    active_feature_codes,
    active_feature_set,
    canonical_pool1600,
    codes_for_feature_set,
    feature_set_scope,
    normalize_feature_set,
    watching_prefix,
)


class TestFeatureSets(unittest.TestCase):
    def test_prefix_keeps_csi300_then_fills(self) -> None:
        hs = [f"{i:06d}" for i in range(1, 301)]
        rest = [f"{i:06d}" for i in range(500, 520)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "csi300_constituents.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"codes": hs}, f)
            with patch("core.universe.feature_sets.cache_path", return_value=path), patch(
                "core.universe.feature_sets._cache_fresh", return_value=True
            ), patch(
                "core.research_universe.load_research_universe",
                return_value={"codes": ["000001", *rest]},
            ):
                self.assertEqual(normalize_feature_set("沪深300"), "hs300")
                self.assertEqual(codes_for_feature_set("hs300"), hs)
                full = canonical_pool1600()
                self.assertEqual(full[:300], hs)
                self.assertEqual(full[300], "000500")
                self.assertEqual(watching_prefix(300), hs)
                self.assertEqual(watching_prefix(305)[:300], hs)
                self.assertEqual(len(watching_prefix(305)), 305)

    def test_large_pool_is_the_current_watchlist(self) -> None:
        from core.universe.feature_sets import materialize_universes

        hs = [f"{i:06d}" for i in range(1, 51)]
        watch = hs + ["000888", "000889"]
        with patch("core.universe.feature_sets.load_csi300_codes", return_value=hs), patch(
            "core.universe.feature_sets.watching_codes", return_value=watch
        ):
            self.assertEqual(codes_for_feature_set("pool"), watch)
            self.assertEqual(codes_for_feature_set("大池"), watch)
            named = materialize_universes()
            self.assertEqual([name for name, _ in named], ["hs300", "pool"])
            self.assertEqual(named[1][1], watch)

    def test_scope_pins_hs300_without_touching_the_process_env(self) -> None:
        hs = [f"{i:06d}" for i in range(1, 51)]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QUANTLAB_FEATURE_SET", None)
            self.assertEqual(active_feature_set(), "")
            with patch("core.universe.feature_sets.codes_for_feature_set", return_value=hs):
                with feature_set_scope("hs300"):
                    self.assertEqual(active_feature_set(), "hs300")
                    self.assertEqual(active_feature_codes(), hs)
                self.assertEqual(active_feature_set(), "")
                self.assertIsNone(active_feature_codes())

    def test_missing_constituents_do_not_invent_a_pool(self) -> None:
        with patch("core.universe.feature_sets._read_cache", return_value=[]), patch(
            "core.universe.feature_sets._cache_fresh", return_value=False
        ):
            self.assertEqual(watching_prefix(300, fetch=False), [])
            self.assertEqual(codes_for_feature_set("hs300", fetch=False), [])


if __name__ == "__main__":
    unittest.main()
