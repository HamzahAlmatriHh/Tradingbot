import pandas as pd
import ta
import numpy as np
from core.logger import logger
from core.config import Config

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
            # Bullish OB: آخر شمعة هابطة قبل displacement صاعد يكسر قمة سابقة
            bullish_ob_cond = prev_bearish & (df['close'] > df['open']) & df['strong_body'] & df['close_near_high'] & bullish_bos
            
            # Bearish OB: آخر شمعة صاعدة قبل displacement هابط يكسر قاع سابق
            bearish_ob_cond = prev_bullish & (df['close'] < df['open']) & df['strong_body'] & df['close_near_low'] & bearish_bos
            
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

            logger.debug("تم حساب SMC بشكل متقدم (OB Zones, Displacement, FVG, BOS).")
            return df
            
        except Exception as e:
            logger.error(f"خطأ في حساب المؤشرات الفنية: {e}")
            return df
            
    def evaluate_trend(self, df: pd.DataFrame, symbol="Unknown", details: dict = None):
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
        
        # Invalidation Check (لمنع الدخول في مناطق مكسورة أو مستهلكة بشدة)
        # إذا السعر الحالي أغلق تحت المنطقة، نعتبرها مكسورة ولا نتداول عليها
        valid_bullish_ob = (ob_bullish_low > 0) and (close >= ob_bullish_low * 0.995)
        valid_bearish_ob = (ob_bearish_high > 0) and (close <= ob_bearish_high * 1.005)
        
        score = 0
        reason = "انتظار وصول السعر لمنطقة OB نشطة"
        trend_result = "neutral"
        
        # 1. تقييم الاتجاه العام
        is_uptrend = ema_50 > ema_200 if (ema_50 > 0 and ema_200 > 0) else True
        
        # 2. الشراء (LONG)
        if is_uptrend and valid_bullish_ob:
            # المسافة إلى أعلى المنطقة (بداية الدخول)
            distance_to_ob = abs(close - ob_bullish_high)
            
            # تقنية ATR بدلاً من 2% ثابتة (حسب اقتراح الصديق)
            # نعتبر السعر قريب إذا كان ضمن مسافة قريبة تعتمد على التذبذب
            if distance_to_ob <= min(close * 0.02, atr * 0.8) or (close <= ob_bullish_high and close >= ob_bullish_low):
                score = 3
                trend_result = "buy"
                reason = "إشارة قنص (Smart Money LONG) - السعر عند OB صاعد (Zone)"
                optimal_entry = ob_bullish_mid # الدخول المثالي من منتصف المنطقة
                invalidation_level = ob_bullish_low - (atr * 0.5) # الستوب أسفل المنطقة + مسافة أمان
                    
        # 3. البيع (SHORT)
        elif not is_uptrend and valid_bearish_ob:
            distance_to_ob = abs(close - ob_bearish_low)
            
            if distance_to_ob <= min(close * 0.02, atr * 0.8) or (close >= ob_bearish_low and close <= ob_bearish_high):
                score = 3
                trend_result = "sell"
                reason = "إشارة قنص (Smart Money SHORT) - السعر عند OB هابط (Zone)"
                optimal_entry = ob_bearish_mid # الدخول المثالي من منتصف المنطقة
                invalidation_level = ob_bearish_high + (atr * 0.5) # الستوب أعلى المنطقة + مسافة أمان
                    
        if details is not None:
            details.update({
                'score': score,
                'close': close,
                'atr': atr,
                'reason': reason,
                'support': ob_bullish_high if pd.notna(ob_bullish_high) else 0, # نرسلها كدعم ليتم استخدامها في السجل
                'resistance': ob_bearish_low if pd.notna(ob_bearish_low) else 0,
                'optimal_entry': locals().get('optimal_entry', close),
                'invalidation_level': locals().get('invalidation_level', close),
                'ob_mid': locals().get('optimal_entry', close) # لإستخدامها في main.py
            })
            
        return trend_result

    def analyze(self, symbol: str, client) -> dict:
        try:
            bars = client.fetch_ohlcv(symbol, timeframe='15m', limit=150)
            if bars is None or len(bars) < 50:
                return {}
            df = self.add_indicators(bars)
            details = {}
            trend = self.evaluate_trend(df, symbol=symbol, details=details)
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
