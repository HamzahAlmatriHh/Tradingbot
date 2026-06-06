import time
from core.logger import logger
from core.config import Config

class UniverseFilter:
    def __init__(self, client, state_manager=None):
        self.client = client
        self.state_manager = state_manager
        # قائمة العملات الميمية المعروفة لتطبيق قواعد حماية مشددة عليها
        self.meme_coins = {
            'DOGE', 'SHIB', 'PEPE', 'FLOKI', 'BONK', 
            'WIF', 'BOME', 'MEME', 'MYRO', '1000SATS', 'ORDI',
            '1000PEPE', '1000SHIB', '1000BONK', '1000FLOKI'
        }
        # القائمة السوداء الافتراضية (مستقرة أو مشاكل سيولة)
        self.blacklist = set(getattr(Config, 'BLACKLIST', [
            'USDC/USDT', 'FDUSD/USDT', 'TUSD/USDT', 'AEUR/USDT', 'EUR/USDT'
        ]))
        
        # إعدادات الفلترة من الإعدادات أو الافتراضية
        self.min_volume_24h = getattr(Config, 'MIN_24H_VOLUME_USDT', 10_000_000) # 10 مليون دولار
        self.max_spread_pct = getattr(Config, 'MAX_SPREAD_PCT', 0.001)         # 0.10%

        # العملات المستهدفة للتحليل لمراقبة الفلاتر المرفوضة بدقة
        self.target_universe = {
            'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'ADA/USDT', 
            'XRP/USDT', 'DOT/USDT', 'LTC/USDT', 'LINK/USDT', 'AVAX/USDT', 
            'NEAR/USDT', 'FIL/USDT', 'FTM/USDT', 'OP/USDT', 'ARB/USDT', 
            'INJ/USDT', 'GRT/USDT', 'SUI/USDT', 'APT/USDT', 'LDO/USDT',
            'DOGE/USDT', '1000PEPE/USDT', '1000SHIB/USDT', '1000BONK/USDT', 
            'WIF/USDT', 'FLOKI/USDT', 'BOME/USDT', 'ORDI/USDT', 'PEOPLE/USDT', 
            'MEME/USDT'
        }

    def classify_symbol(self, symbol):
        """
        تصنيف العملة لمعرفة ما إذا كانت ميم أو جديدة أو عملة كبرى.
        """
        base = symbol.split('/')[0]
        
        # 1. التحقق من كونها عملة ميمية معروفة
        if base in self.meme_coins:
            return 'meme', "عملة ميمية معروفة"
            
        # 2. التحقق من كونها جديدة (أقل من 30 يوم من البيانات اليومية)
        try:
            # نجلب 30 شمعة يومية للتحقق من عمر التداول على باينانس
            daily_candles = self.client.exchange.fetch_ohlcv(symbol, timeframe='1d', limit=30)
            if len(daily_candles) < 30:
                return 'new', f"عملة جديدة (عمرها {len(daily_candles)} يوم فقط)"
        except Exception as e:
            logger.warning(f"فشل التحقق من عمر العملة {symbol}: {e}")
            
        return 'mainstream', "عملة رئيسية / مستقرة تاريخياً"

    def get_dynamic_blacklist(self):
        """
        تقوم بتحليل الصفقات التاريخية من الباك تست والصفقات الحية لآخر 90 يوماً
        وتحظر تلقائياً العملات ذات عامل الربح المنخفض أو التراجع العالي.
        """
        # القائمة السوداء الثابتة كبداية
        dynamic_blacklist = set(self.blacklist)
        
        is_testnet = getattr(Config, 'USE_TESTNET', False)
        is_dynamic_blacklist_active = getattr(Config, 'DYNAMIC_BLACKLIST_ACTIVE', True)
        if is_testnet:
            # تعطيل القائمة السوداء الديناميكية مؤقتاً في وضع Testnet
            is_dynamic_blacklist_active = False
            
        if not is_dynamic_blacklist_active:
            return dynamic_blacklist
            
        import os
        import pandas as pd
        
        trades_dfs = []
        for file_path in ['backtest_results.csv', 'testnet_trades_log.csv']:
            if os.path.exists(file_path):
                try:
                    df = pd.read_csv(file_path)
                    if not df.empty:
                        trades_dfs.append(df)
                except Exception as e:
                    logger.error(f"فشل قراءة سجل الصفقات {file_path}: {e}")
                    
        if not trades_dfs:
            return dynamic_blacklist
            
        try:
            all_trades = pd.concat(trades_dfs, ignore_index=True)
            if all_trades.empty or 'close_time' not in all_trades.columns:
                return dynamic_blacklist
                
            all_trades['close_time'] = pd.to_datetime(all_trades['close_time'], errors='coerce')
            latest_time = all_trades['close_time'].max()
            if pd.isna(latest_time):
                return dynamic_blacklist
                
            cutoff_time = latest_time - pd.Timedelta(days=90)
            recent_trades = all_trades[all_trades['close_time'] >= cutoff_time]
            
            for symbol, group in recent_trades.groupby('symbol'):
                # فلترة مبدئية: يجب توفر 5 صفقات على الأخل للحكم إحصائياً وتجنب العشوائية
                if len(group) >= 5:
                    pnls = pd.to_numeric(group['pnl'], errors='coerce').dropna().values
                    winners = pnls[pnls > 0]
                    losers = pnls[pnls <= 0]
                    
                    sum_win = winners.sum() if len(winners) > 0 else 0
                    sum_loss = abs(losers.sum()) if len(losers) > 0 else 0
                    
                    # حساب عامل الربح (Profit Factor)
                    pf = sum_win / sum_loss if sum_loss > 0 else float('inf')
                    
                    # حساب أقصى تراجع للرمز (Max Drawdown)
                    equity = [100.0]
                    pnl_pcts = pd.to_numeric(group.get('pnl_pct', group['pnl'] / 10.0), errors='coerce').fillna(0).values
                    for p_pct in pnl_pcts:
                        equity.append(equity[-1] + p_pct)
                    
                    peak = equity[0]
                    max_dd = 0
                    for val in equity:
                        if val > peak:
                            peak = val
                        dd = (peak - val) / peak * 100
                        if dd > max_dd:
                            max_dd = dd
                            
                    # شروط الإدراج في القائمة السوداء الديناميكية:
                    # 1. PF أقل من 0.85
                    # 2. أو Max Drawdown أعلى من 15%
                    if pf < 0.85 or max_dd > 15.0:
                        dynamic_blacklist.add(symbol)
                        logger.warning(f"🚫 [Dynamic Blacklist] حظر {symbol} تلقائياً لآخر 90 يوماً: PF={pf:.2f} | Max DD={max_dd:.1f}%")
                        
        except Exception as e:
            logger.error(f"خطأ أثناء تحديث القائمة السوداء الديناميكية: {e}")
            
        return dynamic_blacklist

    def filter_and_get_tickers(self):
        """
        مسح كامل السوق وجلب جميع أزواج USDT Perpetual المؤهلة وتصنيفها.
        """
        try:
            logger.info("🔍 بدء عملية مسح وتصفية أسواق باينانس (Universe Filtering)...")
            tickers = self.client.exchange.fetch_tickers()
            markets = self.client.exchange.load_markets()
            
            # جلب القائمة السوداء الديناميكية
            active_blacklist = self.get_dynamic_blacklist()
            
            eligible_symbols = []
            
            rejection_stats = {
                'symbol_format': 0,
                'inactive_or_not_swap': 0,
                'static_blacklist': 0,
                'dynamic_blacklist': 0,
                'volume': 0,
                'spread': 0
            }
            
            for symbol, ticker in tickers.items():
                # 1. فلترة أزواج USDT العقود الآجلة المستمرة فقط
                if not symbol.endswith('/USDT'):
                    rejection_stats['symbol_format'] += 1
                    continue
                if '-' in symbol: # استبعاد العقود المؤرخة (Delivery)
                    rejection_stats['symbol_format'] += 1
                    continue
                
                market = markets.get(symbol)
                if not market or not market.get('active', False) or market.get('type') != 'swap':
                    rejection_stats['inactive_or_not_swap'] += 1
                    continue
                
                # 2. استبعاد القائمة السوداء الثابتة والديناميكية
                if symbol in active_blacklist:
                    if symbol in self.blacklist:
                        rejection_stats['static_blacklist'] += 1
                    else:
                        rejection_stats['dynamic_blacklist'] += 1
                    continue
                    
                # 3. التحقق من حجم التداول 24h
                volume_24h = ticker.get('quoteVolume', 0) or 0
                if volume_24h < self.min_volume_24h:
                    rejection_stats['volume'] += 1
                    if self.state_manager and symbol in self.target_universe:
                        self.state_manager.increment_rejection_counter("Volume")
                    continue
                    
                # 4. التحقق من السبريد (Spread)
                ask = ticker.get('ask', 0) or 0
                bid = ticker.get('bid', 0) or 0
                if bid <= 0:
                    rejection_stats['spread'] += 1
                    continue
                spread = (ask - bid) / bid
                if spread > self.max_spread_pct:
                    rejection_stats['spread'] += 1
                    if self.state_manager and symbol in self.target_universe:
                        self.state_manager.increment_rejection_counter("Spread")
                    continue
                    
                eligible_symbols.append((symbol, ticker, volume_24h))
                
            logger.info("=========================================")
            logger.info("📊 تقرير تصفية أزواج العملات (Universe Filter Report):")
            logger.info(f"   • Rejected by symbol format: {rejection_stats['symbol_format']}")
            logger.info(f"   • Rejected by inactive/not swap: {rejection_stats['inactive_or_not_swap']}")
            logger.info(f"   • Rejected by static blacklist: {rejection_stats['static_blacklist']}")
            logger.info(f"   • Rejected by dynamic blacklist: {rejection_stats['dynamic_blacklist']}")
            logger.info(f"   • Rejected by volume (< {self.min_volume_24h/1_000_000:.1f}M): {rejection_stats['volume']}")
            logger.info(f"   • Rejected by spread (> {self.max_spread_pct:.2%}): {rejection_stats['spread']}")
            logger.info(f"   • Final eligible pairs: {len(eligible_symbols)}")
            logger.info("=========================================")
            return eligible_symbols
            
        except Exception as e:
            logger.error(f"❌ خطأ أثناء تصفية الأسواق: {e}")
            return []

    def get_scanning_targets(self, limit=10):
        """
        يرتب العملات المؤهلة حسب السيولة/التقلب ويختار أفضلها للمسح الفني
        """
        is_testnet = getattr(Config, 'USE_TESTNET', False)
        force_symbols = getattr(Config, 'TESTNET_FORCE_SYMBOLS', [])
        
        if is_testnet and force_symbols:
            logger.info(f"ℹ️ [Testnet Mode] فرض فحص العملات التالية (تخطي الفلتر): {', '.join(force_symbols)}")
            try:
                self.filter_and_get_tickers()
            except Exception as e:
                logger.warning(f"فشل تشغيل فلتر التصفية للتقرير: {e}")
            return force_symbols
            
        eligible = self.filter_and_get_tickers()
        if not eligible:
            logger.warning("⚠️ لم تنجح التصفية في إيجاد أي عملة مؤهلة! لا يوجد عملات للمسح.")
            return []
            
        # ترتيب حسب حجم التداول 24h الأعلى لضمان دخول سيولة سريعة وموثوقة
        sorted_eligible = sorted(eligible, key=lambda x: x[2], reverse=True)
        targets = [x[0] for x in sorted_eligible[:limit]]
        
        logger.info(f"🔥 أفضل {limit} عملات مؤهلة للمسح بناءً على السيولة والسبريد: {', '.join(targets)}")
        return targets
