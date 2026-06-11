import pandas as pd
import ta
import numpy as np
from core.logger import logger
from core.config import Config

# ---------------------------------------------------------------
# HTF Trend Cache: نخزن الترند العلوي مؤقتاً لتجنب استدعاءات API متكررة
# ---------------------------------------------------------------
_htf_cache: dict = {}  # {symbol: {"trend": str, "ts": float}}
HTF_CACHE_TTL_SECONDS = 900  # 15 دقيقة = عمر الشمعة الـ 1H

class TAEngine:
    def __init__(self):
        pass

    def add_indicators(self, df: pd.DataFrame):
        """
        يضيف مؤشرات التحليل الفني ومفاهيم المال الذكي (SMC) المتقدمة.
        """
        if df is None or df.empty:
            logger.warning("لا توجد بيانات كافية لحساب المؤشرات.")
            return df
            
        try:
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                
            # --- المؤشرات الكلاسيكية الأساسية ---
            df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
            macd = ta.trend.MACD(close=df['close'])
            df['macd'] = macd.macd()
            df['macd_signal'] = macd.macd_signal()
            df['ema_50'] = ta.trend.EMAIndicator(close=df['close'], window=50).ema_indicator()
            df['ema_200'] = ta.trend.EMAIndicator(close=df['close'], window=200).ema_indicator()
            df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
            
            # --- إعدادات مفاهيم المال الذكي (SMC) ---
            # 1. تحليل حجم الشمعة (Displacement)
            df['body'] = (df['close'] - df['open']).abs()
            df['range'] = (df['high'] - df['low']).replace(0, np.nan)
            df['body_avg_20'] = df['body'].rolling(20).mean()
            
            # 2. هيكل السوق (Swing High / Swing Low)
            lookback = 5
            df['prior_swing_high'] = df['high'].shift(1).rolling(lookback).max()
            df['prior_swing_low'] = df['low'].shift(1).rolling(lookback).min()
            
            # ------------------------------------------------------------
            # Liquidity Sweep Detection
            # ------------------------------------------------------------
            liq_lookback = getattr(Config, "LIQUIDITY_SWEEP_LOOKBACK", 10)
            sweep_recent_bars = getattr(Config, "LIQUIDITY_SWEEP_RECENT_BARS", 8)

            # قاع/قمة سابقة قبل الشمعة الحالية
            df["liq_prior_low"] = df["low"].shift(1).rolling(liq_lookback).min()
            df["liq_prior_high"] = df["high"].shift(1).rolling(liq_lookback).max()

            # Sweep للسيولة السفلية: كسر قاع سابق ثم إغلاق فوقه
            df["sell_side_sweep"] = (
                (df["low"] < df["liq_prior_low"]) &
                (df["close"] > df["liq_prior_low"])
            )

            # Sweep للسيولة العلوية: كسر قمة سابقة ثم إغلاق تحتها
            df["buy_side_sweep"] = (
                (df["high"] > df["liq_prior_high"]) &
                (df["close"] < df["liq_prior_high"])
            )

            # نطلب أن يكون السويب قريبًا من شمعة الـ displacement وليس قديمًا جدًا
            df["recent_sell_side_sweep"] = (
                df["sell_side_sweep"]
                .shift(1)
                .rolling(sweep_recent_bars)
                .max()
                .fillna(0)
                .astype(bool)
            )

            df["recent_buy_side_sweep"] = (
                df["buy_side_sweep"]
                .shift(1)
                .rolling(sweep_recent_bars)
                .max()
                .fillna(0)
                .astype(bool)
            )

            # 3. شروط الزخم والاندفاع (Displacement)
            df['strong_body'] = df['body'] > df['body_avg_20'] * 1.5
            df['close_near_high'] = ((df['close'] - df['low']) / df['range']) > 0.7
            df['close_near_low'] = ((df['high'] - df['close']) / df['range']) > 0.7
            
            # 4. تحديد أنواع الشموع السابقة
            prev_bearish = df['close'].shift(1) < df['open'].shift(1)
            prev_bullish = df['close'].shift(1) > df['open'].shift(1)
            
            # 5. كسر الهيكل (Break of Structure - BOS)
            bullish_bos = df['close'] > df['prior_swing_high']
            bearish_bos = df['close'] < df['prior_swing_low']
            
            # 6. كتل الأوامر (Order Blocks)
            require_sweep = getattr(Config, "REQUIRE_LIQUIDITY_SWEEP_FOR_OB", True)

            base_bullish_ob_cond = (
                prev_bearish &
                (df["close"] > df["open"]) &
                df["strong_body"] &
                df["close_near_high"] &
                bullish_bos
            )

            base_bearish_ob_cond = (
                prev_bullish &
                (df["close"] < df["open"]) &
                df["strong_body"] &
                df["close_near_low"] &
                bearish_bos
            )

            if require_sweep:
                bullish_ob_cond = base_bullish_ob_cond & df["recent_sell_side_sweep"]
                bearish_ob_cond = base_bearish_ob_cond & df["recent_buy_side_sweep"]
            else:
                bullish_ob_cond = base_bullish_ob_cond
                bearish_ob_cond = base_bearish_ob_cond
            
            # 7. تخزين مناطق الـ Order Blocks (Zones)
            df['ob_bullish_low'] = np.nan
            df['ob_bullish_high'] = np.nan
            df['ob_bearish_low'] = np.nan
            df['ob_bearish_high'] = np.nan
            
            df.loc[bullish_ob_cond, 'ob_bullish_low'] = df['low'].shift(1)
            df.loc[bullish_ob_cond, 'ob_bullish_high'] = df['open'].shift(1)
            
            df.loc[bearish_ob_cond, 'ob_bearish_low'] = df['open'].shift(1)
            df.loc[bearish_ob_cond, 'ob_bearish_high'] = df['high'].shift(1)
            
            # التعبئة للأمام
            df['ob_bullish_low'] = df['ob_bullish_low'].ffill()
            df['ob_bullish_high'] = df['ob_bullish_high'].ffill()
            df['ob_bearish_low'] = df['ob_bearish_low'].ffill()
            df['ob_bearish_high'] = df['ob_bearish_high'].ffill()
            
            # Mid levels
            df['ob_bullish_mid'] = (df['ob_bullish_low'] + df['ob_bullish_high']) / 2
            df['ob_bearish_mid'] = (df['ob_bearish_low'] + df['ob_bearish_high']) / 2
            
            # 8. الفجوات السعرية (Fair Value Gaps - FVG)
            df['fvg_bullish_low'] = np.nan
            df['fvg_bullish_high'] = np.nan
            fvg_bullish_cond = df['low'] > df['high'].shift(2)
            df.loc[fvg_bullish_cond, 'fvg_bullish_low'] = df['high'].shift(2)
            df.loc[fvg_bullish_cond, 'fvg_bullish_high'] = df['low']
            
            df['fvg_bearish_low'] = np.nan
            df['fvg_bearish_high'] = np.nan
            fvg_bearish_cond = df['high'] < df['low'].shift(2)
            df.loc[fvg_bearish_cond, 'fvg_bearish_low'] = df['high']
            df.loc[fvg_bearish_cond, 'fvg_bearish_high'] = df['low'].shift(2)
            
            df['fvg_bullish_low'] = df['fvg_bullish_low'].ffill()
            df['fvg_bullish_high'] = df['fvg_bullish_high'].ffill()
            df['fvg_bearish_low'] = df['fvg_bearish_low'].ffill()
            df['fvg_bearish_high'] = df['fvg_bearish_high'].ffill()

            # --- OB Age & Mitigation Tracking ---
            # ✅ إصلاح: نتتبع عمر الـ OB ونضع علامة Mitigated
            # عندما يُغلق السعر داخل منطقة الـ OB (أو خلفها) تُعتبر مستهلكة ولا تُطلق إشارة جديدة
            df['bullish_ob_created'] = bullish_ob_cond
            df['bearish_ob_created'] = bearish_ob_cond

            ages_bullish = []
            ages_bearish = []
            mitigated_bullish = []
            mitigated_bearish = []
            last_bull_idx = None
            last_bear_idx = None
            bull_mitigated = False
            bear_mitigated = False

            for i in range(len(df)):
                close_i = df['close'].iloc[i]

                # Bullish OB
                if df['bullish_ob_created'].iloc[i]:
                    last_bull_idx = i
                    bull_mitigated = False  # OB جديد = غير مستهلك
                    ages_bullish.append(0)
                elif last_bull_idx is not None:
                    ages_bullish.append(i - last_bull_idx)
                    # إذا أغلق السعر تحت أدنى نقطة في منطقة الـ OB → مستهلك
                    ob_low = df['ob_bullish_low'].iloc[i]
                    if pd.notna(ob_low) and close_i < ob_low:
                        bull_mitigated = True
                else:
                    ages_bullish.append(np.nan)
                mitigated_bullish.append(bull_mitigated)

                # Bearish OB
                if df['bearish_ob_created'].iloc[i]:
                    last_bear_idx = i
                    bear_mitigated = False  # OB جديد = غير مستهلك
                    ages_bearish.append(0)
                elif last_bear_idx is not None:
                    ages_bearish.append(i - last_bear_idx)
                    # إذا أغلق السعر فوق أعلى نقطة في منطقة الـ OB → مستهلك
                    ob_high = df['ob_bearish_high'].iloc[i]
                    if pd.notna(ob_high) and close_i > ob_high:
                        bear_mitigated = True
                else:
                    ages_bearish.append(np.nan)
                mitigated_bearish.append(bear_mitigated)

            df['ob_bullish_age'] = ages_bullish
            df['ob_bearish_age'] = ages_bearish
            df['ob_bullish_mitigated'] = mitigated_bullish
            df['ob_bearish_mitigated'] = mitigated_bearish

            logger.debug("تم حساب SMC بشكل متقدم (OB Zones, Displacement, FVG, BOS, Age, Mitigation).")
            return df
            
        except Exception as e:
            logger.error(f"خطأ في حساب المؤشرات الفنية: {e}")
            return df
            
    def get_htf_trend(self, symbol: str, client) -> str:
        """
        ✅ جديد: فلتر الترند العلوي (Higher TimeFrame Confirmation)
        يجلب بيانات 1H ويقيّم الاتجاه بناءً على EMA50 و EMA200.
        يُستخدم كفيتو: لا ندخل LONG على 15m إذا كان 1H هابطاً والعكس.
        النتيجة مخزّنة في cache لمدة 15 دقيقة لتجنب استدعاءات API متكررة.
        """
        import time
        now = time.time()
        cached = _htf_cache.get(symbol)
        if cached and (now - cached["ts"]) < HTF_CACHE_TTL_SECONDS:
            return cached["trend"]
        try:
            bars_1h = client.fetch_ohlcv(symbol, timeframe='1h', limit=60)
            if bars_1h is None or len(bars_1h) < 40:
                return "neutral"
            df_htf = bars_1h.copy()
            df_htf['ema_50']  = ta.trend.EMAIndicator(close=df_htf['close'], window=50).ema_indicator()
            df_htf['ema_200'] = ta.trend.EMAIndicator(close=df_htf['close'], window=200).ema_indicator()
            last_htf = df_htf.iloc[-2]
            ema50  = last_htf.get('ema_50',  0)
            ema200 = last_htf.get('ema_200', 0)
            close  = last_htf.get('close',   0)
            if ema50 > ema200 and close > ema50:
                trend = "bullish"
            elif ema50 < ema200 and close < ema50:
                trend = "bearish"
            else:
                trend = "neutral"
            _htf_cache[symbol] = {"trend": trend, "ts": now}
            logger.debug(f"[HTF 1H] {symbol}: EMA50={ema50:.4f} | EMA200={ema200:.4f} --> Trend={trend.upper()}")
            return trend
        except Exception as e:
            logger.warning(f"[HTF] فشل جلب بيانات 1H لـ {symbol}: {e}")
            return "neutral"

    def evaluate_trend(self, df: pd.DataFrame, symbol="Unknown", details: dict = None, htf_trend: str = "neutral"):
        """
        تقييم مناطق الدخول الاستراتيجية بناءً على القواعد الاحترافية.
        htf_trend: الترند من الإطار الزمني الأعلى (1H) — يُستخدم كفيتو.
        """
        تقييم مناطق الدخول الاستراتيجية بناءً على القواعد الاحترافية للصديق המبرمج.
        """
        if df is None or len(df) < 25:
            if details is not None: details.update({'score': 0, 'reason': 'بيانات غير كافية'})
            return "neutral"
            
        # نستخدم الشمعة المغلقة فقط!
        latest = df.iloc[-2]
        
        close = latest.get('close', 0)
        atr = latest.get('atr', 0)
        ema_200 = latest.get('ema_200', 0)
        ema_50 = latest.get('ema_50', 0)
        
        # OB Zones
        ob_bullish_low = latest.get('ob_bullish_low', 0)
        ob_bullish_high = latest.get('ob_bullish_high', 0)
        ob_bullish_mid = latest.get('ob_bullish_mid', 0)
        
        ob_bearish_low = latest.get('ob_bearish_low', 0)
        ob_bearish_high = latest.get('ob_bearish_high', 0)
        ob_bearish_mid = latest.get('ob_bearish_mid', 0)
        
        ob_bullish_age = latest.get('ob_bullish_age', 999)
        ob_bearish_age = latest.get('ob_bearish_age', 999)
        max_ob_age = 80 # 80 شمعة 15 دقيقة = 20 ساعة تقريباً
        
        # Invalidation Check (لمنع الدخول في مناطق مكسورة أو مستهلكة بشدة أو قديمة جداً)
        # إذا السعر الحالي أغلق تحت المنطقة، نعتبرها مكسورة ولا نتداول عليها
        # --- OB Mitigation Check ---
        ob_bullish_mitigated = bool(latest.get('ob_bullish_mitigated', False))
        ob_bearish_mitigated = bool(latest.get('ob_bearish_mitigated', False))

        # ✅ صحة OB: موجودة + في نطاق العمر + غير مستهلكة
        valid_bullish_ob = (
            (ob_bullish_low > 0)
            and (close >= ob_bullish_low * 0.995)
            and (ob_bullish_age <= max_ob_age)
            and not ob_bullish_mitigated
        )
        valid_bearish_ob = (
            (ob_bearish_high > 0)
            and (close <= ob_bearish_high * 1.005)
            and (ob_bearish_age <= max_ob_age)
            and not ob_bearish_mitigated
        )

        score = 0
        reason = "انتظار وصول السعر لمنطقة OB نشطة وحديثة وغير مستهلكة"
        trend_result = "neutral"

        # 1. تقييم الاتجاه العام (15m)
        is_uptrend_15m = ema_50 > ema_200 if (ema_50 > 0 and ema_200 > 0) else True

        # ✅ جديد: فيتو HTF — لا نشتري إذا كان الترند العلوي هابطاً
        htf_allows_buy  = htf_trend != "bearish"   # neutral أو bullish: مسموح
        htf_allows_sell = htf_trend != "bullish"   # neutral أو bearish: مسموح

        # 2. الشراء (LONG)
        if is_uptrend_15m and valid_bullish_ob and htf_allows_buy:
            distance_to_ob = abs(close - ob_bullish_high)
            if distance_to_ob <= min(close * 0.02, atr * 0.8) or (close <= ob_bullish_high and close >= ob_bullish_low):
                score = 3
                trend_result = "buy"
                htf_note = f" | HTF={htf_trend.upper()}" if htf_trend != "neutral" else ""
                reason = f"إشارة قنص (Smart Money LONG) - السعر عند OB صاعد (Zone){htf_note}"
                optimal_entry = ob_bullish_mid
                invalidation_level = ob_bullish_low

        # 3. البيع (SHORT)
        elif not is_uptrend_15m and valid_bearish_ob and htf_allows_sell:
            distance_to_ob = abs(close - ob_bearish_low)
            if distance_to_ob <= min(close * 0.02, atr * 0.8) or (close >= ob_bearish_low and close <= ob_bearish_high):
                score = 3
                trend_result = "sell"
                htf_note = f" | HTF={htf_trend.upper()}" if htf_trend != "neutral" else ""
                reason = f"إشارة قنص (Smart Money SHORT) - السعر عند OB هابط (Zone){htf_note}"
                optimal_entry = ob_bearish_mid
                invalidation_level = ob_bearish_high

        # سبب الرفض بسبب HTF (للتشخيص)
        if score == 0 and is_uptrend_15m and valid_bullish_ob and not htf_allows_buy:
            reason = f"رُفض LONG بسبب HTF هابط (1H={htf_trend.upper()}) رغم وجود OB صاعد على 15m"
        elif score == 0 and not is_uptrend_15m and valid_bearish_ob and not htf_allows_sell:
            reason = f"رُفض SHORT بسبب HTF صاعد (1H={htf_trend.upper()}) رغم وجود OB هابط على 15m"
                    
        if details is not None:
            market_levels = {
                "bearish_obs": [{"low": float(ob_bearish_low), "high": float(ob_bearish_high)}],
                "bullish_obs": [{"low": float(ob_bullish_low), "high": float(ob_bullish_high)}],
                "bearish_fvgs": [{"low": float(latest.get('fvg_bearish_low', 0)), "high": float(latest.get('fvg_bearish_high', 0))}],
                "bullish_fvgs": [{"low": float(latest.get('fvg_bullish_low', 0)), "high": float(latest.get('fvg_bullish_high', 0))}],
            }
            details.update({
                'score': score,
                'close': close,
                'atr': atr,
                'reason': reason,
                'support': ob_bullish_high if pd.notna(ob_bullish_high) else 0,
                'resistance': ob_bearish_low if pd.notna(ob_bearish_low) else 0,
                'optimal_entry': locals().get('optimal_entry', close),
                'invalidation_level': locals().get('invalidation_level', close),
                'ob_mid': locals().get('optimal_entry', close),
                'market_levels': market_levels,
                'ema_200': ema_200,
                'ema_50': ema_50,
                'adx': latest.get('adx', 0),
                'rsi': latest.get('rsi', 0),
                'htf_trend': htf_trend,                                           # ✅ جديد
                'ob_bullish_mitigated': ob_bullish_mitigated,                     # ✅ جديد
                'ob_bearish_mitigated': ob_bearish_mitigated,                     # ✅ جديد
                "sell_side_sweep": bool(latest.get("sell_side_sweep", False)),
                "buy_side_sweep": bool(latest.get("buy_side_sweep", False)),
                "recent_sell_side_sweep": bool(latest.get("recent_sell_side_sweep", False)),
                "recent_buy_side_sweep": bool(latest.get("recent_buy_side_sweep", False))
            })
            
        return trend_result

    def analyze(self, symbol: str, client) -> dict:
        try:
            bars = client.fetch_ohlcv(symbol, timeframe='15m', limit=150)
            if bars is None or len(bars) < 50:
                return {}
            df = self.add_indicators(bars)
            details = {}
            # ✅ جديد: جلب الترند العلوي (1H) أولاً ثم تمريره لـ evaluate_trend
            htf_trend = self.get_htf_trend(symbol, client)
            trend = self.evaluate_trend(df, symbol=symbol, details=details, htf_trend=htf_trend)
            latest = df.iloc[-2]
            market_levels = {
                "bearish_obs": [{"low": float(latest.get('ob_bearish_low', 0)), "high": float(latest.get('ob_bearish_high', 0))}],
                "bullish_obs": [{"low": float(latest.get('ob_bullish_low', 0)), "high": float(latest.get('ob_bullish_high', 0))}],
                "bearish_fvgs": [{"low": float(latest.get('fvg_bearish_low', 0)), "high": float(latest.get('fvg_bearish_high', 0))}],
                "bullish_fvgs": [{"low": float(latest.get('fvg_bullish_low', 0)), "high": float(latest.get('fvg_bullish_high', 0))}],
            }
            
            return {
                "trend": trend,
                "score": details.get("score", 0),
                "reason": details.get("reason", ""),
                "price": latest.get("close", 0),
                "rsi": latest.get("rsi", 0),
                "macd": latest.get("macd", 0),
                "ema_200": latest.get("ema_200", 0),
                "adx": latest.get("adx", 0),
                "support": details.get("support", 0),
                "resistance": details.get("resistance", 0),
                "atr": latest.get("atr", 0),
                "market_levels": market_levels
            }
        except Exception as e:
            logger.error(f"خطأ في دالة analyze للزوج {symbol}: {e}")
            return {}
