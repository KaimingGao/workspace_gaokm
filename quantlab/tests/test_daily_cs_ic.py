"""日频截面 IC（对齐 Qlib）单测。"""

from __future__ import annotations

import unittest


class TestDailyCsIc(unittest.TestCase):
    def test_mean_daily_pearson_and_rank(self):
        from core.research.daily_cs_ic import summarize_daily_cs_ic

        # Day1: perfect rank; Day2: reverse rank → mean ~ 0
        preds = [3.0, 2.0, 1.0, 1.0, 2.0, 3.0]
        ys = [3.0, 2.0, 1.0, 3.0, 2.0, 1.0]
        metas = [
            {"date": "2026-09-01", "code": "a"},
            {"date": "2026-09-01", "code": "b"},
            {"date": "2026-09-01", "code": "c"},
            {"date": "2026-09-02", "code": "a"},
            {"date": "2026-09-02", "code": "b"},
            {"date": "2026-09-02", "code": "c"},
        ]
        # Need ≥3 days for agg; pad day3 with mid correlation
        preds += [1.0, 2.0, 3.0]
        ys += [1.0, 2.5, 2.0]
        metas += [
            {"date": "2026-09-03", "code": "a"},
            {"date": "2026-09-03", "code": "b"},
            {"date": "2026-09-03", "code": "c"},
        ]
        out = summarize_daily_cs_ic(preds, ys, metas, min_names=3)
        self.assertTrue(out["ok"])
        self.assertEqual(out["cs_align"], "qlib_sigana")
        self.assertEqual(out["cs_day_count"], 3)
        self.assertIsNotNone(out["cs_ic"])
        self.assertIsNotNone(out["cs_rank_ic"])
        # day1=+1, day2=-1 → pull mean toward 0; day3 weak positive
        self.assertLess(abs(float(out["cs_ic"])), 0.6)

    def test_tau_sections_dedupe_stock(self):
        from core.research.daily_cs_ic import summarize_daily_cs_ic

        preds = []
        ys = []
        metas = []
        for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
            for tau in ("09:30", "10:00"):
                for i, code in enumerate(("a", "b", "c", "d", "e")):
                    preds.append(float(i))
                    ys.append(float(i))
                    metas.append({"date": day, "tau": tau, "code": code})
        out = summarize_daily_cs_ic(preds, ys, metas, min_names=5)
        self.assertTrue(out["ok"])
        # 3 days × 2 tau = 6 sections
        self.assertEqual(out["cs_day_count"], 6)
        self.assertAlmostEqual(float(out["cs_ic"]), 1.0, places=3)
        self.assertAlmostEqual(float(out["cs_rank_ic"]), 1.0, places=3)

    def test_attach_marks_chrono(self):
        from core.research.daily_cs_ic import attach_daily_cs_ic

        oos = {"ic": 0.12}
        preds = [1.0, 2.0, 3.0, 1.0, 2.0, 3.0, 1.0, 2.0, 3.0]
        ys = [1.0, 2.0, 3.0, 1.0, 2.0, 3.0, 1.0, 2.0, 3.0]
        metas = []
        for d in ("2026-09-01", "2026-09-02", "2026-09-03"):
            for code in ("a", "b", "c"):
                metas.append({"date": d, "code": code})
        attach_daily_cs_ic(oos, preds, ys, metas, min_names=3)
        self.assertEqual(oos.get("ic_kind"), "chrono_pearson")
        self.assertFalse(oos.get("is_primary_ic"))
        self.assertEqual(oos.get("cs_ic_kind"), "cs_pearson")
        self.assertAlmostEqual(float(oos["cs_ic"]), 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
