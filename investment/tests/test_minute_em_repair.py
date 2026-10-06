"""东财补缺：更短的一天不能覆盖更齐的本地日。"""

import unittest

from quant.research.bars_integrity import FIVE_MINUTE_SLOTS
from quant.research.minute_em_repair import should_replace_day


def _from(start: str) -> list:
    return [hm for hm in FIVE_MINUTE_SLOTS if hm >= start]


class MinuteEmRepairTests(unittest.TestCase):
    def test_complete_day_fills_a_hole(self):
        self.assertTrue(should_replace_day([], FIVE_MINUTE_SLOTS))
        self.assertTrue(should_replace_day(_from("09:50"), FIVE_MINUTE_SLOTS))

    def test_shorter_day_does_not_replace(self):
        self.assertFalse(should_replace_day(FIVE_MINUTE_SLOTS, _from("09:50")))
        self.assertFalse(should_replace_day(FIVE_MINUTE_SLOTS, []))
        self.assertFalse(should_replace_day(_from("09:50"), _from("09:50")))

    def test_same_kind_needs_more_bars(self):
        self.assertTrue(should_replace_day(_from("09:55"), _from("09:50")))
        self.assertFalse(should_replace_day(_from("09:50"), _from("10:00")))


if __name__ == "__main__":
    unittest.main()
