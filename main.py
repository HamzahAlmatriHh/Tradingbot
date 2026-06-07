import time
import os
import csv
import threading
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from core.logger import logger
from core.exchange import ExchangeClient
from engines.news_engine import NewsEngine
from engines.ai_engine import AIEngine
from engines.ta_engine import TAEngine
from engines.social_engine import SocialEngine
from strategy.hybrid_logic import HybridStrategy
from strategy.risk_manager import RiskManager
from strategy.trailing_stop import TrailingStopManager
from strategy.alert_manager import TradeApproachAlertManager
from utils.telegram_bot import TelegramNotifier
from utils.state_manager import StateManager
from utils.performance_tracker import PerformanceTracker
from core.config import Config
from core.universe_filter import UniverseFilter
from filters.derivatives_risk_filter import DerivativesRiskFilter
from utils.api_status_monitor import APIStatusMonitor
from strategy.filter_profiles import get_filter_profile
from utils.trade_journal import TradeJournal

def maybe_send_periodic_reports(notifier, state_manager, client=None):
    """
    يرسل تقارير Daily / Weekly / Monthly / Yearly مرة واحدة فقط لكل فترة.
    تعتمد على testnet_trades_log.csv كمصدر حقيقة.
    """
    now = datetime.now()

    tracker = PerformanceTracker(state_manager)

    def get_marker_key(period):
        if period == "daily":
            return now.strftime("%Y-%m-%d")
        if period == "weekly":
            iso = now.isocalendar()
            return f"{iso.year}-W{iso.week}"
        if period == "monthly":
            return now.strftime("%Y-%m")
        if period == "yearly":
            return now.strftime("%Y")
        return now.strftime("%Y-%m-%d")

    def already_sent(period):
        markers = state_manager.get("report_markers", {})
        return markers.get(period) == get_marker_key(period)

    def mark_sent(period):
        markers = state_manager.get("report_markers", {})
        markers[period] = get_marker_key(period)
        state_manager.set("report_markers", markers)

    def send_report(period):
        try:
            text = tracker.format_report(period, client=client)
            ok = notifier.send_message(text)
            if ok:
                mark_sent(period)
                logger.info(f"تم إرسال التقرير {period} بنجاح.")
        except Exception as e:
            logger.error(f"فشل إرسال التقرير {period}: {e}")

    # يومي: آخر اليوم
    if now.hour == 23 and now.minute >= 55 and not already_sent("daily"):
        send_report("daily")

    # أسبوعي: نهاية الأحد
    if now.weekday() == 6 and now.hour == 23 and now.minute >= 55 and not already_sent("weekly"):
        send_report("weekly")

    # شهري: آخر يوم من الشهر
    tomorrow = now + timedelta(days=1)
    is_last_day_of_month = tomorrow.month != now.month
    if is_last_day_of_month and now.hour == 23 and now.minute >= 55 and not already_sent("monthly"):
        send_report("monthly")

    # سنوي: 31 ديسمبر
    if now.month == 12 and now.day == 31 and now.hour == 23 and now.minute >= 55 and not already_sent("yearly"):
        send_report("yearly")


def get_wallet_equity_snapshot(client, state_manager=None):
    try:
        if client is None: return 0.0, 0.0
        
        simulated_cap = getattr(Config, "TESTNET_SIMULATED_BALANCE", 0)
        if getattr(Config, "USE_TESTNET", False) and simulated_cap > 0 and state_manager is not None:
            bal = client.get_balance()
            info = bal.get("info", {}) if bal else {}
            total_unrealized = float(info.get("totalUnrealizedProfit", 0) or 0)
            
            tracker = PerformanceTracker(state_manager)
            sim_wallet = tracker.get_wallet(unrealized_pnl=total_unrealized)
            return float(sim_wallet["equity"]), float(sim_wallet["available"])

        bal = client.get_balance()
        info = bal.get("info", {}) if bal else {}

        equity = (
            info.get("totalMarginBalance")
            or info.get("totalWalletBalance")
            or bal.get("USDT", {}).get("total", 0)
        )

        available = bal.get("USDT", {}).get("free", 0) if bal else 0

        return float(equity or 0.0), float(available or 0.0)
    except Exception as e:
        logger.error(f"فشل جلب لقطة المحفظة: {e}")
        return 0.0, 0.0

