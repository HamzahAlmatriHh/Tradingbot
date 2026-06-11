"""
Unit Tests — candle_sync
يختبر: حساب وقت الانتظار حتى إغلاق الشمعة التالية
لا يتصل بأي خدمة خارجية.
"""
import os
import sys
import time
import math
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.candle_sync import seconds_to_next_candle


class TestCandleSync(unittest.TestCase):

    def test_returns_positive_value(self):
        """يجب أن يُعيد قيمة موجبة دائماً"""
        wait = seconds_to_next_candle(interval_seconds=900, buffer_seconds=10)
        self.assertGreater(wait, 0, "وقت الانتظار يجب أن يكون موجباً")

    def test_minimum_30_seconds(self):
        """يجب ألا يقل الانتظار عن 30 ثانية"""
        wait = seconds_to_next_candle(interval_seconds=900, buffer_seconds=0)
        self.assertGreaterEqual(wait, 30, "الحد الأدنى 30 ثانية")

    def test_wait_less_than_interval_plus_buffer(self):
        """يجب ألا يتجاوز الانتظار مجموع الـ interval + buffer"""
        interval = 900
        buffer   = 10
        wait = seconds_to_next_candle(interval_seconds=interval, buffer_seconds=buffer)
        self.assertLessEqual(
            wait, interval + buffer,
            "الانتظار لا يجب أن يتجاوز الـ interval + buffer"
        )

    def test_aligns_to_candle_boundary(self):
        """
        يتحقق أن الوقت الناتج (now + wait - buffer) يُشير لحد شمعة صحيح.
        حد الشمعة هو مضاعف صحيح لـ interval_seconds.
        """
        interval = 900
        buffer   = 10
        now      = time.time()
        wait     = seconds_to_next_candle(interval_seconds=interval, buffer_seconds=buffer)
        target   = now + wait - buffer
        # target يجب أن يكون قريباً من مضاعف 900 (ضمن ثانيتين)
        remainder = target % interval
        self.assertAlmostEqual(remainder, 0, delta=2,
                               msg="الهدف يجب أن يكون مضاعفاً لـ interval")

    def test_different_intervals(self):
        """يعمل مع فترات مختلفة: 5m, 15m, 30m, 1h"""
        for minutes in [5, 15, 30, 60]:
            interval = minutes * 60
            wait = seconds_to_next_candle(interval_seconds=interval, buffer_seconds=10)
            self.assertGreater(wait, 0)
            self.assertLessEqual(wait, interval + 10)

    def test_buffer_zero(self):
        """يعمل حتى مع buffer = 0"""
        wait = seconds_to_next_candle(interval_seconds=900, buffer_seconds=0)
        self.assertGreaterEqual(wait, 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
