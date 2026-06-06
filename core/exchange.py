import ccxt
import pandas as pd
from core.config import Config
from core.logger import logger

class ExchangeClient:
    def __init__(self):
        # استخدام ccxt للاتصال بمنصة باينانس
        self.exchange = ccxt.binance({
            'apiKey': Config.BINANCE_API_KEY,
            'secret': Config.BINANCE_API_SECRET,
            'enableRateLimit': True, # احترام قيود الـ Rate Limits من باينانس لتجنب الحظر
            'timeout': 30000, # مهلة الاتصال 30 ثانية للتغلب على ضعف الشبكة اللحظي
            'options': {
                'defaultType': 'future', # العمل على سوق العقود الآجلة
            }
        })
        
        # تفعيل Testnet (Demo Trading الجديد) في حالة كان مفعلاً في الإعدادات
        if Config.USE_TESTNET:
            try:
                # التحديث الجديد لـ ccxt يتطلب استخدام enable_demo_trading للعقود الآجلة
                self.exchange.enable_demo_trading(True)
                logger.info("تم تشغيل وضع Demo Trading الجديد لباينانس بنجاح.")
            except AttributeError:
                # كحل بديل إذا كان إصدار ccxt أقدم من 4.5.6
                self.exchange.set_sandbox_mode(True)
                logger.info("تم تشغيل وضع Sandbox (القديم) لباينانس.")

    def fetch_ohlcv(self, symbol, timeframe='15m', limit=100):
        """
        جلب بيانات الشموع (Open, High, Low, Close, Volume)
        مع معالجة الاستثناءات في حالة فشل الاتصال.
        """
        try:
            logger.debug(f"جاري جلب بيانات الشموع لـ {symbol} بإطار {timeframe}")
            bars = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            
            import pandas as pd
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except ccxt.NetworkError as e:
            logger.error(f"خطأ في الشبكة أثناء جلب بيانات الشموع: {e}")
        except ccxt.ExchangeError as e:
            logger.error(f"خطأ من منصة باينانس: {e}")
        except Exception as e:
            logger.critical(f"خطأ غير متوقع في جلب الشموع: {e}")
        return None
        
    def get_balance(self):
        """
        جلب رصيد المحفظة.
        """
        try:
            balance = self.exchange.fetch_balance()
            return balance
        except ccxt.AuthenticationError as e:
            logger.error(f"خطأ في المصادقة (تأكد من الـ API Keys): {e}")
        except Exception as e:
            logger.error(f"فشل في جلب الرصيد: {e}")
        return None

    def get_top_volatile_symbols(self, limit=15):
        """
        جلب العملات الأكثر تحركاً وتذبذباً في السوق (Market Screener)
        الترتيب حسب نسبة التغيّر السعري (%) لاصطياد العملات المشتعلة فعلاً
        """
        try:
            logger.info("جاري مسح السوق للعثور على العملات الأكثر تحركاً...")
            tickers = self.exchange.fetch_tickers()
            
            # فلترة أزواج USDT فقط واستبعاد العقود المؤرخة (Delivery Contracts)
            usdt_pairs = {k: v for k, v in tickers.items() if '/USDT' in k and '-' not in k}
            
            # فلترة العملات ذات السيولة الكافية (حجم تداول يومي > 500,000 USDT)
            liquid_pairs = [v for v in usdt_pairs.values() if (v.get('quoteVolume') or 0) > 500000]
            
            # الترتيب حسب نسبة التغيّر المطلقة (الأعلى تحركاً سواء صعوداً أو هبوطاً)
            sorted_pairs = sorted(liquid_pairs, key=lambda x: abs(x.get('percentage', 0) or 0), reverse=True)
            
            top_symbols = [pair['symbol'] for pair in sorted_pairs[:limit]]
            
            # طباعة تفاصيل العملات المختارة
            for pair in sorted_pairs[:limit]:
                s = pair['symbol'].split('/')[0]
                pct = pair.get('percentage', 0) or 0
                vol = (pair.get('quoteVolume', 0) or 0) / 1_000_000
                logger.info(f"  📌 {s}: تغيّر {pct:+.2f}% | سيولة: {vol:.1f}M USDT")
            
            return top_symbols
        except Exception as e:
            logger.error(f"فشل في مسح السوق: {e}")
            return ['BTC/USDT', 'ETH/USDT', 'PEPE/USDT']

    def get_current_price(self, symbol):
        """جلب السعر اللحظي للعملة"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            logger.error(f"فشل جلب السعر الحالي: {e}")
            return None
            
    def execute_trade(self, symbol, side, amount, price=None, current_price=None, leverage=None):
        """تنفيذ صفقة حقيقية على المنصة وتحديد الرافعة المالية صراحةً"""
        try:
            # تعيين الرافعة المالية قبل التنفيذ
            trade_leverage = leverage if leverage is not None else Config.LEVERAGE
            try:
                self.exchange.set_leverage(trade_leverage, symbol)
                logger.info(f"تم تعيين الرافعة المالية للزوج {symbol} على {trade_leverage}x")
            except Exception as e:
                logger.warning(f"تعذر تعيين الرافعة المالية (قد تكون محددة مسبقاً): {e}")
                
            order_type = 'market' if not price else 'limit'
            logger.info(f"إرسال أمر {order_type.upper()} {side.upper()} للكمية {amount} لزوج {symbol}")
            
            # Hedge Mode: BUY = فتح مركز LONG | SELL = فتح مركز SHORT
            position_side = 'LONG' if side.lower() == 'buy' else 'SHORT'
            params = {'positionSide': position_side}
            
            order = self.exchange.create_order(symbol, order_type, side, amount, price, params)
            logger.info(f"نجاح العملية! رقم الطلب: {order.get('id')}")
            
            # Slippage Monitoring: التحقق من الانزلاق السعري للأوامر السوقية
            if order_type == 'market' and current_price:
                actual_price = order.get('average') or order.get('price')
                if actual_price and actual_price > 0:
                    slippage = abs(actual_price - current_price) / current_price
                    if slippage > 0.01:  # أكثر من 1% انزلاق سعري
                        logger.critical(f"[Slippage Alert] ⚠️ انزلاق سعري كبير في {symbol}: {slippage:.2%}! المتوقع: {current_price}، الفعلي: {actual_price}")
                        # هنا يمكننا اتخاذ قرار بإغلاق الصفقة إذا كان الانزلاق خطيراً جداً، لكننا حالياً نكتفي بالتحذير الشديد لتجنب إيقاف البوت بالكامل.
            return order
        except Exception as e:
            logger.error(f"فشل تنفيذ الصفقة: {e}")
            return None

    def has_open_position(self, symbol):
        """التحقق مما إذا كان هناك صفقة مفتوحة مسبقاً لهذه العملة لتجنب تكرار الدخول"""
        try:
            positions = self.exchange.fetch_positions([symbol])
            for position in positions:
                # positionAmt يمثل كمية الصفقة المفتوحة، إذا كان لا يساوي صفر، فهناك صفقة
                if float(position.get('info', {}).get('positionAmt', 0)) != 0:
                    return True
            return False
        except Exception as e:
            logger.error(f"فشل التحقق من الصفقات المفتوحة لـ {symbol}: {e}")
            return False

    def place_sl_tp(self, symbol, side, amount, sl_price, tp_price):
        """
        وضع أوامر Stop Loss و Take Profit الفعلية على باينانس لحماية رأس المال.
        يجب استدعاؤها مباشرة بعد execute_trade.
        """
        # في Hedge Mode: إذا فتحنا BUY (LONG)، فإن SL/TP تكون أوامر SELL
        close_side = 'sell' if side.lower() == 'buy' else 'buy'
        position_side = 'LONG' if side.lower() == 'buy' else 'SHORT'
        
        sl_success = False
        tp_success = False
        
        try:
            # وضع أمر Stop Loss (إغلاق الصفقة بالكامل)
            sl_params = {
                'positionSide': position_side,
                'stopPrice': sl_price,
                'closePosition': True  # هذه التعليمة تجبر باينانس على إغلاق الصفقة فوراً عند السعر
            }
            # نرسل None في حقل الكمية لأن closePosition=True ستغلق كامل الكمية المفتوحة تلقائياً
            sl_order = self.exchange.create_order(
                symbol, 'STOP_MARKET', close_side, None, None, sl_params
            )
            logger.info(f"✅ تم وضع Stop Loss عند {sl_price} | رقم الأمر: {sl_order.get('id')}")
            sl_success = True
        except Exception as e:
            logger.error(f"فشل وضع Stop Loss: {e}")

        try:
            # وضع أمر Take Profit (إغلاق الصفقة بالكامل)
            tp_params = {
                'positionSide': position_side,
                'stopPrice': tp_price,
                'closePosition': True
            }
            tp_order = self.exchange.create_order(
                symbol, 'TAKE_PROFIT_MARKET', close_side, None, None, tp_params
            )
            logger.info(f"✅ تم وضع Take Profit عند {tp_price} | رقم الأمر: {tp_order.get('id')}")
            tp_success = True
        except Exception as e:
            logger.error(f"فشل وضع Take Profit: {e}")
            
        return sl_success and tp_success

    def cancel_all_orders(self, symbol):
        """إلغاء كافة الأوامر المفتوحة للعملة (مثل الهدف والستوب)"""
        try:
            self.exchange.cancel_all_orders(symbol)
            logger.info(f"تم إلغاء كافة الأوامر المعلقة للزوج {symbol}.")
            return True
        except Exception as e:
            logger.error(f"فشل إلغاء الأوامر للزوج {symbol}: {e}")
            return False

    def close_position(self, symbol, side, amount):
        """إغلاق الصفقة المفتوحة فوراً بسعر السوق (Market Close) مع إلغاء الأوامر المرتبطة"""
        try:
            # 1. إلغاء الأوامر القديمة لتجنب تضاربها مع الإغلاق اليدوي/القسري
            self.cancel_all_orders(symbol)
            
            # 2. الإغلاق بسعر السوق
            close_side = 'sell' if side.lower() == 'buy' else 'buy'
            position_side = 'LONG' if side.lower() == 'buy' else 'SHORT'
            params = {'positionSide': position_side, 'reduceOnly': True} # reduceOnly لمنع فتح صفقة معاكسة بالخطأ
            logger.warning(f"🚨 جاري إغلاق الصفقة للزوج {symbol} بسعر السوق فوراً...")
            self.exchange.create_order(symbol, 'market', close_side, amount, None, params)
            logger.info(f"تم إغلاق الصفقة {symbol} بنجاح.")
            return True
        except Exception as e:
            logger.critical(f"فشل ذريع في إغلاق الصفقة {symbol}! التدخل اليدوي مطلوب فوراً: {e}")
            return False

    def get_position_amount(self, symbol):
        """جلب كمية الصفقة المفتوحة حالياً"""
        try:
            positions = self.exchange.fetch_positions([symbol])
            for position in positions:
                amt = float(position.get('info', {}).get('positionAmt', 0))
                if amt != 0:
                    return abs(amt)
            return 0
        except Exception as e:
            logger.error(f"فشل جلب كمية الصفقة المفتوحة لـ {symbol}: {e}")
            return 0

    def cancel_sl_orders(self, symbol):
        """إلغاء جميع أوامر الستوب المفتوحة للعملة (تُستخدم لتحديث الستوب المتحرك)"""
        try:
            open_orders = self.exchange.fetch_open_orders(symbol)
            for order in open_orders:
                order_type = order.get('type', '').upper()
                # نُلغي فقط أوامر STOP_MARKET (الستوب الحقيقي) ولا نلغي TAKE_PROFIT_MARKET
                if order_type == 'STOP_MARKET':
                    self.exchange.cancel_order(order['id'], symbol)
                    logger.debug(f"تم إلغاء أمر الستوب القديم {order['id']}")
            return True
        except Exception as e:
            logger.error(f"فشل إلغاء أوامر الستوب لـ {symbol}: {e}")
            return False

    def place_sl_only(self, symbol, side, amount, sl_price):
        """وضع أمر ستوب فقط (تُستخدم لتحديث الستوب المتحرك)"""
        close_side = 'sell' if side.lower() == 'buy' else 'buy'
        position_side = 'LONG' if side.lower() == 'buy' else 'SHORT'
        try:
            sl_params = {
                'positionSide': position_side,
                'stopPrice': sl_price,
                'closePosition': True
            }
            sl_order = self.exchange.create_order(
                symbol, 'STOP_MARKET', close_side, None, None, sl_params
            )
            logger.debug(f"تم وضع الستوب الجديد بنجاح عند {sl_price}")
            return True
        except Exception as e:
            logger.error(f"فشل وضع الستوب الجديد لـ {symbol}: {e}")
            return False

    def get_recent_sl_tp_fills(self, symbol, since_ms=None):
        """فحص التداولات المغلقة حديثاً للعثور على الربح والخسارة عند إغلاق الصفقة بدقة"""
        try:
            # استخدام fetch_my_trades أفضل من fetch_closed_orders لأنها تحتوي على realizedPnl دقيق
            trades = self.exchange.fetch_my_trades(symbol, since=since_ms, limit=10)
            alerts = []
            for trade in trades:
                info = trade.get('info', {})
                pnl = float(info.get('realizedPnl', 0))
                
                # في باينانس فيوتشرز، التداول الذي يغلق الصفقة يكون له realizedPnl مختلف عن الصفر
                if pnl != 0:
                    order_type = "TAKE_PROFIT" if pnl > 0 else "STOP_LOSS"
                    alerts.append({
                        'symbol': symbol,
                        'type': order_type,
                        'price': float(trade.get('price', 0)),
                        'pnl': pnl
                    })
            return alerts
        except Exception as e:
            logger.error(f"خطأ في جلب التداولات المغلقة لـ {symbol} (قد لا تكون مدعومة): {e}")
            return []
