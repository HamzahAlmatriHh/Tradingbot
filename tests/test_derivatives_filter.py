"""
Unit Tests — DerivativesRiskFilter
يختبر: أن الفلتر لا يعيد بيانات عشوائية، وأن هيكل الإخراج صحيح
يُحاكي استدعاءات API باستخدام unittest.mock
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDerivativesRiskFilter(unittest.TestCase):

    def _make_filter(self):
        from filters.derivatives_risk_filter import DerivativesRiskFilter
        return DerivativesRiskFilter(state_manager=None)

    # ── 1. هيكل النتيجة دائماً صحيح ──────────────────────────────
    @patch("filters.derivatives_risk_filter.requests.get")
    def test_result_structure(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": {"longShortRatio": "1.2", "longAccount": "0.55"}}
        )
        f = self._make_filter()
        result = f.evaluate_risk("BTCUSDT", "BUY", price=40000.0)
        self.assertIn("decision", result, "يجب وجود مفتاح decision")
        self.assertIn(result["decision"], ["ALLOW", "REJECT", "WARN"],
                      "decision يجب أن يكون ALLOW أو REJECT أو WARN")

    # ── 2. فشل API → ALLOW آمن ───────────────────────────────────
    @patch("filters.derivatives_risk_filter.requests.get", side_effect=Exception("timeout"))
    def test_api_failure_returns_allow(self, _):
        f = self._make_filter()
        result = f.evaluate_risk("BTCUSDT", "BUY", price=40000.0)
        self.assertEqual(result["decision"], "ALLOW",
                         "عند فشل API يجب أن يعود ALLOW وليس رفضاً عشوائياً")

    # ── 3. لا random في النتيجة ───────────────────────────────────
    @patch("filters.derivatives_risk_filter.requests.get", side_effect=Exception("timeout"))
    def test_no_random_on_failure(self, _):
        f = self._make_filter()
        results = {f.evaluate_risk("BTCUSDT", "BUY", price=40000.0)["decision"] for _ in range(20)}
        self.assertEqual(results, {"ALLOW"},
                         "يجب أن تكون جميع النتائج ALLOW عند فشل API — لا عشوائية")

    # ── 4. يعمل مع BUY وSELL ──────────────────────────────────────
    @patch("filters.derivatives_risk_filter.requests.get", side_effect=Exception("timeout"))
    def test_works_with_buy_and_sell(self, _):
        f = self._make_filter()
        for side in ["BUY", "SELL", "LONG", "SHORT"]:
            result = f.evaluate_risk("ETHUSDT", side, price=2000.0)
            self.assertIn("decision", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
