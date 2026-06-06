import pandas as pd
import numpy as np
from core.logger import logger

class TrailingStopManager:
    """
    نظام الستوب المتحرك المتقدم (SMC Hybrid Trailing Stop)
    يعتمد على: R-Multiple + ATR Buffer + Swing Structure
    """
    def __init__(self, state_manager=None):
        self._state_manager = state_manager
        
        # R-based activation
        self.break_even_at_r = 1.0
        self.structure_trailing_at_r = 1.5
        self.atr_tight_trailing_at_r = 3.0

        # ATR buffers
        self.structure_atr_buffer = 0.3
        self.atr_trail_multiplier_normal = 2.5
        self.atr_trail_multiplier_tight = 1.5

        # الحد الأدنى لتحديث الستوب لتقليل ضغط API (0.1%)
        self.min_sl_update_pct = 0.001  
        
        # ذاكرة الصفقات المفتوحة
        self._trades: dict = {}
        
        # استعادة الحالة
        if state_manager:
            saved = state_manager.get_trailing_trades()
            if saved:
                self._trades = saved
                logger.info(f"[Trailing] تم استعادة حالة {len(saved)} صفقة متتبعة.")

    def register_trade(self, symbol: str, side: str, entry_price: float, initial_sl: float):
        """تسجيل صفقة جديدة في نظام التتبع"""
        self._trades[symbol] = {
            'side': side.lower(),
            'entry': float(entry_price),
            'best_price': float(entry_price),
            'current_sl': float(initial_sl),
            'initial_sl': float(initial_sl),
            'trailing_active': False,
        }
        if self._state_manager:
            self._state_manager.save_trailing_trades(self._trades)
        logger.info(f"[Trailing] ✅ تم تسجيل صفقة {symbol} ({side.upper()}) | دخول: {entry_price} | SL أولي: {initial_sl}")

    def unregister_trade(self, symbol: str):
        """إلغاء تتبع الصفقة بعد إغلاقها"""
        if symbol in self._trades:
            del self._trades[symbol]
            if self._state_manager:
                self._state_manager.save_trailing_trades(self._trades)

    def calculate_atr(self, df: pd.DataFrame, period=14):
        high = df['high']
        low = df['low']
        close = df['close']
        prev_close = close.shift(1)
        
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-2]  # نأخذ الشمعة المغلقة
        return atr

    def get_confirmed_swing(self, df: pd.DataFrame, side: str, left=2, right=2):
        """
        يرجع آخر swing low مؤكد للشراء أو swing high مؤكد للبيع.
        """
        if len(df) < left + right + 5:
            return None

        # الشموع المغلقة فقط
        max_i = len(df) - right - 1 
        swings = []

        if side == 'buy':
            lows = df['low'].values
            for i in range(left, max_i):
                center = lows[i]
                if center < np.min(lows[i-left:i]) and center < np.min(lows[i+1:i+1+right]):
                    swings.append(center)
        else:
            highs = df['high'].values
            for i in range(left, max_i):
                center = highs[i]
                if center > np.max(highs[i-left:i]) and center > np.max(highs[i+1:i+1+right]):
                    swings.append(center)

        return swings[-1] if swings else None

    def update(self, symbol: str, current_price: float, client) -> float | None:
        """يُستدعى لفحص وتحديث الستوب المتحرك إذا لزم الأمر."""
        if symbol not in self._trades:
            return None

        trade = self._trades[symbol]
        side = trade['side']
        entry = trade['entry']
        current_sl = trade['current_sl']
        initial_sl = trade['initial_sl']

        # تحديث أفضل سعر
        if side == 'buy':
            best_price = max(trade.get('best_price', entry), current_price)
        else:
            best_price = min(trade.get('best_price', entry), current_price)
            
        trade['best_price'] = best_price

        # حساب المخاطرة الأساسية
        risk = abs(entry - initial_sl)
        if risk <= 0:
            return None

        # حساب مضاعف العائد (R-Multiple)
        if side == 'buy':
            best_r = (best_price - entry) / risk
        else:
            best_r = (entry - best_price) / risk

        # لا نحرك الستوب قبل 1R
        if best_r < self.break_even_at_r:
            return None
            
        if not trade['trailing_active']:
            trade['trailing_active'] = True
            logger.info(f"[Trailing] 🟡 تفعيل الستوب المتحرك لـ {symbol} | أفضل R وصل له: {best_r:.2f}R")

        # جلب بيانات الشموع
        try:
            bars_df = client.fetch_ohlcv(symbol, timeframe='15m', limit=50)
            if bars_df is None or len(bars_df) < 20:
                return None
        except Exception as e:
            logger.error(f"[Trailing] خطأ في جلب بيانات الشموع للزوج {symbol}: {e}")
            return None

        atr = self.calculate_atr(bars_df)
        candidates = []

        if side == 'buy':
            # 1) Breakeven + Slippage
            candidates.append(entry * 1.001)

            # 2) Structure trailing بعد 1.5R
            if best_r >= self.structure_trailing_at_r:
                last_swing_low = self.get_confirmed_swing(bars_df, 'buy')
                if last_swing_low:
                    candidates.append(last_swing_low - atr * self.structure_atr_buffer)

            # 3) ATR trailing
            multiplier = self.atr_trail_multiplier_tight if best_r >= self.atr_tight_trailing_at_r else self.atr_trail_multiplier_normal
            candidates.append(best_price - atr * multiplier)

            new_sl = max(candidates)
            
            # لا تسمح بتراجع الستوب
            if new_sl <= current_sl:
                return None
                
            # تحديث فقط إذا كان الفرق يستحق
            if (new_sl - current_sl) / current_sl < self.min_sl_update_pct:
                return None
                
        else: # sell
            # 1) Breakeven + Slippage
            candidates.append(entry * 0.999)

            # 2) Structure trailing بعد 1.5R
            if best_r >= self.structure_trailing_at_r:
                last_swing_high = self.get_confirmed_swing(bars_df, 'sell')
                if last_swing_high:
                    candidates.append(last_swing_high + atr * self.structure_atr_buffer)

            # 3) ATR trailing
            multiplier = self.atr_trail_multiplier_tight if best_r >= self.atr_tight_trailing_at_r else self.atr_trail_multiplier_normal
            candidates.append(best_price + atr * multiplier)

            new_sl = min(candidates)
            
            # لا تسمح بتراجع الستوب
            if new_sl >= current_sl:
                return None
                
            # تحديث فقط إذا كان الفرق يستحق
            if (current_sl - new_sl) / current_sl < self.min_sl_update_pct:
                return None

        # تطبيق التحديث على المنصة
        try:
            # تقريب السعر لمنع أخطاء باينانس
            new_sl = round(new_sl, 5) # افتراضي مؤقتا، الأفضل ربطه بـ Price Precision
            amount = client.get_position_amount(symbol)
            if amount and amount > 0:
                logger.info(f"[Trailing] محاولة وضع ستوب جديد لـ {symbol} عند {new_sl}...")
                # نلغي القديم أولاً لتحرير الرصيد في حسابات معينة
                client.cancel_sl_orders(symbol)
                ok_new = client.place_sl_only(symbol, side.upper(), amount, new_sl)
                
                if ok_new:
                    trade['current_sl'] = new_sl
                    if self._state_manager:
                        self._state_manager.save_trailing_trades(self._trades)
                    logger.info(f"[Trailing] 📈 تحديث SL لـ {symbol} | R: {best_r:.2f} | القديم: {current_sl:.5f} → الجديد: {new_sl:.5f}")
                    return new_sl
                else:
                    logger.critical(f"[Trailing] ⚠️ خطير: فشل وضع الستوب الجديد! جاري محاولة إرجاع الستوب القديم: {current_sl}")
                    restored = client.place_sl_only(symbol, side.upper(), amount, current_sl)
                    if not restored:
                        logger.critical(f"[Trailing] 🚨 فشل إرجاع الستوب القديم لصفقة {symbol}! التدخل اليدوي مطلوب فوراً.")
                        try:
                            from utils.telegram_bot import TelegramNotifier
                            notifier = TelegramNotifier()
                            notifier.send_message(f"🚨 <b>تحذير طارئ جداً (Trailing Stop Failure)</b>\nالزوج: <code>{symbol}</code>\nفشل البوت في استرجاع الوقف القديم بعد إلغائه. الصفقة الآن بدون حماية (Naked Position)!\nتم إرسال أمر إغلاق (Market) طارئ لحماية الحساب.")
                            
                            # نغلق الصفقة بسعر السوق فوراً لعدم تركها بدون حماية
                            client.close_position(symbol, side.lower(), amount)
                            logger.critical(f"تم إرسال أمر إغلاق ماركت طارئ للصفقة {symbol} لحمايتها.")
                        except Exception as ex:
                            logger.error(f"فشل إرسال تنبيه الطوارئ أو إغلاق الصفقة: {ex}")
                            
                    return None
        except Exception as e:
            logger.error(f"[Trailing] فشل وضع أمر SL جديد لـ {symbol}: {e}")

        return None

    def get_trade_info(self, symbol: str) -> dict | None:
        return self._trades.get(symbol)

    def has_trade(self, symbol: str) -> bool:
        return symbol in self._trades

    def get_current_sl(self, symbol: str) -> float | None:
        trade = self._trades.get(symbol)
        return trade.get('current_sl') if trade else None
