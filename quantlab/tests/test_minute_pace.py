"""新浪分钟开工闸：失败后拉长间隔，业务错误不拉长。"""

import unittest

from core.data.minute_pace import MinuteStartPace, minute_pace_should_stretch


class MinuteStartPaceTests(unittest.TestCase):
    def test_stretch_on_empty_timeout_and_disconnect(self):
        pace = MinuteStartPace(1.0)
        self.assertFalse(minute_pace_should_stretch("invalid bare code"))
        pace.note_remote_failure("invalid bare code")
        self.assertEqual(pace.pace_sec, 1.0)
        self.assertFalse(pace.stretched)

        pace.note_remote_failure("timeout (45s)")
        self.assertEqual(pace.pace_sec, 2.0)
        pace.note_remote_failure("empty")
        self.assertEqual(pace.pace_sec, 4.0)
        pace.note_remote_failure("Connection aborted")
        self.assertEqual(pace.pace_sec, 8.0)
        pace.note_remote_failure("RemoteDisconnected")
        self.assertEqual(pace.pace_sec, 8.0)
        self.assertTrue(pace.stretched)

    def test_zero_pace_does_not_sleep_or_stretch(self):
        pace = MinuteStartPace(0.0)
        pace.wait()
        pace.note_remote_failure("timeout")
        self.assertEqual(pace.pace_sec, 0.0)
        self.assertFalse(pace.stretched)


if __name__ == "__main__":
    unittest.main()
