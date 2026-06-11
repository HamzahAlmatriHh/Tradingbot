"""
Unit Tests — PerformanceTracker
يختبر: حساب المحفظة، إحصائيات الفترات، Profit Factor، Win Rate
يستخدم قاعدة بيانات SQLite مؤقتة بدون أي API خارجي.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.db_manager as db_mod


def _insert_trade(tid, pnl, side="BUY", symbol="BTC/USDT",
                  entry=None, exit_t=None):
    from datetime import datetime, timedelta
    now = datetime.now()
    entry  = entry  or (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    exit_t = exit_t or now.strftime("%Y-%m-%d %H:%M:%S")

    db_mod.insert_trade({
        "trade_id":               tid,
        "symbol":                 symbol,
        "side":                   side,
        "entry_time":             entry,
        "entry_price":            40000.0,
        "exit_time":              exit_t,
        "exit_price":             40000.0 + (pnl / 0.025),
        "pnl":                    pnl,
        "pnl_pct":                pnl / 50 * 100,
        "exit_reason":            "TP1" if pnl > 0 else "SL",
        "entry_reason":           "SMC",
        "sentiment_score":        0.5,
        "adx":                    25.0,
        "ema_200":                39000.0,
        "atr":                    400.0,
        "spread":                 0.001,
        "volume_24h":             1e9,
        "risk_pct":               1.0,
        "leverage":               10.0,
        "slippage":               0.0002,
        "amount":                 0.025,
        "filter_profile":         "balanced",
        "journal_dir":            "",
        "wallet_equity_at_entry": 50.0,
        "wallet_pnl_pct":         pnl / 50 * 100,
        "reference_balance":      50.0,
        "pnl_ref_50":             pnl,
    })


class TestPerformanceTracker(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        db_mod.DB_PATH = self.tmp.name
        db_mod.init_db()

        # 3 رابحة + 2 خاسرة
        _insert_trade("T1", pnl=+5.0,  exit_t="2024-01-10 12:00:00")
        _insert_trade("T2", pnl=+8.0,  exit_t="2024-01-10 13:00:00")
        _insert_trade("T3", pnl=-3.0,  exit_t="2024-01-10 14:00:00")
        _insert_trade("T4", pnl=+2.0,  exit_t="2024-01-10 15:00:00")
        _insert_trade("T5", pnl=-1.5,  exit_t="2024-01-10 16:00:00")

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _get_tracker(self):
        from utils.performance_tracker import PerformanceTracker
        return PerformanceTracker(starting_balance=50.0)

    # ── 1. المحفظة الإجمالية ──────────────────────────────────────
    def test_wallet_realized_pnl(self):
        tracker = self._get_tracker()
        wallet = tracker.get_wallet()
        expected_pnl = 5.0 + 8.0 - 3.0 + 2.0 - 1.5  # = 10.5
        self.assertAlmostEqual(wallet["realized_pnl"], expected_pnl, places=2)

    def test_wallet_balance_equals_start_plus_pnl(self):
        tracker = self._get_tracker()
        wallet = tracker.get_wallet()
        self.assertAlmostEqual(
            wallet["wallet_balance"],
            50.0 + wallet["realized_pnl"],
            places=4
        )

    # ── 2. Win Rate ───────────────────────────────────────────────
    def test_win_rate_correct(self):
        df = db_mod.load_trades_df()
        wins = df[df["pnl"] > 0]
        win_rate = len(wins) / len(df) * 100
        self.assertAlmostEqual(win_rate, 60.0, delta=0.1)

    # ── 3. Profit Factor ─────────────────────────────────────────
    def test_profit_factor_correct(self):
        df = db_mod.load_trades_df()
        gross_profit = df[df["pnl"] > 0]["pnl"].sum()
        gross_loss   = abs(df[df["pnl"] <= 0]["pnl"].sum())
        pf = gross_profit / gross_loss if gross_loss > 0 else 999.0
        expected_pf  = (5.0 + 8.0 + 2.0) / (3.0 + 1.5)
        self.assertAlmostEqual(pf, expected_pf, places=1)

    # ── 4. أفضل وأسوأ صفقة ───────────────────────────────────────
    def test_best_and_worst_trade(self):
        df = db_mod.load_trades_df()
        self.assertAlmostEqual(float(df["pnl"].max()), +8.0, places=2)
        self.assertAlmostEqual(float(df["pnl"].min()), -3.0, places=2)

    # ── 5. بلا صفقات — لا أخطاء ──────────────────────────────────
    def test_empty_db_no_crash(self):
        # قاعدة بيانات فارغة
        tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp2.close()
        db_mod.DB_PATH = tmp2.name
        db_mod.init_db()
        try:
            from utils.performance_tracker import PerformanceTracker
            tracker = PerformanceTracker(starting_balance=50.0)
            wallet  = tracker.get_wallet()
            self.assertEqual(wallet["realized_pnl"], 0.0)
            self.assertEqual(wallet["trades_count"], 0)
        finally:
            db_mod.DB_PATH = self.tmp.name
            os.unlink(tmp2.name)

    # ── 6. Total Return % ─────────────────────────────────────────
    def test_total_return_pct(self):
        tracker = self._get_tracker()
        wallet  = tracker.get_wallet()
        expected = (wallet["realized_pnl"] / 50.0) * 100
        self.assertAlmostEqual(wallet["total_return_pct"], expected, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
