"""日线 / 5 分钟仓完整度格子。"""

import unittest

from quant.research.bars_integrity import (
    FIVE_MINUTE_SLOTS,
    build_daily_grid,
    build_minute_grid,
    classify_minute_day,
    minute_day_detail,
)


class BarsIntegrityTests(unittest.TestCase):
    def test_five_minute_day_has_48_slots(self):
        self.assertEqual(len(FIVE_MINUTE_SLOTS), 48)
        self.assertEqual(FIVE_MINUTE_SLOTS[0], "09:35")
        self.assertEqual(FIVE_MINUTE_SLOTS[23], "11:30")
        self.assertEqual(FIVE_MINUTE_SLOTS[24], "13:05")
        self.assertEqual(FIVE_MINUTE_SLOTS[-1], "15:00")

    def test_head_gap_is_not_complete(self):
        # 立讯 09-09：从 09:50 到 15:00，尾齐头缺
        hms = [hm for hm in FIVE_MINUTE_SLOTS if hm >= "09:50"]
        self.assertEqual(classify_minute_day(hms, live=False), "head")
        detail = minute_day_detail(hms, live=False)
        self.assertEqual(detail["kind"], "head")
        self.assertEqual(detail["n"], 45)
        self.assertFalse(detail["morning"][0]["ok"])
        self.assertTrue(detail["afternoon"][-1]["ok"])

    def test_live_session_is_not_a_gap(self):
        self.assertEqual(classify_minute_day([], live=True), "live")

    def test_grids_sink_complete_rows(self):
        dates = ["2026-09-08", "2026-09-09"]
        daily = build_daily_grid(
            codes=["000001", "002475"],
            names={"002475": "立讯精密", "000001": "平安银行"},
            dates=dates,
            present={"000001": set(dates), "002475": {"2026-09-08"}},
        )
        self.assertEqual(daily["rows"][0]["stock_code"], "002475")
        self.assertEqual(daily["miss_names"], 1)
        self.assertEqual(daily["rows"][0]["cells"], ["ok", "miss"])

        minute = build_minute_grid(
            codes=["601088", "002475"],
            names={"002475": "立讯精密", "601088": "中国神华"},
            dates=dates,
            hms_by_code_date={
                "601088": {d: set(FIVE_MINUTE_SLOTS) for d in dates},
                "002475": {
                    "2026-09-08": set(FIVE_MINUTE_SLOTS),
                    "2026-09-09": {hm for hm in FIVE_MINUTE_SLOTS if hm >= "09:50"},
                },
            },
        )
        self.assertEqual(minute["rows"][0]["stock_code"], "002475")
        self.assertEqual(minute["head_names"], 1)
        self.assertEqual(minute["rows"][0]["cells"][1], "head")
        self.assertEqual(minute["rows"][1]["cells"], ["ok", "ok"])

    def test_window_default_is_sixty_trading_days(self):
        from quant.research.bars_integrity import INTEGRITY_DAYS, clamp_integrity_days

        self.assertEqual(INTEGRITY_DAYS, 60)
        self.assertEqual(clamp_integrity_days(60), 60)
        self.assertEqual(clamp_integrity_days(200), 90)


if __name__ == "__main__":
    unittest.main()
