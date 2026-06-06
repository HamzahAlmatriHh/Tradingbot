import os
import html
import pandas as pd
from datetime import datetime, timedelta
from core.config import Config
from core.logger import logger


class PerformanceTracker:
    """
    مركز حساب أداء البوت:
    - محفظة Testnet وهمية تراكمية.
    - تقارير Daily / Weekly / Monthly / Yearly.
    - حساب PNL و ROE و Win Rate و Profit Factor.
    """

    REQUIRED_COLUMNS = [
        "trade_id",
        "symbol",
        "side",
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "pnl",
        "pnl_pct",
        "exit_reason",
        "entry_reason",
        "amount",
        "risk_pct",
        "leverage",
        "slippage",
    ]

    def __init__(self, state_manager=None, csv_path=None, starting_balance=None):
        self.state_manager = state_manager
        self.csv_path = csv_path or getattr(Config, "TESTNET_TRADES_LOG", "testnet_trades_log.csv")
        self.starting_balance = float(
            starting_balance
            if starting_balance is not None
            else getattr(Config, "TESTNET_SIMULATED_BALANCE", 50.0)
        )

    # ==========================================================
    # Data Loading
    # ==========================================================
    def _empty_df(self):
        return pd.DataFrame(columns=self.REQUIRED_COLUMNS)

    def load_trades(self) -> pd.DataFrame:
        """
        يقرأ سجل الصفقات وينظفه.
        CSV هو مصدر الحقيقة لتجنب تكرار احتساب PNL بعد إعادة التشغيل.
        """
        if not os.path.exists(self.csv_path):
            return self._empty_df()

        try:
            df = pd.read_csv(self.csv_path)

            if df.empty:
                return self._empty_df()

            # ضمان وجود الأعمدة حتى لو السجل قديم.
            for col in self.REQUIRED_COLUMNS:
                if col not in df.columns:
                    df[col] = None

            # تحويل التواريخ.
            df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")
            df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce")

            # تحويل الأرقام.
            numeric_cols = [
                "entry_price",
                "exit_price",
                "pnl",
                "pnl_pct",
                "amount",
                "risk_pct",
                "leverage",
                "slippage",
            ]
            for col in numeric_cols:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

            # تنظيف الصفقات غير المكتملة.
            df = df[df["exit_time"].notna()].copy()

            # منع احتساب نفس الصفقة مرتين إذا تكرر تسجيلها.
            dedup_cols = [
                "trade_id",
                "symbol",
                "side",
                "entry_time",
                "entry_price",
                "exit_time",
                "exit_price",
                "pnl",
            ]
            dedup_cols = [c for c in dedup_cols if c in df.columns]
            df = df.drop_duplicates(subset=dedup_cols, keep="last")

            # تجهيز بيانات إضافية.
            df["symbol"] = df["symbol"].fillna("").astype(str)
            df["side"] = df["side"].fillna("").astype(str).str.upper()

            df["entry_notional"] = (df["entry_price"] * df["amount"]).abs()
            df["leverage"] = df["leverage"].replace(0, 1).clip(lower=1)

            # الهامش المستخدم = قيمة الصفقة / الرافعة.
            df["margin_used"] = df["entry_notional"] / df["leverage"]

            # ROE = الربح / الهامش المستخدم.
            df["roe_pct"] = df.apply(
                lambda r: (r["pnl"] / r["margin_used"] * 100)
                if r["margin_used"] > 0 else 0.0,
                axis=1
            )

            # نسبة الحركة على قيمة الصفقة نفسها.
            df["price_pnl_pct"] = df.apply(
                lambda r: (r["pnl"] / r["entry_notional"] * 100)
                if r["entry_notional"] > 0 else 0.0,
                axis=1
            )

            return df.sort_values("exit_time")

        except Exception as e:
            logger.error(f"[PerformanceTracker] فشل قراءة سجل الصفقات: {e}")
            return self._empty_df()

    # ==========================================================
    # Wallet
    # ==========================================================
    def get_reserved_margin_from_state(self) -> float:
        """
        يقدّر الهامش المحجوز للصفقات المفتوحة من entry_metadata.
        مهم حتى لا يعتبر البوت كل الرصيد الوهمي متاحًا أثناء وجود صفقة مفتوحة.
        """
        if not self.state_manager:
            return 0.0

        state = self.state_manager.get_state()
        metadata = state.get("entry_metadata", {}) or {}

        reserved = 0.0
        for _, meta in metadata.items():
            try:
                entry_price = float(meta.get("entry_price", 0.0))
                amount = float(meta.get("amount", 0.0))
                leverage = float(meta.get("leverage", 1.0) or 1.0)
                leverage = max(leverage, 1.0)

                reserved += abs(entry_price * amount) / leverage
            except Exception:
                continue

        return reserved

    def get_wallet(self, unrealized_pnl: float = 0.0) -> dict:
        """
        يحسب المحفظة الوهمية التراكمية:
        wallet_balance = starting + realized pnl
        equity = wallet_balance + unrealized pnl
        available = wallet_balance - reserved margin
        """
        df = self.load_trades()

        realized_pnl = float(df["pnl"].sum()) if not df.empty else 0.0
        wallet_balance = self.starting_balance + realized_pnl

        reserved_margin = self.get_reserved_margin_from_state()
        available = max(wallet_balance - reserved_margin, 0.0)

        equity = wallet_balance + float(unrealized_pnl or 0.0)
        total_return_pct = (
            (wallet_balance - self.starting_balance) / self.starting_balance * 100
            if self.starting_balance > 0 else 0.0
        )

        wallet = {
            "starting_balance": round(self.starting_balance, 6),
            "realized_pnl": round(realized_pnl, 6),
            "wallet_balance": round(wallet_balance, 6),
            "unrealized_pnl": round(float(unrealized_pnl or 0.0), 6),
            "equity": round(equity, 6),
            "reserved_margin": round(reserved_margin, 6),
            "available": round(available, 6),
            "total_return_pct": round(total_return_pct, 4),
            "trades_count": int(len(df)),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        if self.state_manager:
            self.state_manager.set("simulated_wallet", wallet)

        return wallet

    # ==========================================================
    # Period Stats
    # ==========================================================
    def _period_bounds(self, period: str, now=None):
        now = pd.Timestamp(now or datetime.now())

        if period == "daily":
            start = now.normalize()
            end = start + pd.Timedelta(days=1)

        elif period == "weekly":
            start = (now - pd.Timedelta(days=now.weekday())).normalize()
            end = start + pd.Timedelta(days=7)

        elif period == "monthly":
            start = now.replace(day=1).normalize()
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)

        elif period == "yearly":
            start = pd.Timestamp(year=now.year, month=1, day=1)
            end = pd.Timestamp(year=now.year + 1, month=1, day=1)

        else:
            raise ValueError("period must be daily, weekly, monthly, or yearly")

        return start, end

    def period_stats(self, period: str) -> dict:
        df = self.load_trades()
        start, end = self._period_bounds(period)

        if df.empty:
            period_df = df
            prior_pnl = 0.0
        else:
            prior_df = df[df["exit_time"] < start]
            prior_pnl = float(prior_df["pnl"].sum()) if not prior_df.empty else 0.0
            period_df = df[(df["exit_time"] >= start) & (df["exit_time"] < end)].copy()

        start_equity = self.starting_balance + prior_pnl
        period_pnl = float(period_df["pnl"].sum()) if not period_df.empty else 0.0
        end_equity = start_equity + period_pnl

        trades_count = int(len(period_df))
        wins = period_df[period_df["pnl"] > 0] if trades_count else period_df
        losses = period_df[period_df["pnl"] <= 0] if trades_count else period_df

        gross_profit = float(wins["pnl"].sum()) if trades_count else 0.0
        gross_loss = abs(float(losses["pnl"].sum())) if trades_count else 0.0

        win_rate = (len(wins) / trades_count * 100) if trades_count else 0.0
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else (999.0 if gross_profit > 0 else 0.0)
        )

        period_return_pct = (
            period_pnl / start_equity * 100
            if start_equity > 0 else 0.0
        )

        avg_roe = float(period_df["roe_pct"].mean()) if trades_count else 0.0
        best_trade = float(period_df["pnl"].max()) if trades_count else 0.0
        worst_trade = float(period_df["pnl"].min()) if trades_count else 0.0
        avg_slippage = float(period_df["slippage"].mean() * 100) if trades_count else 0.0

        return {
            "period": period,
            "start": start,
            "end": end,
            "trades": period_df,
            "trades_count": trades_count,
            "start_equity": start_equity,
            "end_equity": end_equity,
            "period_pnl": period_pnl,
            "period_return_pct": period_return_pct,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "avg_roe": avg_roe,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "avg_slippage": avg_slippage,
        }

    # ==========================================================
    # Telegram Formatting
    # ==========================================================
    def format_report(self, period: str, recent_limit=None) -> str:
        stats = self.period_stats(period)
        wallet = self.get_wallet()

        recent_limit = recent_limit or getattr(Config, "REPORT_RECENT_TRADES_LIMIT", 10)
        period_df = stats["trades"].sort_values("exit_time", ascending=False)

        period_names = {
            "daily": "اليومي",
            "weekly": "الأسبوعي",
            "monthly": "الشهري",
            "yearly": "السنوي",
        }

        title = period_names.get(period, period)

        pnl_emoji = "🟢" if stats["period_pnl"] >= 0 else "🔴"
        pf_text = "∞" if stats["profit_factor"] >= 999 else f"{stats['profit_factor']:.2f}"

        msg = f"🏆 <b>تقرير الأداء {title}</b>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

        msg += f"🗓️ <b>الفترة:</b> <code>{stats['start'].strftime('%Y-%m-%d')}</code> → <code>{(stats['end'] - pd.Timedelta(seconds=1)).strftime('%Y-%m-%d')}</code>\n"
        msg += f"💼 <b>رأس البداية:</b> <code>{stats['start_equity']:.2f}</code> USDT\n"
        msg += f"💰 <b>رصيد النهاية:</b> <code>{stats['end_equity']:.2f}</code> USDT\n"
        msg += f"{pnl_emoji} <b>صافي الربح:</b> <code>{stats['period_pnl']:+.2f}</code> USDT\n"
        msg += f"📈 <b>عائد الفترة:</b> <code>{stats['period_return_pct']:+.2f}%</code>\n\n"

        msg += "📊 <b>إحصائيات التداول</b>\n"
        msg += f"• عدد الصفقات: <code>{stats['trades_count']}</code>\n"
        msg += f"• نسبة الفوز: <code>{stats['win_rate']:.2f}%</code>\n"
        msg += f"• Profit Factor: <code>{pf_text}</code>\n"
        msg += f"• متوسط ROE: <code>{stats['avg_roe']:+.2f}%</code>\n"
        msg += f"• أفضل صفقة: <code>{stats['best_trade']:+.2f}</code> USDT\n"
        msg += f"• أسوأ صفقة: <code>{stats['worst_trade']:+.2f}</code> USDT\n"
        msg += f"• متوسط الانزلاق: <code>{stats['avg_slippage']:.3f}%</code>\n\n"

        msg += "💳 <b>المحفظة الوهمية التراكمية</b>\n"
        msg += f"• رأس البداية: <code>{wallet['starting_balance']:.2f}</code> USDT\n"
        msg += f"• Realized PNL: <code>{wallet['realized_pnl']:+.2f}</code> USDT\n"
        msg += f"• الرصيد الحالي: <code>{wallet['wallet_balance']:.2f}</code> USDT\n"
        msg += f"• العائد الكلي: <code>{wallet['total_return_pct']:+.2f}%</code>\n\n"

        msg += f"🧾 <b>آخر {recent_limit} صفقات في الفترة</b>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"

        if period_df.empty:
            msg += "لا توجد صفقات مغلقة في هذه الفترة.\n"
            return msg

        recent = period_df.head(recent_limit)

        for _, r in recent.iterrows():
            symbol = html.escape(str(r.get("symbol", "")))
            side_raw = str(r.get("side", "")).upper()
            side = "LONG" if side_raw in ["BUY", "LONG"] else "SHORT"
            side_emoji = "🟢" if side == "LONG" else "🔴"
            result_emoji = "✅" if float(r["pnl"]) >= 0 else "❌"

            exit_time = r["exit_time"].strftime("%m-%d %H:%M") if pd.notna(r["exit_time"]) else "-"

            msg += (
                f"{result_emoji} <code>{exit_time}</code> | "
                f"{side_emoji} <b>{symbol}</b> {side} | "
                f"PNL <code>{float(r['pnl']):+.2f}</code> | "
                f"ROE <code>{float(r['roe_pct']):+.2f}%</code>\n"
            )

        return msg