def log_testnet_trade(symbol, entry_meta, exit_details):
    """
    تسجيل الصفقات الفردية لـ Testnet بالكامل في ملف CSV محلي
    """
    file_path = getattr(Config, "TESTNET_TRADES_LOG", "testnet_trades_log.csv")
    os.makedirs(os.path.dirname(os.path.abspath(file_path)) or ".", exist_ok=True)
    file_exists = os.path.exists(file_path)
    
    headers = [
        "trade_id", "symbol", "side", "entry_time", "entry_price", "exit_time", "exit_price",
        "pnl", "pnl_pct", "exit_reason", "entry_reason", "sentiment_score", "adx",
        "ema_200", "atr", "spread", "volume_24h", "risk_pct", "leverage", "slippage",
        "amount", "filter_profile", "journal_dir",
        "wallet_equity_at_entry", "wallet_pnl_pct", "reference_balance", "pnl_ref_50"
    ]
    
    exit_time = exit_details.get("exit_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    entry_time = entry_meta.get("entry_time", "")

    trade_id = entry_meta.get("trade_id") or (
        f"{symbol}|{entry_meta.get('side', '')}|{entry_time}|"
        f"{entry_meta.get('entry_price', 0.0)}|{exit_time}|{exit_details.get('exit_price', 0.0)}"
    )

    # منع تكرار نفس الصفقة في السجل إذا وصل إشعار الإغلاق مرتين
    if file_exists:
        try:
            old_df = pd.read_csv(file_path)
            if "trade_id" in old_df.columns and trade_id in set(old_df["trade_id"].astype(str)):
                logger.warning(f"⚠️ الصفقة {trade_id} مسجلة مسبقًا، سيتم تجاهل التكرار.")
                return True
        except Exception as e:
            logger.warning(f"تعذر فحص تكرار الصفقة في CSV: {e}")
    
    row = {
        "trade_id": trade_id,
        "symbol": symbol,
        "side": entry_meta.get("side", ""),
        "entry_time": entry_time,
        "entry_price": entry_meta.get("entry_price", 0.0),
        "exit_time": exit_time,
        "exit_price": exit_details.get("exit_price", 0.0),
        "pnl": exit_details.get("pnl", 0.0),
        "pnl_pct": exit_details.get("pnl_pct", 0.0),
        "exit_reason": exit_details.get("exit_reason", ""),
        "entry_reason": entry_meta.get("entry_reason", ""),
        "sentiment_score": entry_meta.get("sentiment_score", 0.0),
        "adx": entry_meta.get("adx", 0.0),
        "ema_200": entry_meta.get("ema_200", 0.0),
        "atr": entry_meta.get("atr", 0.0),
        "spread": entry_meta.get("spread", 0.0),
        "volume_24h": entry_meta.get("volume_24h", 0.0),
        "risk_pct": entry_meta.get("risk_pct", 0.0),
        "leverage": entry_meta.get("leverage", 1.0),
        "slippage": exit_details.get("slippage", 0.0),
        "amount": entry_meta.get("amount", 0.0),
        "filter_profile": entry_meta.get("filter_profile", ""),
        "journal_dir": entry_meta.get("journal_dir", ""),
        "wallet_equity_at_entry": entry_meta.get("wallet_equity_at_entry", 0.0),
        "wallet_pnl_pct": exit_details.get("wallet_pnl_pct", 0.0),
        "reference_balance": entry_meta.get("reference_balance", 50.0),
        "pnl_ref_50": exit_details.get("pnl_ref_50", 0.0),
    }
    
    try:
        with open(file_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
        logger.info(f"✅ تم تسجيل صفقة {symbol} في سجل الصفقات بنجاح.")
        return True
    except Exception as e:
        logger.error(f"❌ فشل تسجيل الصفقة في الملف المحلي: {e}")
        return False

def execute_and_protect_trade(symbol, final_decision, amount, current_price, trade_leverage, trade_risk_pct, atr_val, coin_data, sentiment, bars_df, ticker, ta_trend, client, risk_manager, trailing_manager, state_manager, notifier, active_symbols, scan_results, invalidation_level=None):
    """
    دالة موحدة لتنفيذ أمر الماركت، حساب الستوب/الهدف الديناميكي (Sniper)، وضع الأوامر، وتحديث الحالة المحفوظة.
    """
    filter_profile = get_filter_profile(state_manager)

    # حساب الستوب والهدف بناءً على السعر الحالي أولاً للتأكد من جودة الـ RR
    sl, tp = risk_manager.calculate_sl_tp(
        final_decision, 
        current_price, 
        support=coin_data.get('support'), 
        resistance=coin_data.get('resistance'),
        atr=atr_val,
        invalidation_level=invalidation_level,
        market_levels=coin_data.get('market_levels', {}),
        filter_profile=filter_profile,
    )
    
    if tp is None:
        logger.warning(f"[{symbol}] ❌ تم إحباط التنفيذ: الـ RR سيء أو لا يوجد هدف سيولة مقابل.")
        coin_data['decision'] = '🔴 REJECT'
        coin_data['reason'] = "RR ضعيف حسب SMC Targets"
        scan_results.append(coin_data)
        return False

    order = client.execute_trade(symbol, final_decision, amount, current_price=current_price, leverage=trade_leverage)
    if order:
        actual_entry = order.get('average') or order.get('price') or current_price
        if not actual_entry or actual_entry == 0:
            actual_entry = current_price
            
        # إعادة الضبط على السعر الفعلي بعد الانزلاق (Slippage)
        sl, tp = risk_manager.calculate_sl_tp(
            final_decision, 
            actual_entry, 
            support=coin_data.get('support'), 
            resistance=coin_data.get('resistance'),
            atr=atr_val,
            invalidation_level=invalidation_level,
            market_levels=coin_data.get('market_levels', {}),
            filter_profile=filter_profile,
        )
        if tp is None:
            # لو الانزلاق خرب الصفقة، نستخدم الهدف القديم مؤقتاً كخطة طوارئ
            sl, tp = risk_manager.calculate_sl_tp(
                final_decision, actual_entry, support=coin_data.get('support'), resistance=coin_data.get('resistance'), atr=atr_val, invalidation_level=invalidation_level, filter_profile=filter_profile
            )
        
        logger.info(f"!!! تم تنفيذ إشارة تداول !!! لـ {symbol}")
        logger.info(f"سعر التنفيذ الفعلي: {actual_entry:.4f} | الهدف: {tp:.4f} | الوقف: {sl:.4f}")
        
        sentiment_label = sentiment.get('label', 'neutral') if sentiment else 'neutral'
        notifier.send_trade_alert(symbol, final_decision, amount, actual_entry, sl, tp, sentiment_label)
        
        logger.info("جاري وضع أوامر الحماية...")

        partial_enabled = getattr(Config, "PARTIAL_TP_ENABLED", True)

        protection_meta = {}

        if partial_enabled:
            protection = client.place_partial_protection(
                symbol=symbol,
                side=final_decision,
                amount=amount,
                sl_price=sl,
                tp1_price=tp,
                partial_pct=getattr(Config, "PARTIAL_TP_PCT", 0.5)
            )

            sl_tp_success = protection.get("success", False)
            protection_meta = protection

        else:
            sl_tp_success = client.place_sl_tp(symbol, final_decision, amount, sl, tp)
        
        if not sl_tp_success:
            logger.critical(f"⚠️ فشل وضع أوامر الحماية للزوج {symbol}! جاري إغلاق الصفقة...")
            client.close_position(symbol, final_decision, amount)
            notifier.send_message(f"⚠️ <b>تنبيه طارئ:</b> تم إغلاق صفقة {symbol} يدوياً بسبب فشل وضع الوقف والهدف.")
            if symbol in active_symbols:
                active_symbols.remove(symbol)
                state_manager.save_active_symbols(active_symbols)
            
            # عند فشل الحماية، احفظ تقرير فشل دخول/حماية
            try:
                emergency_exit_details = {
                    "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_price": actual_entry,
                    "pnl": 0.0,
                    "pnl_pct": 0.0,
                    "exit_reason": "PROTECTION_FAILED_EMERGENCY_CLOSE",
                    "slippage": 0.0,
                }

                TradeJournal().finalize_trade(
                    client=client,
                    symbol=symbol,
                    entry_meta={
                        "side": final_decision.upper(),
                        "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "entry_price": float(actual_entry),
                        "amount": float(amount),
                        "sl": float(sl),
                        "tp": float(tp),
                        "entry_reason": "Protection failed after entry",
                        "filter_profile": filter_profile.get("key", "strict"),
                    },
                    exit_details=emergency_exit_details,
                )
            except Exception as e:
                logger.error(f"[TradeJournal] فشل تسجيل الإغلاق الطارئ لـ {symbol}: {e}")

            return False
        
        try:
            latest_bar = bars_df.iloc[-2] if bars_df is not None and len(bars_df) > 1 else {}
            wallet_equity_at_entry, wallet_available_at_entry = get_wallet_equity_snapshot(client, state_manager=state_manager)
            reference_balance = float(getattr(Config, "REFERENCE_BALANCE", 50.0))

            entry_meta = {
                "wallet_equity_at_entry": wallet_equity_at_entry,
                "wallet_available_at_entry": wallet_available_at_entry,
                "reference_balance": reference_balance,
                "exchange_order_id": order.get("id") if isinstance(order, dict) else None,
                "exchange_order": {
                    "id": order.get("id"),
                    "clientOrderId": order.get("clientOrderId"),
                    "status": order.get("status"),
                    "type": order.get("type"),
                    "side": order.get("side"),
                    "price": order.get("price"),
                    "average": order.get("average"),
                    "filled": order.get("filled"),
                    "amount": order.get("amount"),
                    "cost": order.get("cost"),
                } if isinstance(order, dict) else {},
                "side": final_decision.upper(),
                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "entry_price": float(actual_entry),
                "amount": float(amount),
                "sl": float(sl),
                "tp": float(tp),
                "entry_reason": coin_data.get('reason', f"فني:{ta_trend}"),
                "sentiment_score": float(sentiment.get('score') or sentiment.get('galaxy_score') or 0.0) if sentiment else 0.0,
                "adx": float(latest_bar.get('adx', 0.0)),
                "ema_200": float(latest_bar.get('ema_200', 0.0)),
                "atr": float(latest_bar.get('atr', 0.0)),
                "spread": float((ticker.get('ask', 0) - ticker.get('bid', 0)) / ticker.get('bid', 1)) if ticker and ticker.get('bid', 0) > 0 else 0.0,
                "volume_24h": float(ticker.get('quoteVolume', 0.0)) if ticker else 0.0,
                "risk_pct": float(trade_risk_pct * 100),
                "leverage": int(trade_leverage),
                "partial_tp_enabled": bool(partial_enabled),
                "partial_tp_done": False,
                "tp1": float(tp),
                "tp1_order_id": protection_meta.get("tp1_order_id"),
                "tp1_client_id": protection_meta.get("tp1_client_id"),
                "partial_amount": float(protection_meta.get("partial_amount", amount * getattr(Config, "PARTIAL_TP_PCT", 0.5))),
                "runner_amount": float(protection_meta.get("runner_amount", amount * (1 - getattr(Config, "PARTIAL_TP_PCT", 0.5)))),
                "original_amount": float(amount),
                "current_sl": float(sl),
                "filter_profile": filter_profile.get("key", "strict"),
            }
            # ==================================================
            # Trade Journal: تبرير الدخول وسجل الشموع
            # ==================================================
            try:
                planned_risk = abs(float(actual_entry) - float(sl))
                planned_reward = abs(float(tp) - float(actual_entry))
                planned_rr = planned_reward / planned_risk if planned_risk > 0 else 0.0

                decision_context = {
                    "symbol": symbol,
                    "side": final_decision,
                    "filter_profile": filter_profile,
                    "ta_trend": ta_trend,
                    "entry_reason": coin_data.get("reason", ""),
                    "sentiment": sentiment or {},
                    "sniper": coin_data.get("sniper", {}),
                    "support": coin_data.get("support"),
                    "resistance": coin_data.get("resistance"),
                    "invalidation_level": invalidation_level,
                    "risk_plan": {
                        "entry": float(actual_entry),
                        "sl": float(sl),
                        "tp": float(tp),
                        "planned_rr": round(planned_rr, 4),
                        "atr": float(atr_val or 0.0),
                        "risk_pct": float(trade_risk_pct * 100),
                        "leverage": int(trade_leverage),
                    },
                    "market_levels_summary": {
                        "bullish_obs": len((coin_data.get("market_levels") or {}).get("bullish_obs", [])),
                        "bearish_obs": len((coin_data.get("market_levels") or {}).get("bearish_obs", [])),
                        "bullish_fvgs": len((coin_data.get("market_levels") or {}).get("bullish_fvgs", [])),
                        "bearish_fvgs": len((coin_data.get("market_levels") or {}).get("bearish_fvgs", [])),
                    },
                }

                entry_meta["decision_context"] = decision_context

                journal_dir = TradeJournal().record_entry(
                    client=client,
                    symbol=symbol,
                    entry_meta=entry_meta,
                    decision_context=decision_context,
                )

                if journal_dir:
                    entry_meta["journal_dir"] = journal_dir

            except Exception as e:
                logger.error(f"[TradeJournal] فشل تسجيل مبررات دخول الصفقة {symbol}: {e}")

            state_manager.save_entry_metadata(symbol, entry_meta)
            logger.info(f"تم حفظ بيانات دخول صفقة {symbol} بنجاح.")
        except Exception as e:
            logger.error(f"خطأ أثناء حفظ بيانات دخول الصفقة: {e}")
        
        trailing_manager.register_trade(
            symbol=symbol,
            side=final_decision,
            entry_price=actual_entry,
            initial_sl=sl,
            amount=amount,
            partial_tp_enabled=partial_enabled,
        )
    
        emoji = '🟢 BUY' if final_decision == 'buy' else '🔴 SELL'
        coin_data['decision'] = emoji
        coin_data['reason'] = f"تم التنفيذ | السعر: {actual_entry:.4f}"
        scan_results.append(coin_data)
        
        active_symbols.add(symbol)
        state_manager.save_active_symbols(active_symbols)
        logger.info(f"تم فتح صفقة لـ {symbol}. إجمالي الصفقات المفتوحة: {len(active_symbols)}")
        return True
    return False

def handle_trade_close(alert, state_manager, client=None):
    """
    معالجة إغلاق الصفقة: لا نحذف بيانات الدخول إلا بعد نجاح التسجيل.
    """
    symbol = alert["symbol"].split(":")[0]

    # نقرأ بيانات الدخول بدون حذفها أولاً
    entry_meta = state_manager.get_state().get("entry_metadata", {}).get(symbol, {})

    if not entry_meta:
        logger.error(f"❌ لا توجد entry_metadata للصفقة المغلقة {symbol}. لن يتم تسجيلها بدقة.")
        return None

    is_sl = "STOP" in alert["type"]
    target_exit = entry_meta.get("sl") if is_sl else entry_meta.get("tp1") or entry_meta.get("tp")

    actual_exit = float(alert.get("price", 0.0) or 0.0)

    slippage_exit = 0.0
    if target_exit and float(target_exit) > 0 and actual_exit > 0:
        slippage_exit = abs(actual_exit - float(target_exit)) / float(target_exit)

    pnl_val = float(alert.get("pnl", 0.0) or 0.0)
    partial_realized = float(entry_meta.get("partial_realized_pnl", 0.0) or 0.0)
    pnl_val += partial_realized

    entry_price = float(entry_meta.get("entry_price", 0.0) or 0.0)
    amount = float(entry_meta.get("amount", 0.0) or 0.0)
    entry_notional = abs(entry_price * amount)

    pnl_pct = (pnl_val / entry_notional * 100) if entry_notional > 0 else 0.0

    wallet_equity_at_entry = float(entry_meta.get("wallet_equity_at_entry", 0.0) or 0.0)
    reference_balance = float(
        entry_meta.get("reference_balance", getattr(Config, "REFERENCE_BALANCE", 50.0)) or 50.0
    )

    wallet_pnl_pct = (pnl_val / wallet_equity_at_entry * 100) if wallet_equity_at_entry > 0 else 0.0
    pnl_ref_50 = (wallet_pnl_pct / 100.0) * reference_balance

    exit_reason = alert["type"]
    if partial_realized != 0:
        exit_reason = f"{alert['type']} + PARTIAL_TP1"

    exit_details = {
        "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "exit_price": actual_exit,
        "pnl": pnl_val,
        "pnl_pct": pnl_pct,
        "wallet_pnl_pct": wallet_pnl_pct,
        "reference_balance": reference_balance,
        "pnl_ref_50": pnl_ref_50,
        "exit_reason": exit_reason,
        "slippage": slippage_exit,
    }

    logged = log_testnet_trade(symbol, entry_meta, exit_details)

    try:
        if client is not None:
            journal_dir = TradeJournal().finalize_trade(
                client=client,
                symbol=symbol,
                entry_meta=entry_meta,
                exit_details=exit_details,
            )
            if journal_dir:
                logger.info(f"[TradeJournal] ملفات تحليل الصفقة محفوظة في: {journal_dir}")
    except Exception as e:
        logger.error(f"[TradeJournal] فشل إنهاء سجل الصفقة {symbol}: {e}")

    # نحذف metadata فقط بعد نجاح تسجيل الصفقة
    if logged:
        state_manager.pop_entry_metadata(symbol)
    else:
        logger.error(f"❌ لم يتم حذف metadata لـ {symbol} لأن التسجيل في CSV فشل.")

    try:
        tracker = PerformanceTracker(state_manager)
        wallet = tracker.get_wallet()
        logger.info(
            f"[SimWallet] الرصيد التراكمي بعد إغلاق {symbol}: "
            f"{wallet['wallet_balance']:.2f} USDT | "
            f"Realized: {wallet['realized_pnl']:+.2f} USDT"
        )
    except Exception as e:
        logger.error(f"[SimWallet] فشل تحديث المحفظة بعد الإغلاق: {e}")

    # --- تطبيق PairLocks (التبريد بعد الخسارة) ---
    is_profit = pnl_val > 0
    consecutive_losses = state_manager.record_trade_result(symbol, is_profit)

    if not is_profit:
        if consecutive_losses >= 3:
            lock_until = datetime.now().timestamp() + (24 * 3600)
            state_manager.lock_pair(symbol, lock_until, "خسارة 3 مرات متتالية")
            logger.warning(f"🔒 [PairLocks] تم قفل {symbol} لمدة 24 ساعة بسبب 3 خسائر متتالية.")
        elif consecutive_losses >= 2:
            lock_until = datetime.now().timestamp() + (4 * 3600)
            state_manager.lock_pair(symbol, lock_until, "خسارة مرتين متتاليتين")
            logger.warning(f"🔒 [PairLocks] تم قفل {symbol} لمدة 4 ساعات بسبب خسارتين متتاليتين.")

    return exit_details

def send_daily_performance_report(client, state_manager, notifier):
    """
    توليد وإرسال التقرير اليومي للمقارنة بين التداول التجريبي (Testnet) والاختبار العكسي (Backtest)
    """
    logger.info("📊 جاري توليد التقرير اليومي للأداء والمقارنة...")
    
    testnet_csv = "testnet_trades_log.csv"
    testnet_trades_count = 0
    testnet_win_rate = 0.0
    testnet_pf = 0.0
    testnet_max_dd = 0.0
    testnet_avg_slippage = 0.0
    testnet_net_return = 0.0
    
    if os.path.exists(testnet_csv):
        try:
            df_test = pd.read_csv(testnet_csv)
            if not df_test.empty:
                testnet_trades_count = len(df_test)
                pnls = pd.to_numeric(df_test['pnl'], errors='coerce').dropna().values
                winners = pnls[pnls > 0]
                losers = pnls[pnls <= 0]
                
                if testnet_trades_count > 0:
                    testnet_win_rate = len(winners) / testnet_trades_count * 100
                    
                sum_win = winners.sum() if len(winners) > 0 else 0
                sum_loss = abs(losers.sum()) if len(losers) > 0 else 0
                testnet_pf = sum_win / sum_loss if sum_loss > 0 else (float('inf') if sum_win > 0 else 0.0)
                
                equity = [100.0]
                pnl_pcts = pd.to_numeric(df_test.get('pnl_pct', df_test['pnl'] / 10.0), errors='coerce').fillna(0).values
                for p_pct in pnl_pcts:
                    equity.append(equity[-1] + p_pct)
                
                peak = equity[0]
                for val in equity:
                    if val > peak:
                        peak = val
                    dd = (peak - val) / peak * 100
                    if dd > testnet_max_dd:
                        testnet_max_dd = dd
                        
                slippages = pd.to_numeric(df_test['slippage'], errors='coerce').dropna().values
                if len(slippages) > 0:
                    testnet_avg_slippage = np.mean(slippages) * 100
                    
                testnet_net_return = sum(pnls)
        except Exception as e:
            logger.error(f"خطأ في معالجة سجل صفقات التجريبي: {e}")
            
    backtest_csv = "backtest_results.csv"
    bt_trades_count = 0
    bt_win_rate = 0.0
    bt_pf = 0.0
    bt_max_dd = 0.0
    bt_net_return = 0.0
    
    if os.path.exists(backtest_csv):
        try:
            df_bt = pd.read_csv(backtest_csv)
            if not df_bt.empty:
                bt_trades_count = len(df_bt)
                pnls_bt = pd.to_numeric(df_bt['pnl'], errors='coerce').dropna().values
                winners_bt = pnls_bt[pnls_bt > 0]
                losers_bt = pnls_bt[pnls_bt <= 0]
                
                if bt_trades_count > 0:
                    bt_win_rate = len(winners_bt) / bt_trades_count * 100
                    
                sum_win_bt = winners_bt.sum() if len(winners_bt) > 0 else 0
                sum_loss_bt = abs(losers_bt.sum()) if len(losers_bt) > 0 else 0
                bt_pf = sum_win_bt / sum_loss_bt if sum_loss_bt > 0 else (float('inf') if sum_win_bt > 0 else 0.0)
                
                equity_bt = [100.0]
                pnl_pcts_bt = pd.to_numeric(df_bt.get('pnl_pct', df_bt['pnl'] / 10.0), errors='coerce').fillna(0).values
                for p_pct in pnl_pcts_bt:
                    equity_bt.append(equity_bt[-1] + p_pct)
                
                peak_bt = equity_bt[0]
                for val in equity_bt:
                    if val > peak_bt:
                        peak_bt = val
                    dd = (peak_bt - val) / peak_bt * 100
                    if dd > bt_max_dd:
                        bt_max_dd = dd
                        
                bt_net_return = sum(pnls_bt)
        except Exception as e:
            logger.error(f"خطأ في معالجة سجل صفقات الباك تست: {e}")
            
    rejections = state_manager.get_rejection_counters()
    
    report = f"📊 <b>التقرير المقارن اليومي (Testnet vs Backtest)</b> 📊\n\n"
    report += f"🗓️ <b>التاريخ:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    report += f"〰️〰️〰️〰️〰️〰️〰️〰️\n\n"
    
    report += f"🟢 <b>أداء التداول التجريبي الحقيقي (Live Testnet):</b>\n"
    report += f"• عدد الصفقات: <code>{testnet_trades_count}</code>\n"
    report += f"• نسبة الفوز: <code>{testnet_win_rate:.2f}%</code>\n"
    report += f"• معامل الربح (PF): <code>{testnet_pf:.2f}</code>\n"
    report += f"• أقصى تراجع (Max DD): <code>{testnet_max_dd:.2f}%</code>\n"
    report += f"• متوسط الانزلاق الفعلي: <code>{testnet_avg_slippage:.3f}%</code>\n"
    report += f"• صافي الأرباح/الخسائر: <code>{testnet_net_return:+.2f}</code> USDT\n\n"
    
    report += f"🔵 <b>المرجع التاريخي المقارن (Backtest 365d):</b>\n"
    report += f"• عدد الصفقات الكلي: <code>{bt_trades_count}</code>\n"
    report += f"• نسبة الفوز: <code>{bt_win_rate:.2f}%</code>\n"
    report += f"• معامل الربح (PF): <code>{bt_pf:.2f}</code>\n"
    report += f"• أقصى تراجع (Max DD): <code>{bt_max_dd:.2f}%</code>\n"
    report += f"• صافي العائد الكلي: <code>{bt_net_return:+.2f}</code> USDT\n\n"
    
    report += f"🚫 <b>إحصائيات الصفقات المرفوضة بالفلاتر:</b>\n"
    if rejections:
        for filter_name, count in rejections.items():
            report += f"• فلتر <code>{filter_name}</code>: تم إلغاء <code>{count}</code> صفقات\n"
    else:
        report += "• لا يوجد صفقات مرفوضة اليوم.\n"
        
    report += f"\n💡 <i>ملاحظة: البوت يحتاج إلى 7 إلى 14 يوماً من الأداء التجريبي المستقر للتحقق من الموثوقية الكاملة.</i>"
    
    notifier.send_message(report)
    state_manager.reset_rejection_counters()
    
    state_manager.state["last_daily_report_date"] = datetime.now().strftime("%Y-%m-%d")
    state_manager.save_state()


def reconcile_positions(client, state_manager):
    """
    التأكد من أن الصفقات المسجلة في الذاكرة تتطابق مع باينانس.
    إذا أغلقت باينانس صفقة ولم يعلم بها البوت (بسبب انقطاع مثلاً)، سيتم مسحها من الذاكرة لتجنب التعليق.
    """
    try:
        logger.info("🔄 جاري مزامنة الصفقات المفتوحة مع منصة باينانس...")
        positions = client.exchange.fetch_positions()
        # توحيد اسم العملة ليطابق النظام (بدون :USDT)
        exchange_active = {p['symbol'].split(':')[0] for p in positions if float(p.get('info', {}).get('positionAmt', 0)) != 0}
        
        local_symbols = state_manager.get_active_symbols()  # set
        
        # إيجاد الصفقات الموجودة في الذاكرة لكنها غير مفتوحة على باينانس دون تعديل ال_set أثناء التكرار
        stale = {sym for sym in local_symbols if sym not in exchange_active}
        if stale:
            logger.warning(f"⚠️ صفقات مغلقة على باينانس لكنها في الذاكرة — سيتم حذفها: {stale}")
            local_symbols = local_symbols - stale
            
        # إيجاد الصفقات المفتوحة في باينانس لكنها غير مسجلة في ذاكرة البوت (مثلاً فتحت يدوياً أو بسبب انقطاع)
        missing = {sym for sym in exchange_active if sym not in local_symbols}
        if missing:
            logger.info(f"➕ صفقات مفتوحة على باينانس غير مسجلة في البوت — سيتم إضافتها: {missing}")
            local_symbols = local_symbols.union(missing)
            
        if stale or missing:
            state_manager.save_active_symbols(local_symbols)
            
    except Exception as e:
        logger.error(f"❌ فشل مزامنة الصفقات: {e}")

def check_network_connection(client):
    """
    مراقبة حالة الشبكة (Hummingbot Network Check).
    يتحقق من الاتصال بباينانس، وإذا فشل، يدخل البوت في وضع التعليق الآمن حتى يعود الاتصال.
    """
    while True:
        try:
            client.exchange.fetch_time()
            return True
        except Exception as e:
            logger.warning(f"⚠️ [Network Monitor] فقدان الاتصال بشبكة Binance. البوت في وضع التعليق الآمن. إعادة المحاولة بعد 10 ثوانٍ...")
            time.sleep(10)

def run_bot_iteration(client, hybrid_strategy, risk_manager, trailing_manager, state_manager, notifier, news_engine, ai_engine, ta_engine, social_engine, universe_filter, derivatives_filter, last_scan_time=None):
    # مزامنة الصفقات للتأكد من أن الذاكرة متوافقة مع منصة باينانس (لتنظيف الصفقات المغلقة أو غير المعروفة)
    reconcile_positions(client, state_manager)
    
    active_symbols = state_manager.get_active_symbols()
        
    logger.info("=========================================")
    logger.info("بدء جولة مسح السوق (Market Screener)...")
    logger.info("=========================================")
    
    # [مراقب الصفقات المغلقة]
    if last_scan_time:
        logger.info("جاري التحقق من الصفقات المغلقة (SL/TP) خلال الاستراحة...")
        check_symbols = set(client.get_top_volatile_symbols(limit=15))
        check_symbols.update(active_symbols)
        
        for sym in check_symbols:
            alerts = client.get_recent_sl_tp_fills(sym, last_scan_time)
            for alert in alerts:
                sym_clean = alert["symbol"].split(":")[0]
                meta = state_manager.state.get("entry_metadata", {}).get(sym_clean, {})

                # إذا الصفقة Partial TP وما زال المركز مفتوحاً، لا تعتبر هذا إغلاقاً كاملاً.
                if meta.get("partial_tp_enabled") and not meta.get("partial_tp_done"):
                    current_amt = client.get_position_amount(sym_clean)

                    if current_amt > 0:
                        logger.info(
                            f"🎯 [Partial TP Detected] تم رصد ربح جزئي لـ {sym_clean}، "
                            f"لكن الصفقة ما زالت مفتوحة. سيتم تركها لـ manage_partial_tp_runner."
                        )

                        # نخزن الربح الجزئي داخل الميتاداتا حتى لا يضيع من المحفظة الوهمية لاحقاً.
                        partial_pnl = float(alert.get("pnl", 0.0))
                        meta["partial_realized_pnl"] = float(meta.get("partial_realized_pnl", 0.0)) + partial_pnl
                        meta["partial_tp_alert_seen"] = True

                        state_manager.state["entry_metadata"][sym_clean] = meta
                        state_manager.save_state()

                        continue

                pnl_pct = 0.0
                exit_details = None
                try:
                    exit_details = handle_trade_close(alert, state_manager, client=client)
                    if exit_details:
                        pnl_pct = exit_details.get("pnl_pct", 0.0)
                except Exception as e:
                    logger.error(f"خطأ أثناء تسجيل إغلاق الصفقة: {e}")
                    
                if exit_details:
                    notifier.send_pnl_alert(
                        alert['symbol'],
                        alert['type'],
                        exit_details.get('exit_price', alert.get('price', 0.0)),
                        exit_details.get('pnl', alert.get('pnl', 0.0)),
                        exit_details.get('pnl_pct', 0.0),
                        wallet_pnl_pct=exit_details.get("wallet_pnl_pct", 0.0),
                        pnl_ref_50=exit_details.get("pnl_ref_50", 0.0),
                        reference_balance=exit_details.get("reference_balance", getattr(Config, "REFERENCE_BALANCE", 50.0)),
                    )
                else:
                    notifier.send_message(
                        f"⚠️ <b>تم رصد إغلاق صفقة لكن فشل تسجيلها</b>\n"
                        f"الزوج: <code>{alert.get('symbol')}</code>"
                    )
                logger.info(f"إشعار إغلاق صفقة: {alert['symbol']} | {alert['type']} | PnL: {alert['pnl']} | Pct: {pnl_pct}%")
                if sym_clean in active_symbols:
                    active_symbols.remove(sym_clean)
                    state_manager.save_active_symbols(active_symbols)
                if trailing_manager.has_trade(sym_clean):
                    trailing_manager.unregister_trade(sym_clean)

    # جلب الرصيد الإجمالي (يشمل الرصيد المتاح + الهامش المحجوز في الصفقات)
    balance = client.get_balance()
    usdt_free = balance.get('USDT', {}).get('free', 0) if balance else 0
    usdt_total = balance.get('USDT', {}).get('total', 0) if balance else 0
    
    # [ميزة الرصيد الوهمي]: تقييد الرصيد المستخدم للمحاكاة (مثال: 50 دولار فقط بدلاً من 100 ألف)
    # ملاحظة: هذا التقييد يعمل *فقط* في الـ Testnet (التجريبي). 
    # بمجرد تحويل البوت للحساب الحقيقي (USE_TESTNET=False) سيتم إلغاء هذا التقييد برمجياً وسيعتمد على الرصيد الفعلي في منصة باينانس.
    simulated_cap = getattr(Config, "TESTNET_SIMULATED_BALANCE", 0)

    if getattr(Config, "USE_TESTNET", False) and simulated_cap > 0:
        info = balance.get("info", {}) if balance else {}
        total_unrealized = float(info.get("totalUnrealizedProfit", 0) or 0)

        tracker = PerformanceTracker(state_manager)
        sim_wallet = tracker.get_wallet(unrealized_pnl=total_unrealized)

        usdt_total = sim_wallet["equity"]
        usdt_free = sim_wallet["available"]

        logger.info(
            f"[SimWallet] الرصيد المتاح: {usdt_free:.2f} USDT | "
            f"Equity وهمي: {usdt_total:.2f} USDT | "
            f"Realized: {sim_wallet['realized_pnl']:+.2f} USDT | "
            f"Unrealized: {sim_wallet['unrealized_pnl']:+.2f} USDT"
        )
    else:
        logger.info(
            f"الرصيد المتاح: {usdt_free:.2f} USDT | "
            f"الإجمالي Equity: {usdt_total:.2f} USDT"
        )
    
    # تحديث الرصيد اليومي (المرجعي) بناءً على الرصيد الإجمالي
    if usdt_total > 0:
        state_manager.update_daily_balance(usdt_total)
        
    initial_balance = state_manager.get_initial_balance()
    
    if usdt_free <= 0:
        logger.error("لا يوجد رصيد حر كافي للبدء.")
        return usdt_free

    # التحقق من حد الخسارة اليومية (Daily Loss Limit) بناءً على الرصيد الإجمالي
    if initial_balance and initial_balance > 0:
        drawdown = ((initial_balance - usdt_total) / initial_balance) * 100
        if drawdown >= Config.DAILY_LOSS_LIMIT_PERCENT:
            msg = f"🛑 إيقاف طارئ (Kill Switch): الخسارة وصلت إلى {drawdown:.2f}% وتجاوزت الحد المسموح ({Config.DAILY_LOSS_LIMIT_PERCENT}%). سيتم إيقاف البوت لحماية المتبقي من رأس المال."
            logger.critical(msg)
            notifier.send_message(f"🚨 <b>{msg}</b>")
            raise SystemExit(msg) # إيقاف البرنامج بالكامل

    # التحقق من الحد الأقصى للصفقات المفتوحة
    max_trades = getattr(Config, 'TESTNET_MAX_OPEN_TRADES', Config.MAX_OPEN_TRADES) if getattr(Config, 'USE_TESTNET', False) else Config.MAX_OPEN_TRADES
    if len(active_symbols) >= max_trades:
        logger.warning(f"وصلنا للحد الأقصى للصفقات المفتوحة ({max_trades}). سنتوقف عن البحث حتى تُغلق صفقة.")
        return usdt_total

    # 2. مسح السوق - استخدام فلتر أمان الكتالوج (Universe Filter) لجلب أفضل العملات المشتعلة المؤهلة
    target_symbols = universe_filter.get_scanning_targets(limit=10)
    logger.info(f"العملات التي سيتم مراقبتها الآن: {', '.join(target_symbols)}")
    
    scan_results  = []
    trade_executed = False
    
    # 3. دورة البحث عن الصفقات
    for symbol in target_symbols:
        coin_name = symbol.split('/')[0]
        logger.info(f"\n--- فحص الزوج: {symbol} ---")
        
        # [تطبيق PairLocks] - تجاوز العملة إذا كانت محظورة بسبب الخسائر
        if state_manager.is_pair_locked(symbol):
            lock_info = state_manager.get_pair_lock_info(symbol)
            logger.warning(f"[{symbol}] 🔒 العملة محظورة مؤقتاً بسبب: {lock_info.get('reason', 'غير محدد')}. سيتم تجاوزها.")
            continue
        
        # تصنيف العملة وقواعد حماية Meme/New
        coin_type, classification_reason = universe_filter.classify_symbol(symbol)
        logger.info(f"[{symbol}] تصنيف العملة: {coin_type.upper()} ({classification_reason})")
        
        # إعدادات الرافعة والمخاطرة المخصصة
        trade_leverage = int(state_manager.get("trade_leverage", getattr(Config, "LEVERAGE", 10)))
        min_lev = int(getattr(Config, "MIN_LEVERAGE", 1))
        max_lev = int(getattr(Config, "MAX_LEVERAGE", 20))
        trade_leverage = max(min_lev, min(trade_leverage, max_lev))

        trade_risk_pct = Config.RISK_PER_TRADE_PERCENT / 100.0
        
        if coin_type in ['meme', 'new']:
            # لا نغيّر الرافعة هنا؛ الرافعة تتحكم بها Telegram/Config.
            # نخفض المخاطرة فقط للعملات عالية الخطورة.
            trade_risk_pct = getattr(Config, 'MEME_RISK_PER_TRADE_PERCENT', 0.25) / 100.0
            logger.info(f"[{symbol}] تطبيق إعدادات حماية الميم/الجديد: رافعة {trade_leverage}x | مخاطرة {trade_risk_pct*100:.2f}%")
            
        # تطبيق مضاعف مخاطرة Testnet إن وجد
        if getattr(Config, 'USE_TESTNET', False) and hasattr(Config, 'TESTNET_RISK_MULTIPLIER'):
            trade_risk_pct = trade_risk_pct * getattr(Config, 'TESTNET_RISK_MULTIPLIER', 1.0)
            logger.info(f"[{symbol}] تطبيق مضاعف مخاطرة التست نت: {getattr(Config, 'TESTNET_RISK_MULTIPLIER')}x -> مخاطرة مخفضة {trade_risk_pct*100:.3f}%")

        # أ) التحليل الفني الأساسي (15 دقيقة) - نطلب 250 لضمان وجود بيانات تكفي لـ EMA 200
        bars_df = client.fetch_ohlcv(symbol, timeframe='15m', limit=250)
        if bars_df is None or bars_df.empty:
            scan_results.append({'coin': coin_name, 'decision': '⚪ SKIP', 'reason': 'فشل جلب البيانات 15m', 'price': 0, 'rsi': 0, 'macd': 0, 'bb_low': 0, 'bb_high': 0})
            continue
            
        bars_df  = ta_engine.add_indicators(bars_df)
        ta_details = {}
        ta_trend = ta_engine.evaluate_trend(bars_df, symbol=symbol, details=ta_details)
        
        # [تحديث المحترفين]: Multi-Timeframe Confirmation (تأكيد الاتجاه العام من فريم 1 ساعة)
        bars_1h = client.fetch_ohlcv(symbol, timeframe='1h', limit=250)
        trend_1h = 'neutral'
        if bars_1h is not None and not bars_1h.empty:
            bars_1h = ta_engine.add_indicators(bars_1h)
            trend_1h = ta_engine.evaluate_trend(bars_1h, symbol=symbol)
            logger.info(f"[{symbol}] اتجاه فريم 1 ساعة (1H Trend): {trend_1h}")
            
            # فلترة فورية: لا تدخل شراء إذا كان الاتجاه العام هابط، ولا بيع إذا كان صاعد
            if ta_trend == 'buy' and trend_1h == 'sell':
                logger.warning(f"[{symbol}] ❌ تم رفض الشراء: الإشارة على 15m صاعدة لكن فريم 1H هابط (مصيدة ثيران).")
                state_manager.increment_rejection_counter("Multi-Timeframe")
                scan_results.append({'coin': coin_name, 'decision': '🔴 REJECT', 'reason': 'فريم 1H هابط (Multi-Timeframe)', 'price': bars_df.iloc[-1].get('close',0), 'rsi': bars_df.iloc[-1].get('rsi',0)})
                
                # طباعة التقرير التفصيلي اللوجي
                ema_200 = ta_details.get('ema_200', 0)
                close_price = ta_details.get('close', 0)
                ema_trend = 'neutral'
                if ema_200 > 0:
                    ema_trend = 'bullish' if close_price > ema_200 else 'bearish'
                logger.info(f"📊 [ملخص فحص {symbol}] | النقاط: {ta_details.get('score', 0)} | ADX: {ta_details.get('adx', 0):.1f} | EMA200 ترند: {ema_trend} | فريم 1H ترند: {trend_1h} | القرار النهائي: REJECT | السبب: فريم 1H هابط (Multi-Timeframe)")
                
                continue
            elif ta_trend == 'sell' and trend_1h == 'buy':
                logger.warning(f"[{symbol}] ❌ تم رفض البيع: الإشارة على 15m هابطة لكن فريم 1H صاعد (مصيدة دببة).")
                state_manager.increment_rejection_counter("Multi-Timeframe")
                scan_results.append({'coin': coin_name, 'decision': '🔴 REJECT', 'reason': 'فريم 1H صاعد (Multi-Timeframe)', 'price': bars_df.iloc[-1].get('close',0), 'rsi': bars_df.iloc[-1].get('rsi',0)})
                
                # طباعة التقرير التفصيلي اللوجي
                ema_200 = ta_details.get('ema_200', 0)
                close_price = ta_details.get('close', 0)
                ema_trend = 'neutral'
                if ema_200 > 0:
                    ema_trend = 'bullish' if close_price > ema_200 else 'bearish'
                logger.info(f"📊 [ملخص فحص {symbol}] | النقاط: {ta_details.get('score', 0)} | ADX: {ta_details.get('adx', 0):.1f} | EMA200 ترند: {ema_trend} | فريم 1H ترند: {trend_1h} | القرار النهائي: REJECT | السبب: فريم 1H صاعد (Multi-Timeframe)")
                
                continue

        latest = bars_df.iloc[-1]
        coin_data = {
            'coin': coin_name,
            'price':    latest.get('close', 0),
            'open':     latest.get('open', 0),
            'rsi':      latest.get('rsi', 0),
            'macd':     latest.get('macd', 0),
            'ema_50':   latest.get('ema_50', 0),
            'bb_low':   latest.get('bb_low', 0),
            'bb_high':  latest.get('bb_high', 0),
            'support':  latest.get('support', 0),
            'resistance': latest.get('resistance', 0),
            'market_levels': ta_details.get('market_levels', {})
        }
        
        # ب) تحليل المشاعر عبر نظام هجين (LunarCrush أولاً، ثم NewsAPI+Groq)
        sentiment = None
        
        # المرحلة 1: LunarCrush (الأسرع والأكثر تخصصاً للكريبتو)
        logger.info(f"[{symbol}] جاري سؤال LunarCrush عن مشاعر {coin_name}...")
        sentiment = social_engine.get_social_sentiment(coin_name)
        time.sleep(1.5)  # Rate Limiting: منع خطأ 429 من LunarCrush
        
        if sentiment:
            logger.info(f"[LunarCrush] {coin_name}: {sentiment['label'].upper()} | Galaxy={sentiment['galaxy_score']} | Raw={sentiment['raw_sentiment_pct']}%")
        else:
            # المرحلة 2: NewsAPI + Groq (الاحتياطي للعملات غير الموجودة في LunarCrush)
            logger.info(f"[{symbol}] لا توجد بيانات LunarCrush - جاري الاحتياط بـ NewsAPI+Groq...")
            news = news_engine.fetch_news_for_coin(coin_name, page_size=2)
            if news and news[0].get('title'):
                title = news[0]['title']
                description = news[0].get('description', '')
                full_text = f"{title}. {description}"
                logger.info(f"أحدث خبر عن {coin_name}: {title}")
                sentiment = ai_engine.analyze_sentiment(full_text)
                if sentiment:
                    logger.info(f"[Groq/Llama] {coin_name}: {sentiment['label'].upper()} (ثقة: {sentiment['score']:.2f})")
            else:
                logger.warning(f"لا توجد أخبار حديثة عن {coin_name}.")
        
        # ج) القرار النهائي (الاستراتيجية الهجينة)
        # نمرر coin_data ليقوم المحرك بمنع الدخول الأعمى إذا كانت المؤشرات سيئة جداً
        decision_obj = hybrid_strategy.decide(ta_trend, sentiment, ta_data=coin_data)
        final_decision = decision_obj.get("action", "hold")
        hybrid_reason = decision_obj.get("reason", "انتظار إشارة فنية")
        risk_multiplier = decision_obj.get("risk_multiplier", 1.0)
        
        # تطبيق مضاعف المخاطرة الخاص بالذكاء الاصطناعي (Risk Reducer)
        if risk_multiplier < 1.0:
            trade_risk_pct = trade_risk_pct * risk_multiplier
            logger.info(f"[{symbol}] تم خفض المخاطرة بنسبة { (1 - risk_multiplier) * 100 }% بناءً على قرار الذكاء الاصطناعي.")
            
        logger.info(f"[{symbol}] القرار المدمج النهائي: {final_decision.upper()} | السبب: {hybrid_reason}")
        
        # طباعة التقرير التفصيلي اللوجي
        ema_200 = ta_details.get('ema_200', 0)
        close_price = ta_details.get('close', 0)
        ema_trend = 'neutral'
        if ema_200 > 0:
            ema_trend = 'bullish' if close_price > ema_200 else 'bearish'
            
        rejection_reason = hybrid_reason
        if final_decision == 'hold' and ta_trend in ['buy', 'sell']:
            sentiment_lbl = sentiment.get('label', 'neutral') if sentiment else 'neutral'
            rejection_reason = f"{hybrid_reason} (TA={ta_trend.upper()}, Sentiment={sentiment_lbl.upper()})"
            
        if getattr(Config, 'TESTNET_DEBUG_MODE', False):
            sentiment_lbl = sentiment.get('label', 'neutral') if sentiment else 'neutral'
            logger.info(f"--- [DECISION PIPELINE DEBUG : {symbol}] ---")
            logger.info(f"TA raw signal: {ta_trend.upper()}")
            logger.info(f"Score: {ta_details.get('score', 0)}")
            logger.info(f"ADX: {ta_details.get('adx', 0):.1f}")
            logger.info(f"EMA200 trend: {ema_trend}")
            logger.info(f"1H trend: {trend_1h}")
            logger.info(f"Sentiment: {sentiment_lbl.upper()}")
            logger.info(f"Derivatives mode: {Config.DERIVATIVES_FILTER_MODE}")
            logger.info(f"Initial Hybrid Decision: {final_decision.upper()}")
            logger.info(f"Rejection Reason (so far): {rejection_reason}")
            logger.info(f"--------------------------------------------")
        else:
            logger.info(f"📊 [ملخص فحص {symbol}] | النقاط: {ta_details.get('score', 0)} | ADX: {ta_details.get('adx', 0):.1f} | EMA200 ترند: {ema_trend} | فريم 1H ترند: {trend_1h} | القرار النهائي: {final_decision.upper()} | السبب: {rejection_reason}")

        
        if final_decision in ['buy', 'sell']:
            # [تدقيق المشتقات - كوين جلاس]: فحص مخاطر المشتقات وتسجيلها للمراقبة/المنع
            if Config.DERIVATIVES_FILTER_MODE != "off":
                deriv_result = derivatives_filter.evaluate_risk(symbol, final_decision, coin_data['price'])
                derivatives_filter.log_audit_result(symbol, final_decision, coin_data['price'], deriv_result)
                
                # إذا تم اقتراح المنع وكان وضع التفعيل نشطاً
                if Config.DERIVATIVES_FILTER_MODE == "enforce" and deriv_result['decision'] == "BLOCK_SUGGESTED":
                    logger.warning(f"[{symbol}] ❌ تم منع الصفقة بواسطة فلتر المشتقات (CoinGlass): {deriv_result['reason']}")
                    state_manager.increment_rejection_counter("Derivatives (CoinGlass)")
                    coin_data['decision'] = '🔴 REJECT'
                    coin_data['reason'] = f"حظر كوين جلاس: {deriv_result['reason']}"
                    scan_results.append(coin_data)
                    continue
                    
            # [تحديث المحترفين]: الحماية من الدخول المتكرر (Over-trading)
            if client.has_open_position(symbol):
                logger.warning(f"[{symbol}] ❌ تم إلغاء الصفقة: لديك صفقة مفتوحة مسبقاً على هذه العملة!")
                coin_data['decision'] = '🟡 SKIP'
                coin_data['reason'] = "صفقة مفتوحة مسبقاً"
                scan_results.append(coin_data)
                continue
                
            # جلب تفاصيل التغير السعري في 24 ساعة لتفادي الشراء في القمة لعملات الميم/الجديدة
            ticker = None
            try:
                ticker = client.exchange.fetch_ticker(symbol)
            except Exception as e:
                logger.warning(f"تعذر جلب بيانات Ticker للزوج {symbol}: {e}")
                
            change_24h = ticker.get('percentage', 0) if ticker else 0
            logger.info(f"[{symbol}] نسبة تغير السعر في 24 ساعة: {change_24h:+.2f}%")
            
            # حماية الشراء في القمم (Anti-Pump Filter) للعملات عالية الخطورة
            if coin_type in ['meme', 'new'] and change_24h > 15.0 and final_decision == 'buy':
                logger.warning(f"[{symbol}] ❌ تم رفض الشراء: العملة ميم/جديدة وهي في حالة صعود حاد (Pump > 15%). السعر الحالي قد يكون قمة.")
                state_manager.increment_rejection_counter("Anti-Pump")
                coin_data['decision'] = '🔴 REJECT'
                coin_data['reason'] = f"تجنب شراء قمة (صعود 24h: {change_24h:.1f}%)"
                scan_results.append(coin_data)
                continue

            current_price = client.get_current_price(symbol)
            if current_price:
                optimal_entry = ta_details.get('optimal_entry', current_price)
                invalidation_level = ta_details.get('invalidation_level', current_price)
                
                atr_val = float(bars_df.iloc[-2].get('atr', 0)) if len(bars_df) > 1 else 0.0
                
                # حساب الستوب الفعلي لتقدير حجم المخاطرة بدقة
                temp_sl, _ = risk_manager.calculate_sl_tp(final_decision, optimal_entry, atr=atr_val, invalidation_level=invalidation_level, market_levels=coin_data.get('market_levels'))
                amount = risk_manager.calculate_position_size(usdt_free, optimal_entry, custom_risk_percent=trade_risk_pct, atr=atr_val, sl=temp_sl)
                
                margin_required = (amount * optimal_entry) / trade_leverage
                if margin_required > usdt_free * 0.95:
                    logger.warning(f"[{symbol}] الهامش المطلوب أكبر من الرصيد الحر. تخطي الصفقة.")
                    state_manager.increment_rejection_counter("Margin")
                    continue
                
                distance = abs(current_price - optimal_entry) / optimal_entry
                if distance > 0.002: # أكثر من 0.2% انعكاس مطلوب لتفعيل الأمر المعلق
                    if symbol not in state_manager.get_virtual_orders():
                        logger.info(f"🎯 [Sniper] السعر {current_price:.4f} يقترب من Order Block {optimal_entry:.4f}. إرسال تنبيه وحفظ أمر معلق...")
                        notifier.send_message(f"⚠️ <b>تنبيه تحضيري (Pre-Signal):</b>\nالزوج <code>{symbol}</code> يقترب من منطقة مؤسسية (Order Block) عند سعر <code>{optimal_entry:.4f}</code>.\n(جاري المراقبة لانتظار إغلاق الشمعة وتأكيد السيولة...)")
                        from strategy.filter_profiles import get_filter_profile
                        from datetime import datetime, timedelta
                        
                        filter_profile = get_filter_profile(state_manager)
                        profile_key = filter_profile.get("key", "strict")

                        if profile_key == "strict":
                            max_age_minutes = getattr(Config, "VIRTUAL_ORDER_MAX_AGE_MINUTES_STRICT", 90)
                        elif profile_key == "medium":
                            max_age_minutes = getattr(Config, "VIRTUAL_ORDER_MAX_AGE_MINUTES_MEDIUM", 60)
                        else:
                            max_age_minutes = getattr(Config, "VIRTUAL_ORDER_MAX_AGE_MINUTES_RELAXED", 30)

                        created_at = datetime.now()
                        expires_at = created_at + timedelta(minutes=max_age_minutes)
                        
                        temp_sl, temp_tp = risk_manager.calculate_sl_tp(
                            final_decision,
                            optimal_entry,
                            atr=atr_val,
                            invalidation_level=invalidation_level,
                            market_levels=coin_data.get("market_levels", {}),
                            filter_profile=filter_profile,
                        )

                        if temp_tp is None:
                            logger.warning(f"[{symbol}] لن يتم إنشاء أمر معلق لأن TP غير صالح.")
                            continue

                        virtual_order = {
                            "side": final_decision,
                            "entry_price": float(optimal_entry),
                            "optimal_entry": float(optimal_entry),
                            "sl": float(temp_sl),
                            "tp": float(temp_tp),
                            "invalidation_level": float(temp_sl),
                            "ob_mid": float(coin_data.get('ob_mid', optimal_entry)),
                            "amount": float(amount),
                            "atr": float(atr_val),
                            "support": float(coin_data.get('support', 0)),
                            "resistance": float(coin_data.get('resistance', 0)),
                            "trade_leverage": int(trade_leverage),
                            "trade_risk_pct": float(trade_risk_pct),
                            
                            "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S"),
                            "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
                            "max_age_minutes": max_age_minutes,
                            "filter_profile": profile_key,
                            
                            "cancel_if_tp_hit_first": True,
                            "cancel_if_invalidated": True,
                            "cancel_if_trend_flips": True,
                            "created_price": float(current_price),
                            "created_reason": coin_data.get("reason", ""),
                            "created_ta_signal": ta_trend,
                            
                            "sentiment_label": sentiment.get('label', 'neutral') if sentiment else 'neutral',
                            "sentiment_score": float(sentiment.get('score') or sentiment.get('galaxy_score') or 0.0) if sentiment else 0.0,
                            "ta_trend": ta_trend,
                            "market_levels": coin_data.get('market_levels', {})
                        }
                        state_manager.save_virtual_order(symbol, virtual_order)
                    scan_results.append({'coin': coin_name, 'decision': '⏳ V-LIMIT', 'reason': f"أمر معلق عند {optimal_entry:.4f}", 'price': current_price})
                    continue
                else:
                    # السعر مثالي الآن، ننفذ الصفقة
                    trade_executed = execute_and_protect_trade(
                        symbol, final_decision, amount, current_price, trade_leverage, trade_risk_pct, atr_val, 
                        coin_data, sentiment, bars_df, ticker, ta_trend, client, risk_manager, trailing_manager, 
                        state_manager, notifier, active_symbols, scan_results, invalidation_level
                    )
                    
                    if trade_executed and len(active_symbols) >= max_trades:
                        logger.info("تم الوصول للحد الأقصى للصفقات المتزامنة. إيقاف المسح الحالي.")
                        break
                    elif not trade_executed:
                        logger.warning(f"[{symbol}] فشل تنفيذ الصفقة عبر المنصة. لم يتم إضافتها للمراقبة.")
                        coin_data['decision'] = '🔴 FAILED'
                        coin_data['reason'] = f"فشل فتح الصفقة على المنصة (حجم صغير أو خطأ)"
                        scan_results.append(coin_data)
                        
            else:
                logger.warning(f"تعذر الحصول على السعر الحالي للزوج {symbol}")
                scan_results.append({'coin': coin_name, 'decision': '🔴 REJECT', 'reason': 'فشل الحصول على السعر', 'price': 0})
                    
        else:
            coin_data['decision'] = '🟡 HOLD'
            if sentiment and sentiment.get('source') == 'lunarcrush':
                coin_data['reason'] = f"فني:{ta_trend} | 🌙 Galaxy={sentiment.get('galaxy_score')} | Sentiment={sentiment.get('raw_sentiment_pct')}%"
            elif sentiment:
                coin_data['reason'] = f"فني:{ta_trend} | أخبار:{sentiment.get('label','?')}"
            else:
                coin_data['reason'] = f"فني:{ta_trend} | لا بيانات اجتماعية"
            scan_results.append(coin_data)
                
    logger.info("=========================================")
    logger.info("تم إنهاء جولة مسح السوق.")
    
    # --- إرسال الملخص التفصيلي إلى تيليجرام ---
    summary = f"📊 <b>تقرير مسح السوق</b> 📊\n\n"
    summary += f"💰 <b>الرصيد الإجمالي:</b> <code>{usdt_total:.2f}</code> USDT\n"
    summary += f"💵 <b>الرصيد المتاح:</b> <code>{usdt_free:.2f}</code> USDT\n"
    summary += f"🔍 <b>المسح:</b> <code>{len(scan_results)}</code> عملات\n"
    summary += "〰️〰️〰️〰️〰️〰️〰️〰️\n\n"
    
    for r in scan_results:
        price  = r.get('price', 0)
        rsi    = r.get('rsi', 0)
        
        if rsi > 70:    rsi_status = "🔥 تشبع شرائي"
        elif rsi < 30:  rsi_status = "🧊 تشبع بيعي"
        else:           rsi_status = "⚖️ محايد"
        
        summary += f"{r['decision']} | <code>{r['coin']}</code>\n"
        summary += f"💲 السعر: <code>{price:.4f}</code>\n"
        summary += f"📈 RSI: <code>{rsi:.1f}</code> ({rsi_status})\n"
        summary += f"📝 {r.get('reason', '')}\n\n"
    
    summary += "〰️〰️〰️〰️〰️〰️〰️〰️\n"
    if not trade_executed:
        summary += "⏳ <b>النتيجة:</b> لا توجد فرص قوية حالياً. (انتظار الدورة القادمة)"
    else:
        summary += "✅ <b>النتيجة:</b> تم قنص فرصة وتنفيذ صفقة بنجاح!"
        
    notifier.send_message(summary)
    return usdt_total

def _parse_dt_safe(value):
    try:
        if isinstance(value, float) or isinstance(value, int):
            return datetime.fromtimestamp(value)
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def get_current_price_from_ticker(client, symbol):
    try:
        ticker = client.exchange.fetch_ticker(symbol)
        return float(
            ticker.get("last")
            or ticker.get("close")
            or ticker.get("bid")
            or ticker.get("ask")
            or 0.0
        )
    except Exception as e:
        logger.warning(f"[VirtualOrderGuard] تعذر جلب السعر الحالي لـ {symbol}: {e}")
        return 0.0

def check_virtual_order_cancellation(client, symbol, v_order):
    """
    يفحص هل الأمر المعلق ما زال صالحًا أم يجب إلغاؤه.
    يرجع:
    - None إذا الأمر صالح.
    - نص سبب الإلغاء إذا انتهت صلاحيته.
    """
    now = datetime.now()

    side = str(v_order.get("side", "")).lower()
    if side in ["long"]:
        side = "buy"
    elif side in ["short"]:
        side = "sell"

    entry = float(v_order.get("entry_price") or v_order.get("optimal_entry") or 0.0)
    sl = float(v_order.get("sl") or v_order.get("invalidation_level") or 0.0)
    tp = float(v_order.get("tp") or 0.0)
    invalidation = float(v_order.get("invalidation_level") or sl or 0.0)

    # 1. انتهاء الوقت
    expires_at = _parse_dt_safe(v_order.get("expires_at"))
    if expires_at and now > expires_at:
        return f"انتهت صلاحية الأمر المعلق. expires_at={v_order.get('expires_at')}"

    # 2. قراءة شموع قصيرة لمعرفة هل TP أو invalidation ضُرب قبل الدخول
    try:
        bars_1m = client.fetch_ohlcv(symbol, timeframe="1m", limit=20)
        if bars_1m is None or bars_1m.empty:
            current_price = get_current_price_from_ticker(client, symbol)
            recent_high = current_price
            recent_low = current_price
            last_close = current_price
        else:
            recent_high = float(bars_1m["high"].max())
            recent_low = float(bars_1m["low"].min())
            last_close = float(bars_1m.iloc[-1]["close"])
    except Exception as e:
        logger.warning(f"[VirtualOrderGuard] فشل جلب شموع 1m لـ {symbol}: {e}")
        current_price = get_current_price_from_ticker(client, symbol)
        recent_high = current_price
        recent_low = current_price
        last_close = current_price

    # 3. إذا ضرب الهدف المتوقع قبل أن يفعل الدخول، فالفرصة انتهت
    if getattr(Config, "VIRTUAL_ORDER_CANCEL_IF_TP_HIT_FIRST", True) and tp > 0:
        if side == "buy" and recent_high >= tp:
            return f"تم إلغاء الأمر لأن السعر ضرب هدف الشراء قبل العودة للدخول. high={recent_high}, tp={tp}"
        if side == "sell" and recent_low <= tp:
            return f"تم إلغاء الأمر لأن السعر ضرب هدف البيع قبل العودة للدخول. low={recent_low}, tp={tp}"

    # 4. إذا ضرب invalidation قبل الدخول، فالتحليل لم يعد صالحًا
    if getattr(Config, "VIRTUAL_ORDER_CANCEL_IF_INVALIDATED", True) and invalidation > 0:
        if side == "buy" and recent_low <= invalidation:
            return f"تم إلغاء أمر الشراء لأن السعر كسر invalidation قبل الدخول. low={recent_low}, invalidation={invalidation}"
        if side == "sell" and recent_high >= invalidation:
            return f"تم إلغاء أمر البيع لأن السعر كسر invalidation قبل الدخول. high={recent_high}, invalidation={invalidation}"

    # 5. إذا ابتعد السعر كثيرًا عن الدخول ولم يعد قريبًا، فالفرصة غالبًا فاتت
    max_dist = float(getattr(Config, "VIRTUAL_ORDER_MAX_DISTANCE_FROM_ENTRY_PCT", 0.04))
    if entry > 0 and last_close > 0:
        distance = abs(last_close - entry) / entry
        if distance >= max_dist:
            return f"تم إلغاء الأمر لأن السعر ابتعد كثيرًا عن منطقة الدخول. distance={distance:.2%}"

    # 6. فحص تغير الترند العام على 15m
    if getattr(Config, "VIRTUAL_ORDER_CANCEL_IF_TREND_FLIPS", True):
        try:
            bars_15m = client.fetch_ohlcv(
                symbol,
                timeframe="15m",
                limit=getattr(Config, "VIRTUAL_ORDER_TREND_CHECK_LIMIT", 250)
            )

            if bars_15m is not None and not bars_15m.empty and len(bars_15m) >= 210:
                ema_50 = bars_15m["close"].ewm(span=50, adjust=False).mean().iloc[-1]
                ema_200 = bars_15m["close"].ewm(span=200, adjust=False).mean().iloc[-1]

                if side == "buy" and ema_50 < ema_200:
                    return f"تم إلغاء أمر الشراء لأن ترند 15m أصبح هابطًا. EMA50 < EMA200"

                if side == "sell" and ema_50 > ema_200:
                    return f"تم إلغاء أمر البيع لأن ترند 15m أصبح صاعدًا. EMA50 > EMA200"

        except Exception as e:
            logger.warning(f"[VirtualOrderGuard] فشل فحص الترند للأمر المعلق {symbol}: {e}")

    return None

def monitor_virtual_orders(client, state_manager, notifier, trailing_manager, risk_manager):
    """
    مراقبة الأوامر الافتراضية المعلقة (Virtual Pending Orders).
    تستخدم مفاهيم التداول المؤسسي: انتظار إغلاق الشمعة (Candle Close)
    وتأكيد حجم التداول (Volume Confirmation) قبل التنفيذ.
    """
    virtual_orders = state_manager.get_virtual_orders()
    if not virtual_orders:
        return
        
    active_symbols = state_manager.get_active_symbols()
    
    for symbol, v_order in list(virtual_orders.items()):
        if symbol in active_symbols:
            state_manager.remove_virtual_order(symbol)
            continue
            
        cancel_reason = check_virtual_order_cancellation(client, symbol, v_order)

        if cancel_reason:
            logger.warning(f"🧹 [VirtualOrder CANCELLED] {symbol}: {cancel_reason}")
            state_manager.remove_virtual_order(symbol)

            try:
                notifier.send_message(
                    f"🧹 <b>إلغاء أمر قناص معلق</b>\n"
                    f"الزوج: <code>{symbol}</code>\n"
                    f"السبب: <code>{notifier._safe_html(cancel_reason)}</code>"
                )
            except Exception:
                pass

            continue
            
        current_price = client.get_current_price(symbol)
        if not current_price:
            continue
            
        optimal_entry = v_order.get('optimal_entry')
        side = v_order.get('side')
        
        # المرحلة الأولى: هل السعر وصل للمنطقة؟
        price_ready = False
        if side == 'buy' and current_price <= optimal_entry * 1.002:
            price_ready = True
        elif side == 'sell' and current_price >= optimal_entry * 0.998:
            price_ready = True
            
        if price_ready:
            logger.info(f"🎯 [Sniper ALERT] السعر داخل منطقة الـ Order Block لـ {symbol}! جاري فحص تأكيد الإغلاق والسيولة (Multi-Confirmation)...")
            
            try:
                # نجلب الشموع للتأكد
                bars_df = client.fetch_ohlcv(symbol, timeframe='15m', limit=25)
                if bars_df is None or len(bars_df) < 20:
                    continue
                    
                # نستخدم الشمعة المغلقة لضمان التأكيد
                closed_candle = bars_df.iloc[-2]
                
                # حساب متوسط السيولة للشموع السابقة (نستبعد الشمعة الحالية)
                current_volume = closed_candle['volume']
                avg_volume = bars_df['volume'].iloc[-22:-2].mean()
                
                # حساب أبعاد الشمعة لتحديد قوة الرفض (Rejection)
                body = abs(closed_candle['close'] - closed_candle['open'])
                lower_wick = min(closed_candle['open'], closed_candle['close']) - closed_candle['low']
                upper_wick = closed_candle['high'] - max(closed_candle['open'], closed_candle['close'])
                
                ob_mid = v_order.get('ob_mid', optimal_entry)

                # ------------------------------------------------------------
                # فلتر Liquidity Sweep لحظة التأكيد النهائي
                # ------------------------------------------------------------
                recent_low = bars_df["low"].iloc[-14:-2].min()
                recent_high = bars_df["high"].iloc[-14:-2].max()

                swept_liquidity = False

                if side == "buy":
                    swept_liquidity = (
                        closed_candle["low"] < recent_low and
                        closed_candle["close"] > recent_low
                    )

                elif side == "sell":
                    swept_liquidity = (
                        closed_candle["high"] > recent_high and
                        closed_candle["close"] < recent_high
                    )
                
                # فلتر 1: إغلاق الشمعة في الاتجاه الصحيح مع ذيل ارتدادي قوي (Wick Rejection)
                is_closed_correctly = False
                if side == 'buy':
                    is_closed_correctly = (
                        closed_candle['close'] > closed_candle['open'] and
                        lower_wick > body * 1.0 and
                        closed_candle['close'] > ob_mid
                    )
                elif side == 'sell':
                    is_closed_correctly = (
                        closed_candle['close'] < closed_candle['open'] and
                        upper_wick > body * 1.0 and
                        closed_candle['close'] < ob_mid
                    )
                    
                # فلتر 2: سيولة قوية تدل على تدخل المال الذكي (اختراق المتوسط بـ 20%)
                is_volume_confirmed = current_volume > (avg_volume * 1.2)
                
                # ------------------------------------------------------------
                # Trading Filter Profile من تيليجرام
                # ------------------------------------------------------------
                filter_profile = get_filter_profile(state_manager)

                require_volume = bool(filter_profile.get("require_volume", True))
                require_entry_sweep = bool(filter_profile.get("require_entry_sweep", True))

                sniper_confirmed = (
                    is_closed_correctly
                    and (is_volume_confirmed or not require_volume)
                    and (swept_liquidity or not require_entry_sweep)
                )

                if sniper_confirmed:
                    logger.info(
                        f"✅ [Sniper CONFIRMED] {symbol} | "
                        f"profile={filter_profile.get('key')} | "
                        f"closed={is_closed_correctly}, "
                        f"volume={is_volume_confirmed}, "
                        f"sweep={swept_liquidity}"
                    )

                    notifier.send_message(
                        f"✅ <b>إشارة دخول مؤكدة</b>\n"
                        f"الزوج: <code>{symbol}</code>\n"
                        f"وضع الفلاتر: <b>{filter_profile.get('label')}</b>\n"
                        f"closed=<code>{is_closed_correctly}</code> | "
                        f"volume=<code>{is_volume_confirmed}</code> | "
                        f"sweep=<code>{swept_liquidity}</code>\n"
                        f"جاري التنفيذ."
                    )
                    
                    # إعادة حساب الحجم بدقة بناءً على السعر اللحظي ومستوى الستوب
                    balance = client.get_balance()
                    usdt_free = balance.get("USDT", {}).get("free", 0) if balance else 0

                    if getattr(Config, "USE_TESTNET", False) and getattr(Config, "TESTNET_SIMULATED_BALANCE", 0) > 0:
                        info = balance.get("info", {}) if balance else {}
                        total_unrealized = float(info.get("totalUnrealizedProfit", 0) or 0)

                        tracker = PerformanceTracker(state_manager)
                        sim_wallet = tracker.get_wallet(unrealized_pnl=total_unrealized)
                        usdt_free = sim_wallet["available"]

                    temp_sl, _ = risk_manager.calculate_sl_tp(
                        side,
                        current_price,
                        atr=v_order["atr"],
                        invalidation_level=v_order.get("invalidation_level", current_price),
                        market_levels=v_order.get("market_levels"),
                        filter_profile=filter_profile,
                    )

                    recalculated_amount = risk_manager.calculate_position_size(
                        usdt_free,
                        current_price,
                        custom_risk_percent=v_order["trade_risk_pct"],
                        atr=v_order["atr"],
                        sl=temp_sl,
                    )

                    # لا نسمح أن يكون الحجم الجديد أكبر من الحجم الأصلي المحسوب وقت إنشاء الأمر الافتراضي
                    original_amount = float(v_order.get("amount", recalculated_amount) or recalculated_amount)
                    new_amount = min(original_amount, recalculated_amount)
                    
                    if new_amount <= 0:
                        logger.warning(f"⚠️ الرصيد الحر لا يكفي لفتح الصفقة {symbol}")
                        state_manager.remove_virtual_order(symbol)
                        continue

                    # التنفيذ المباشر
                    execute_and_protect_trade(
                        symbol=symbol,
                        final_decision=side,
                        amount=new_amount,
                        current_price=current_price,
                        trade_leverage=v_order['trade_leverage'],
                        trade_risk_pct=v_order['trade_risk_pct'],
                        atr_val=v_order['atr'],
                        coin_data={
                            'support': v_order['support'],
                            'resistance': v_order['resistance'],
                            'price': current_price,
                            'reason': 'SMC Multi-Confirmed',
                            'market_levels': v_order.get('market_levels', {}),
                            'sniper': {
                                'closed_correctly': bool(is_closed_correctly),
                                'volume_confirmed': bool(is_volume_confirmed),
                                'swept_liquidity': bool(swept_liquidity),
                                'require_volume': bool(require_volume),
                                'require_entry_sweep': bool(require_entry_sweep),
                                'profile': filter_profile.get("key"),
                                'closed_candle': {
                                    'open': float(closed_candle.get('open', 0)),
                                    'high': float(closed_candle.get('high', 0)),
                                    'low': float(closed_candle.get('low', 0)),
                                    'close': float(closed_candle.get('close', 0)),
                                    'volume': float(closed_candle.get('volume', 0)),
                                }
                            }
                        },
                        sentiment={'label': v_order['sentiment_label'], 'score': v_order['sentiment_score']},
                        bars_df=bars_df,
                        ticker=client.exchange.fetch_ticker(symbol),
                        ta_trend=v_order['ta_trend'],
                        client=client,
                        risk_manager=risk_manager,
                        trailing_manager=trailing_manager,
                        state_manager=state_manager,
                        notifier=notifier,
                        active_symbols=active_symbols,
                        scan_results=[],
                        invalidation_level=v_order.get('invalidation_level')
                    )
                    state_manager.remove_virtual_order(symbol)
                else:
                    logger.info(
                        f"⏳ [Sniper WAIT] {symbol}: لم يكتمل التأكيد. "
                        f"profile={filter_profile.get('key')}, "
                        f"closed={is_closed_correctly}, "
                        f"volume={is_volume_confirmed}, "
                        f"sweep={swept_liquidity}, "
                        f"require_volume={require_volume}, "
                        f"require_sweep={require_entry_sweep}"
                    )
                    
            except Exception as e:
                logger.error(f"❌ فشل تحويل الأمر المعلق إلى صفقة حقيقية للزوج {symbol}: {e}")

def manage_partial_tp_runner(client, state_manager, trailing_manager, notifier):
    """
    يفحص الصفقات المفتوحة:
    إذا تم تنفيذ TP1 جزئياً، ينقل SL للباقي إلى Breakeven ويترك Runner للتريلينغ.
    """
    state = state_manager.get_state()
    entry_metadata = state.get("entry_metadata", {}) or {}

    for symbol, meta in list(entry_metadata.items()):
        try:
            if not meta.get("partial_tp_enabled"):
                continue

            if meta.get("partial_tp_done"):
                continue

            side = str(meta.get("side", "BUY")).lower()
            if side in ["long"]:
                side = "buy"
            elif side in ["short"]:
                side = "sell"

            original_amount = float(meta.get("original_amount") or meta.get("amount") or 0.0)
            if original_amount <= 0:
                continue

            partial_pct = getattr(Config, "PARTIAL_TP_PCT", 0.5)
            expected_runner = original_amount * (1 - partial_pct)

            current_amount = client.get_position_amount(symbol)

            # إذا نقصت الكمية إلى حدود كمية الـ Runner، نفترض أن TP1 تحقق
            # tolerance بسيط بسبب الدقة والـ rounding
            tolerance = original_amount * 0.03

            tp1_hit = (
                current_amount > 0 and
                current_amount <= expected_runner + tolerance
            )

            if not tp1_hit:
                continue

            entry_price = float(meta.get("entry_price", 0.0))
            if entry_price <= 0:
                continue

            if side == "buy":
                be_sl = entry_price * (1 + getattr(Config, "BREAKEVEN_BUFFER_PCT", 0.001))
            else:
                be_sl = entry_price * (1 - getattr(Config, "BREAKEVEN_BUFFER_PCT", 0.001))

            be_sl = round(be_sl, 5)

            logger.info(f"🎯 [Partial TP] تحقق TP1 لـ {symbol}. نقل الستوب إلى BE={be_sl}")

            old_sl = float(meta.get("current_sl") or meta.get("sl") or 0)

            client.cancel_sl_orders(symbol)
            ok = client.place_sl_only(symbol, side, current_amount, be_sl)

            if not ok:
                logger.critical(
                    f"🚨 فشل نقل SL إلى Breakeven بعد TP1 لـ {symbol}. محاولة إرجاع الستوب القديم..."
                )

                restored = False
                if old_sl > 0:
                    restored = client.place_sl_only(symbol, side, current_amount, old_sl)

                if not restored:
                    notifier.send_message(
                        f"🚨 <b>فشل حرج بعد TP1</b>\n"
                        f"الزوج: <code>{symbol}</code>\n"
                        f"فشل نقل SL إلى Breakeven وفشل إرجاع الستوب القديم.\n"
                        f"راجع الصفقة فوراً أو أغلقها يدوياً."
                    )
                else:
                    notifier.send_message(
                        f"⚠️ <b>فشل نقل SL إلى Breakeven</b>\n"
                        f"الزوج: <code>{symbol}</code>\n"
                        f"تم إرجاع الستوب القديم مؤقتاً عند: <code>{old_sl}</code>."
                    )

                continue

            # تحديث metadata
            meta["partial_tp_done"] = True
            meta["runner_amount"] = float(current_amount)
            meta["current_sl"] = float(be_sl)
            entry_metadata[symbol] = meta

            state["entry_metadata"] = entry_metadata
            state_manager.set("entry_metadata", entry_metadata)

            trailing_manager.mark_partial_tp_done(symbol, be_sl, current_amount)

            notifier.send_message(
                f"🎯 <b>TP1 تحقق بنجاح</b>\n"
                f"الزوج: <code>{symbol}</code>\n"
                f"تم إغلاق <code>{partial_pct*100:.0f}%</code> من الصفقة.\n"
                f"تم نقل SL للباقي إلى Breakeven: <code>{be_sl}</code>\n"
                f"الباقي يعمل الآن كـ Runner مع Trailing Stop."
            )

        except Exception as e:
            logger.error(f"خطأ في manage_partial_tp_runner للزوج {symbol}: {e}")

def monitor_active_trades(client, state_manager, trailing_manager, alert_manager, notifier, last_check_time):
    """
    مراقبة الصفقات المفتوحة في الخلفية للتحقق مما إذا كانت قد أغلقت، تحديث التريلينغ، وتنبيهات الاقتراب.
    """
    active_symbols = state_manager.get_active_symbols()
    if not active_symbols:
        return int(time.time() * 1000)
        
    try:
        # 1. جلب كميات الصفقات الحقيقية من المنصة مباشرة
        positions = client.exchange.fetch_positions() # لا نمرر الرموز لتفادي خطأ CCXT في الفلترة
        live_amts = {}
        for pos in positions:
            sym = pos.get('symbol', '').split(':')[0] # تحويل BTC/USDT:USDT إلى BTC/USDT ليتطابق مع النظام
            amt = float(pos.get('info', {}).get('positionAmt', 0))
            if amt != 0:
                live_amts[sym] = abs(amt)
            
        current_time = int(time.time() * 1000)
        
        for sym in list(active_symbols):
            # 2. تحديث الستوب المتحرك والتنفيذ بالقوة (Active Execution)
            if live_amts.get(sym, 0) > 0:
                current_price = client.get_current_price(sym)
                if current_price:
                    # أ) تحديث الستوب المتحرك
                    if trailing_manager.has_trade(sym):
                        new_sl = trailing_manager.update(sym, current_price, client)
                        if new_sl:
                            logger.info(f"تم تفعيل الستوب المتحرك للزوج {sym} عند السعر {new_sl}")

                    # ب) التحقق من إشعارات الاقتراب
                    meta = state_manager.state.get("entry_metadata", {}).get(sym)
                    if meta:
                        trade_info = {
                            "trade_id": f"{sym}_active",
                            "side": meta.get("side", ""),
                            "entry_price": meta.get("entry_price", 0),
                            "initial_stop_loss": meta.get("sl", 0),
                            "stop_loss": trailing_manager.get_current_sl(sym) or meta.get("sl"),
                            "take_profit": meta.get("tp", 0)
                        }
                        atr = meta.get("atr", 0)
                        if atr > 0:
                            alert_manager.check_alerts(sym, trade_info, current_price, atr)

                    # ب) التنفيذ بالقوة (Virtual SL فقط عند تفعيل Partial TP)
                    meta = state_manager.state.get("entry_metadata", {}).get(sym)
                    if meta:
                        side = meta.get("side", "").upper()
                        current_sl = trailing_manager.get_current_sl(sym) or meta.get("current_sl") or meta.get("sl")
                        tp = meta.get("tp")
                        amt = live_amts.get(sym, meta.get("amount", 0))

                        partial_enabled = bool(meta.get("partial_tp_enabled", False))
                        partial_done = bool(meta.get("partial_tp_done", False))

                        force_close_reason = None

                        if side in ["BUY", "LONG"]:
                            # مع Partial TP لا نغلق ماركت عند TP، نترك أمر TP1 في Binance ينفذ جزئيًا.
                            if current_sl and current_price <= current_sl:
                                force_close_reason = "وقف الخسارة (SL)"

                            if (not partial_enabled) and tp and current_price >= tp:
                                force_close_reason = "الهدف (TP)"

                        elif side in ["SELL", "SHORT"]:
                            if current_sl and current_price >= current_sl:
                                force_close_reason = "وقف الخسارة (SL)"

                            if (not partial_enabled) and tp and current_price <= tp:
                                force_close_reason = "الهدف (TP)"

                        if force_close_reason:
                            logger.critical(
                                f"🚀 [Active Execution] السعر اللحظي ({current_price}) لمس {force_close_reason} "
                                f"لصفقة {sym}! جاري الإغلاق الفوري بالقوة..."
                            )
                            success = client.close_position(sym, side.lower(), amt)
                            if success:
                                live_amts[sym] = 0
                                time.sleep(1.5)

            # 3. التحقق مما إذا كانت الصفقة قد أغلقت (سواء من باينانس أو بتدخلنا القسري أعلاه)
            if live_amts.get(sym, 0) == 0:
                logger.info(f"⚡ تم رصد إغلاق الصفقة للزوج {sym} لحظياً! جاري جلب تفاصيل الربح/الخسارة...")
                # تأخير بسيط لضمان تحديث سجل التداولات في باينانس
                time.sleep(2.0)
                alerts = client.get_recent_sl_tp_fills(sym, since_ms=None) # لا نعتمد على الوقت هنا لضمان عدم ضياع الإشعار
                
                if alerts:
                    # نأخذ آخر أمر إغلاق
                    alert = alerts[-1]
                else:
                    # في حال تأخر الـ API في تحديث السجل، نقوم بحساب الربح والخسارة يدوياً!
                    logger.warning(f"تم إغلاق {sym} ولكن لم يتم العثور على أمر الإغلاق في السجل. سيتم توليد إشعار احتياطي...")
                    meta = state_manager.state.get("entry_metadata", {}).get(sym, {})
                    entry_price = float(meta.get("entry_price", 0))
                    amount = float(meta.get("amount", 0))
                    side = meta.get("side", "BUY")
                    
                    pnl = 0.0
                    if entry_price > 0 and amount > 0 and current_price:
                        if side.upper() in ["BUY", "LONG"]:
                            pnl = (current_price - entry_price) * amount
                        else:
                            pnl = (entry_price - current_price) * amount
                            
                    order_type = "TAKE_PROFIT" if pnl > 0 else "STOP_LOSS"
                    alert = {
                        'symbol': sym,
                        'type': order_type,
                        'price': current_price if current_price else entry_price,
                        'pnl': pnl
                    }

                exit_details = None
                try:
                    exit_details = handle_trade_close(alert, state_manager, client=client)
                except Exception as e:
                    logger.error(f"خطأ أثناء تسجيل إغلاق الصفقة في مراقب الخلفية: {e}")
                    
                if exit_details:
                    notifier.send_pnl_alert(
                        alert["symbol"],
                        alert["type"],
                        exit_details.get("exit_price", alert.get("price", 0.0)),
                        exit_details.get("pnl", alert.get("pnl", 0.0)),
                        exit_details.get("pnl_pct", 0.0),
                        wallet_pnl_pct=exit_details.get("wallet_pnl_pct", 0.0),
                        pnl_ref_50=exit_details.get("pnl_ref_50", 0.0),
                        reference_balance=exit_details.get("reference_balance", getattr(Config, "REFERENCE_BALANCE", 50.0)),
                    )
                else:
                    notifier.send_message(
                        f"⚠️ <b>تم رصد إغلاق صفقة لكن فشل تسجيلها</b>\n"
                        f"الزوج: <code>{alert.get('symbol')}</code>"
                    )
                logger.info(f"إشعار فوري: إغلاق {alert['symbol']} | PnL: {alert['pnl']}")
                    
                # تنظيف الذاكرة
                if sym in active_symbols:
                    active_symbols.remove(sym)
                    state_manager.save_active_symbols(active_symbols)
                if trailing_manager.has_trade(sym):
                    trailing_manager.unregister_trade(sym)

        return current_time
    except Exception as e:
        logger.error(f"خطأ في مراقبة الصفقات النشطة لحظياً: {e}")
        return last_check_time

def main():
    logger.info("=========================================")
    logger.info("🤖 بدء التشغيل الآلي المستمر (Autonomous Loop) 🤖")
    logger.info("سيعمل البوت 24/7 ويقوم بالمسح كل 15 دقيقة.")
    logger.info("=========================================")
    
    last_scan_time = None
    
    client         = ExchangeClient()
    state_manager  = StateManager()
    notifier       = TelegramNotifier()
    
    api_status_monitor = APIStatusMonitor(
        client=client,
        state_manager=state_manager,
        notifier=notifier
    )
    last_api_health_check = 0
    
    # [فحوصات الأمان المبدئية - Startup Safety Checks]
    try:
        if not Config.USE_TESTNET and os.getenv("LIVE_TRADING_CONFIRMATION") != "I_ACCEPT_REAL_MONEY_RISK":
            raise RuntimeError("Live trading blocked: LIVE_TRADING_CONFIRMATION is missing or incorrect.")
            
        state_file_path = os.getenv("STATE_FILE", "bot_state.json")
        dir_path = os.path.dirname(os.path.abspath(state_file_path)) or "."
        if not os.access(dir_path, os.W_OK):
            raise RuntimeError(f"Volume path not writable: {dir_path}")
    except Exception as e:
        logger.critical(f"🛑 Startup Safety Check Failed: {e}")
        notifier.send_message(f"🛑 <b>فشل في فحص بيئة التشغيل:</b>\n{e}\nتم إيقاف البوت لحماية النظام.")
        raise
        
    hybrid_strategy= HybridStrategy()
    risk_manager   = RiskManager()
    trailing_manager = TrailingStopManager(state_manager=state_manager)
    alert_manager = TradeApproachAlertManager(state_manager=state_manager, notifier=notifier)
    news_engine    = NewsEngine()
    ai_engine      = AIEngine()
    ta_engine      = TAEngine()
    social_engine  = SocialEngine()
    universe_filter = UniverseFilter(client)
    derivatives_filter = DerivativesRiskFilter(state_manager=state_manager)
    
    # تشغيل نظام الاستماع لأوامر التيليجرام في الخلفية
    notifier.start_polling(client, state_manager, ai_engine, ta_engine, news_engine, social_engine)
    
    logger.info("🔄 جاري مزامنة الصفقات المفتوحة مع منصة باينانس...")
    try:
        live_positions = client.exchange.fetch_positions()
        live_symbols = set()
        for pos in live_positions:
            amt = float(pos.get('info', {}).get('positionAmt', 0))
            if amt != 0:
                sym = pos['symbol'].split(':')[0]
                live_symbols.add(sym)
        
        # دمج الصفقات الحية من المنصة مع الحالة المحفوظة
        saved_symbols = state_manager.get_active_symbols()
        all_active = saved_symbols.union(live_symbols)
        
        # تنظيف الصفقات المحفوظة التي تم إغلاقها يدوياً أو انتهت
        for sym in list(all_active):
            if sym not in live_symbols:
                all_active.discard(sym)
                logger.info(f"تم إزالة {sym} من السجل لأنه مغلق حالياً في باينانس.")
                
        state_manager.save_active_symbols(all_active)
        logger.info(f"✅ اكتملت المزامنة. الصفقات المفتوحة الفعلية: {all_active if all_active else 'لا يوجد'}")
    except Exception as e:
        logger.error(f"⚠️ فشل مزامنة الصفقات مع باينانس، سنعتمد على الملف المحلي: {e}")
        
    last_heartbeat_time = 0
    
    # حلقة التشغيل المستمرة
    while True:
        try:
            current_timestamp = time.time()
            # إرسال Heartbeat كل ساعتين للتيليجرام للتأكد من أن البوت حي
            if current_timestamp - last_heartbeat_time > 7200:
                mode_str = "TESTNET" if getattr(Config, 'USE_TESTNET', False) else "LIVE"
                active_count = len(state_manager.get_active_symbols())
                max_trades = getattr(Config, 'TESTNET_MAX_OPEN_TRADES', 1) if getattr(Config, 'USE_TESTNET', False) else getattr(Config, 'MAX_OPEN_TRADES', 1)
                
                notifier.send_message(f"✅ <b>Bot is alive & running</b>\nMode: <code>{mode_str}</code>\nOpen trades: {active_count}/{max_trades}\nState file: <code>{os.getenv('STATE_FILE', 'bot_state.json')}</code>")
                last_heartbeat_time = current_timestamp

            try:
                maybe_send_periodic_reports(notifier, state_manager, client)
            except Exception as e:
                logger.error(f"فشل فحص التقارير الدورية: {e}")

            check_network_connection(client)
            run_bot_iteration(
                client, hybrid_strategy, risk_manager, trailing_manager, state_manager, notifier, news_engine, 
                ai_engine, ta_engine, social_engine, universe_filter, derivatives_filter,
                last_scan_time
            )
                
            last_scan_time = int(time.time() * 1000)
        except SystemExit:
            raise # نمرر الإغلاق للخروج من البوت بشكل كامل
        except Exception as e:
            logger.error(f"❌ حدث خطأ غير متوقع أثناء دورة المسح: {e}")
            
        scan_interval = int(
            state_manager.get(
                "scan_interval_seconds",
                getattr(Config, "SCAN_INTERVAL_SECONDS", 600)
            )
        )

        scan_interval = max(
            getattr(Config, "MIN_SCAN_INTERVAL_SECONDS", 300),
            min(scan_interval, getattr(Config, "MAX_SCAN_INTERVAL_SECONDS", 3600))
        )

        monitor_tick = getattr(Config, "MONITOR_TICK_SECONDS", 10)

        logger.info(
            f"⏳ انتهت جولة المسح. مراقبة مستمرة لمدة {scan_interval} ثانية "
            f"قبل الجولة التالية..."
        )

        end_time = time.time() + scan_interval
        
        # وقت آخر فحص للصفقات المفتوحة (دقيق لتجنب فجوات الوقت)
        monitor_last_time = int(time.time() * 1000)

        while time.time() < end_time:
            if state_manager.get("force_scan_now", False):
                logger.info("⚡ تم طلب مسح فوري من تيليجرام. الخروج من وضع المراقبة.")
                state_manager.set("force_scan_now", False)
                break
                
            try:
                check_network_connection(client)
                monitor_virtual_orders(client, state_manager, notifier, trailing_manager, risk_manager)
                manage_partial_tp_runner(client, state_manager, trailing_manager, notifier)
                monitor_last_time = monitor_active_trades(
                    client,
                    state_manager,
                    trailing_manager,
                    alert_manager,
                    notifier,
                    monitor_last_time
                )
                maybe_send_periodic_reports(notifier, state_manager, client)
            except Exception as e:
                logger.error(f"خطأ أثناء وضع المراقبة بين جولات المسح: {e}")

            if getattr(Config, "API_HEALTH_CHECK_ENABLED", True):
                interval_sec = getattr(Config, "API_HEALTH_CHECK_INTERVAL_MINUTES", 60) * 60
                if time.time() - last_api_health_check >= interval_sec:
                    last_api_health_check = time.time()

                    def _api_health_job():
                        try:
                            logger.info("🧪 بدء فحص دوري لكل خدمات API...")
                            payload = api_status_monitor.run_full_check()
                            api_status_monitor.notify_if_needed(payload)
                        except Exception as e:
                            logger.error(f"فشل الفحص الدوري لخدمات API: {e}")

                    threading.Thread(target=_api_health_job, daemon=True).start()

            time.sleep(monitor_tick)

if __name__ == "__main__":
    main()
