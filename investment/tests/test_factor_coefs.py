"""因子系数 ↔ |β| 派生展示权。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.factors.meta.coefs import (
    coefficients_from_return_model,
    display_weights_from_coefficients,
    display_weights_from_return_model,
    has_factor_coefficients,
)


class TestFactorCoefs(unittest.TestCase):
    def test_display_weights_from_abs_beta(self):
        w = display_weights_from_coefficients({"momentum": 0.3, "value": -0.1})
        self.assertIsNotNone(w)
        self.assertAlmostEqual(w["momentum"], 0.75)
        self.assertAlmostEqual(w["value"], 0.25)
        self.assertAlmostEqual(sum(w.values()), 1.0, places=5)

    def test_from_return_model(self):
        rm = {
            "coefficients": {"momentum": 0.2, "value": 0.2, "intercept": 1.0},
            "intercept": 1.0,
        }
        self.assertTrue(has_factor_coefficients(rm))
        coefs = coefficients_from_return_model(rm)
        self.assertNotIn("intercept", coefs)
        w = display_weights_from_return_model(rm)
        self.assertAlmostEqual(w["momentum"], 0.5)
        self.assertAlmostEqual(w["value"], 0.5)

    def test_empty(self):
        self.assertFalse(has_factor_coefficients(None))
        self.assertIsNone(display_weights_from_coefficients({}))


if __name__ == "__main__":
    unittest.main()
