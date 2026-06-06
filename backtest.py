"""
=============================================================
 Backtest Engine — اختبار استراتيجية التداول على البيانات التاريخية
=============================================================
يجلب بيانات Binance Futures مباشرةً (2024-2026) ويُشغّل
الاستراتيجية الهجينة بنفس المنطق المستخدم في البوت الحقيقي.

الناتج:
  - Win Rate
  - Profit Factor
  - Max Drawdown
  - Sharpe Ratio (مبسّط)
  - تقرير كامل بكل صفقة

الاستخدام:
  python backtest.py
=============================================================
"""

import sys
import os
import logging
import pandas as pd
import numpy as np
import ccxt
from datetime import datetime, timedelta
from dotenv import load_dotenv

# إضافة مسار المشروع ليتمكن من استيراد الموديولات
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

from engines.ta_engine import TAEngine
from strategy.hybrid_logic import HybridStrategy
from strategy.risk_manager import RiskManager

# كتم الرسائل التفصيلية والتحذيرات أثناء الاختبار العكسي لتسريع العملية وتجنب امتلاء الذاكرة
logging.getLogger('TradingBot').setLevel(logging.ERROR)
for handler in logging.getLogger('TradingBot').handlers:
    handler.setLevel(logging.ERROR)

# =============================================
#  إعدادات الـ Backtest — عدّلها حسب رغبتك
# =============================================
SYMBOLS        = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT', '1000PEPE/USDT']  # العملات المراد اختبارها
TIMEFRAME      = '15m'                                   # الإطار الزمني
DAYS_BACK      = 365                                     # كم يوماً للخلف (365 = سنة كاملة)
INITIAL_BALANCE = 1000                                   # الرصيد الافتراضي للاختبار ($)
LEVERAGE        = 10                                     # الرافعة المالية
SL_PCT          = 0.02                                   # ستوب لوز 2%
TP_PCT          = 0.04                                   # هدف الربح 4%
RISK_PER_TRADE  = 0.01                                   # خطر 1% من الرصيد لكل صفقة
COMMISSION      = 0.0005                                 # رسوم Binance Futures (0.05% Taker)
# =============================================

def fetch_historical_data(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """جلب البيانات التاريخية من Binance مباشرةً"""
    print(f"  ⏳ جلب بيانات {symbol} ({timeframe}) لآخر {days} يوم...")
    
    exchange = ccxt.binance({
        'options': {'defaultType': 'future'},
        'enableRateLimit': True,
    })

    since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    all_ohlcv = []
    
    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + 1
            # إذا آخر شمعة أصبحت أحدث من الوقت الحالي، توقف
            if ohlcv[-1][0] >= int(datetime.utcnow().timestamp() * 1000) - 60000:
                break
        except Exception as e:
            print(f"  ⚠️ خطأ في جلب البيانات: {e}")
            break

    if not all_ohlcv:
        return pd.DataFrame()

    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df = df[~df.index.duplicated(keep='last')]
    print(f"  ✅ تم جلب {len(df)} شمعة ({df.index[0].date()} → {df.index[-1].date()})")
    return df


def run_backtest(symbol: str, df: pd.DataFrame, initial_balance: float) -> dict:
    """
    تشغيل الـ Backtest على بيانات الشمعات مع دمج رسوم المنصة والانزلاق السعري والتمويل والتحقق الصارم.
    """
    ta_engine       = TAEngine()
    hybrid_strategy = HybridStrategy()
    risk_manager    = RiskManager()

    # إضافة المؤشرات لكل البيانات مرة واحدة
    df = ta_engine.add_indicators(df.copy())
    df.dropna(inplace=True)

    balance      = initial_balance
    position     = None
    trades       = []
    equity_curve = [balance]
    last_trade_close_idx = 0        # Cooldown: index آخر صفقة أُغلقت
    COOLDOWN_BARS = 16              # 16 شمعة × 15 دقيقة = 4 ساعات كولداون بين الصفقات

    # ثوابت المحاكاة الواقعية
    SLIPPAGE_ENTRY = 0.0003  # 0.03% انزلاق سعري عند الدخول
    SLIPPAGE_SL = 0.0008     # 0.08% انزلاق سعري عند تفعيل وقف الخسارة
    SLIPPAGE_TP = 0.0002     # 0.02% انزلاق سعري عند تفعيل جني الأرباح
    MIN_ORDER_VALUE = 5.0    # الحد الأدنى لقيمة الأمر على باينانس ($5)

    # نبدأ من الشمعة 200 لضمان توفر جميع المؤشرات (EMA 200)
    for i in range(200, len(df)):
        bars   = df.iloc[:i+1]      # بدون look-ahead
        latest = bars.iloc[-1]
        current_price = latest['close']

        if balance <= 0:
            break

        # === إذا لا توجد صفقة مفتوحة: نبحث عن إشارة ===
        if position is None:
            if i - last_trade_close_idx < COOLDOWN_BARS:
                equity_curve.append(balance)
                continue

            ta_signal = ta_engine.evaluate_trend(bars.tail(100), symbol)
            ta_data = {
                'price': latest['close'], 'open': latest['open'],
                'rsi': latest.get('rsi', 0), 'macd': latest.get('macd', 0),
                'ema_50': latest.get('ema_50', 0)
            }
            decision_obj  = hybrid_strategy.decide(ta_signal, sentiment=None, ta_data=ta_data)
            decision = decision_obj.get("action", "hold")

            if decision in ['buy', 'sell']:
                # تصنيف العملة لمعرفة ما إذا كانت ميم لتطبيق قواعد الحماية
                base = symbol.split('/')[0]
                is_meme = base in ['DOGE', 'SHIB', 'PEPE', 'FLOKI', 'BONK', 'WIF', 'BOME', 'MEME', 'MYRO', '1000SATS', 'ORDI', '1000PEPE', '1000SHIB', '1000BONK', '1000FLOKI']
                
                coin_leverage = 3 if is_meme else LEVERAGE
                coin_risk_pct = 0.0025 if is_meme else RISK_PER_TRADE
                
                # حماية شراء القمم (Anti-Pump Filter) للعملات عالية التذبذب
                if is_meme and decision == 'buy':
                    price_24h_ago = df.iloc[max(0, i-96)]['close']
                    change_24h = (current_price - price_24h_ago) / price_24h_ago * 100
                    if change_24h > 15.0:
                        equity_curve.append(balance)
                        continue

                support    = latest.get('support', 0)
                resistance = latest.get('resistance', 0)
                atr        = latest.get('atr', 0)
                sl, tp     = risk_manager.calculate_sl_tp(decision, current_price, support, resistance, atr)

                # التحقق الصارم من صحة مستويات الوقف والهدف قبل الدخول
                if decision == 'buy':
                    if tp <= current_price or sl >= current_price:
                        # إلغاء الدخول إذا كانت المستويات غير منطقية
                        equity_curve.append(balance)
                        continue
                else:
                    if tp >= current_price or sl <= current_price:
                        # إلغاء الدخول إذا كانت المستويات غير منطقية
                        equity_curve.append(balance)
                        continue

                # حساب الكمية بناءً على نسبة المخاطرة المخصصة والـ ATR
                amount = risk_manager.calculate_position_size(balance, current_price, custom_risk_percent=coin_risk_pct, atr=atr)

                # التحقق من الحد الأدنى لقيمة الأمر على باينانس
                notional = amount * current_price
                if notional < MIN_ORDER_VALUE:
                    equity_curve.append(balance)
                    continue

                # التحقق من الهامش المطلوب (القيمة الاسمية / الرافعة المالية المخصصة)
                margin_required = notional / coin_leverage
                if margin_required > balance * 0.95:
                    equity_curve.append(balance)
                    continue

                if amount > 0:
                    # تطبيق الانزلاق السعري عند الدخول
                    if decision == 'buy':
                        entry_price = current_price * (1 + SLIPPAGE_ENTRY)
                    else:
                        entry_price = current_price * (1 - SLIPPAGE_ENTRY)
                        
                    # رسوم الدخول = 0.05% من القيمة الاسمية الفعلية
                    entry_notional = amount * entry_price
                    commission = entry_notional * COMMISSION
                    balance   -= commission

                    position = {
                        'side':      decision,
                        'entry':     entry_price,
                        'sl':        sl,
                        'tp':        tp,
                        'amount':    amount,
                        'open_time': bars.index[-1],
                    }

        # === إذا توجد صفقة مفتوحة: نتحقق من SL/TP ===
        else:
            high = latest['high']
            low  = latest['low']
            closed = False
            exit_price = None
            exit_reason = None

            if position['side'] == 'buy':
                if low <= position['sl']:
                    # تطبيق انزلاق الستوب لوز (أكبر بسبب البيع بسعر السوق)
                    exit_price  = position['sl'] * (1 - SLIPPAGE_SL)
                    exit_reason = 'SL'
                    closed      = True
                elif high >= position['tp']:
                    # تطبيق انزلاق التيك بروفيت (أقل بسبب وضع أمر ليميت)
                    exit_price  = position['tp'] * (1 - SLIPPAGE_TP)
                    exit_reason = 'TP'
                    closed      = True
            else:  # sell (SHORT)
                if high >= position['sl']:
                    exit_price  = position['sl'] * (1 + SLIPPAGE_SL)
                    exit_reason = 'SL'
                    closed      = True
                elif low <= position['tp']:
                    exit_price  = position['tp'] * (1 + SLIPPAGE_TP)
                    exit_reason = 'TP'
                    closed      = True

            if closed:
                # حساب PnL الفعلي بناءً على سعر الخروج مع الانزلاق السعري
                if position['side'] == 'buy':
                    pnl = (exit_price - position['entry']) * position['amount']
                else:
                    pnl = (position['entry'] - exit_price) * position['amount']

                # رسوم التمويل (Funding Fee) التقديرية: 0.01% لكل 8 ساعات احتفاظ بالصفقة
                open_time = position['open_time']
                close_time = bars.index[-1]
                hold_hours = (close_time - open_time).total_seconds() / 3600.0
                funding_intervals = int(hold_hours / 8.0)
                funding_cost = funding_intervals * 0.0001 * (position['amount'] * exit_price)
                pnl -= funding_cost

                # رسوم الخروج = 0.05% من القيمة الاسمية عند الخروج
                exit_notional = exit_price * position['amount']
                commission    = exit_notional * COMMISSION
                pnl          -= commission
                
                balance      += pnl

                trades.append({
                    'symbol':     symbol,
                    'side':       position['side'].upper(),
                    'entry':      position['entry'],
                    'exit':       exit_price,
                    'sl':         position['sl'],
                    'tp':         position['tp'],
                    'amount':     position['amount'],
                    'pnl':        pnl,
                    'pnl_pct':    (pnl / initial_balance) * 100,
                    'result':     exit_reason,
                    'open_time':  position['open_time'],
                    'close_time': close_time,
                    'duration':   str(close_time - position['open_time']),
                })
                last_trade_close_idx = i
                position = None

        equity_curve.append(balance)

    # إغلاق أي صفقة مفتوحة في نهاية البيانات بسعر الإغلاق الأخير مع الرسوم والانزلاق السعري
    if position:
        exit_price = df.iloc[-1]['close'] * (1 - SLIPPAGE_SL if position['side'] == 'buy' else 1 + SLIPPAGE_SL)
        if position['side'] == 'buy':
            pnl = (exit_price - position['entry']) * position['amount']
        else:
            pnl = (position['entry'] - exit_price) * position['amount']
            
        pnl -= exit_price * position['amount'] * COMMISSION
        balance += pnl
        trades.append({
            'symbol': symbol, 'side': position['side'].upper(),
            'entry': position['entry'], 'exit': exit_price,
            'sl': position['sl'], 'tp': position['tp'],
            'amount': position['amount'], 'pnl': pnl,
            'pnl_pct': (pnl / initial_balance) * 100,
            'result': 'OPEN_AT_END', 'open_time': position['open_time'],
            'close_time': df.index[-1], 'duration': 'N/A',
        })

    return {
        'symbol':        symbol,
        'trades':        trades,
        'equity_curve':  equity_curve,
        'final_balance': balance,
    }


def calculate_metrics(result: dict, initial_balance: float) -> dict:
    """حساب مقاييس الأداء الإحصائية"""
    trades = result['trades']
    if not trades:
        return {'error': 'لا توجد صفقات'}

    pnls         = [t['pnl'] for t in trades]
    winners      = [p for p in pnls if p > 0]
    losers       = [p for p in pnls if p <= 0]
    win_rate     = len(winners) / len(pnls) * 100 if pnls else 0
    avg_win      = np.mean(winners) if winners else 0
    avg_loss     = abs(np.mean(losers)) if losers else 0
    profit_factor = sum(winners) / abs(sum(losers)) if losers and sum(losers) != 0 else float('inf')

    # Max Drawdown
    equity = result['equity_curve']
    peak   = equity[0]
    max_dd = 0
    for val in equity:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe Ratio (مبسّط باستخدام عوائد يومية)
    daily_returns = pd.Series(pnls).resample('D').sum() if False else pd.Series(pnls)
    sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() > 0 else 0

    total_return = ((result['final_balance'] - initial_balance) / initial_balance) * 100

    return {
        'total_trades':   len(trades),
        'winners':        len(winners),
        'losers':         len(losers),
        'win_rate':       win_rate,
        'avg_win':        avg_win,
        'avg_loss':       avg_loss,
        'profit_factor':  profit_factor,
        'max_drawdown':   max_dd,
        'sharpe_ratio':   sharpe,
        'total_return':   total_return,
        'final_balance':  result['final_balance'],
    }


def print_report(symbol: str, metrics: dict, trades: list):
    """طباعة تقرير منسّق"""
    sep = "=" * 60

    print(f"\n{sep}")
    print(f"  📊 تقرير Backtest: {symbol}")
    print(sep)

    if 'error' in metrics:
        print(f"  ⚠️  {metrics['error']}")
        return

    # تقييم الجودة
    pf  = metrics['profit_factor']
    wr  = metrics['win_rate']
    mdd = metrics['max_drawdown']

    pf_icon  = "🟢" if pf > 1.5 else ("🟡" if pf > 1.0 else "🔴")
    wr_icon  = "🟢" if wr > 55 else ("🟡" if wr > 45 else "🔴")
    mdd_icon = "🟢" if mdd < 15 else ("🟡" if mdd < 25 else "🔴")
    ret_icon = "🟢" if metrics['total_return'] > 0 else "🔴"

    print(f"  📈 إجمالي الصفقات : {metrics['total_trades']}")
    print(f"  ✅ صفقات رابحة   : {metrics['winners']}")
    print(f"  ❌ صفقات خاسرة   : {metrics['losers']}")
    print(f"  {wr_icon}  نسبة الفوز      : {metrics['win_rate']:.1f}%  (الهدف: > 55%)")
    print(f"  {pf_icon}  معامل الربح    : {metrics['profit_factor']:.2f}  (الهدف: > 1.5)")
    print(f"  {mdd_icon}  أقصى تراجع    : {metrics['max_drawdown']:.1f}%  (الهدف: < 20%)")
    print(f"  📐 Sharpe Ratio  : {metrics['sharpe_ratio']:.2f}  (الهدف: > 1.0)")
    print(f"  {ret_icon}  إجمالي العائد  : {metrics['total_return']:+.2f}%")
    print(f"  💰 الرصيد النهائي: ${metrics['final_balance']:.2f}")
    print(f"  💵 متوسط الربح   : ${metrics['avg_win']:.2f}")
    print(f"  💸 متوسط الخسارة : ${metrics['avg_loss']:.2f}")

    # آخر 10 صفقات بالتفصيل
    print(f"\n  آخر 10 صفقات:")
    print(f"  {'الزوج':<10} {'النوع':<6} {'Entry':<10} {'SL':<10} {'TP':<10} {'النتيجة':<10} {'PnL':>8}")
    print(f"  {'-'*72}")
    for t in trades[-10:]:
        icon = "✅" if t['pnl'] > 0 else "❌"
        print(f"  {t['symbol']:<10} {t['side']:<6} {t['entry']:<10.2f} {t['sl']:<10.2f} {t['tp']:<10.2f} {icon} {t['result']:<8} {t['pnl']:>+7.2f}$")

    print(sep)


def save_trades_csv(all_trades: list, filename: str = "backtest_results.csv"):
    """حفظ جميع الصفقات في ملف CSV للمراجعة"""
    if not all_trades:
        return
    df = pd.DataFrame(all_trades)
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"\n  💾 تم حفظ {len(all_trades)} صفقة في: {filename}")


# فئات العملات للاختبار والتقرير
CATEGORIES = {
    'MAINSTREAM': ['BTC/USDT', 'ETH/USDT'],
    'MIDCAP': [
        'SOL/USDT', 'BNB/USDT', 'ADA/USDT', 'XRP/USDT', 'DOT/USDT', 
        'LTC/USDT', 'LINK/USDT', 'AVAX/USDT', 'NEAR/USDT', 'FIL/USDT', 
        'FTM/USDT', 'OP/USDT', 'ARB/USDT', 'INJ/USDT', 'GRT/USDT', 
        'SUI/USDT', 'APT/USDT', 'LDO/USDT'
    ],
    'MEME': [
        'DOGE/USDT', '1000PEPE/USDT', '1000SHIB/USDT', '1000BONK/USDT', 
        'WIF/USDT', 'FLOKI/USDT', 'BOME/USDT', 'ORDI/USDT', 'PEOPLE/USDT', 'MEME/USDT'
    ]
}

def evaluate_period(trades_list: list, days: int, symbol: str, initial_balance: float) -> dict:
    """تصفية الصفقات وحساب المقاييس لفترة زمنية محددة بالخلف"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    # تصفية الصفقات التي أغلقت داخل الفترة
    period_trades = [t for t in trades_list if pd.to_datetime(t['close_time']).replace(tzinfo=None) >= cutoff]
    
    if not period_trades:
        return {
            'total_trades': 0, 'winners': 0, 'losers': 0, 'win_rate': 0.0,
            'profit_factor': 0.0, 'max_drawdown': 0.0, 'total_return': 0.0,
            'final_balance': initial_balance, 'buy_count': 0, 'sell_count': 0
        }
        
    winners = [t['pnl'] for t in period_trades if t['pnl'] > 0]
    losers = [t['pnl'] for t in period_trades if t['pnl'] <= 0]
    win_rate = len(winners) / len(period_trades) * 100
    profit_factor = sum(winners) / abs(sum(losers)) if losers and sum(losers) != 0 else float('inf')
    
    # إعادة بناء منحنى المحفظة التراكمي للفترة
    balance = initial_balance
    equity_curve = [balance]
    for t in period_trades:
        balance += t['pnl']
        equity_curve.append(balance)
        
    # حساب أقصى تراجع للفترة
    peak = equity_curve[0]
    max_dd = 0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100
        if dd > max_dd:
            max_dd = dd
            
    total_return = ((balance - initial_balance) / initial_balance) * 100
    buy_count = sum(1 for t in period_trades if t['side'] == 'BUY')
    sell_count = sum(1 for t in period_trades if t['side'] == 'SELL')
    
    return {
        'total_trades': len(period_trades),
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_drawdown': max_dd,
        'total_return': total_return,
        'final_balance': balance,
        'buy_count': buy_count,
        'sell_count': sell_count
    }

# ==============================
#  نقطة البداية الرئيسية
# ==============================
if __name__ == '__main__':
    all_test_symbols = CATEGORIES['MAINSTREAM'] + CATEGORIES['MIDCAP'] + CATEGORIES['MEME']
    
    print("\n" + "=" * 60)
    print("  🚀 Backtest Engine — اختبار الاستراتيجية الهجينة الموسع")
    print(f"  📅 الفترة: آخر {DAYS_BACK} يوم | الإطار: {TIMEFRAME}")
    print(f"  💰 رصيد افتراضي للرمز الواحد: ${INITIAL_BALANCE} | رافعة عامة: {LEVERAGE}x")
    print(f"  📋 عدد العملات المستهدفة: {len(all_test_symbols)} عملة موزعة فئوياً")
    print("=" * 60)

    symbol_trades = {}
    
    for symbol in all_test_symbols:
        df = fetch_historical_data(symbol, TIMEFRAME, DAYS_BACK)
        if df.empty:
            print(f"  ❌ فشل جلب بيانات {symbol}. تخطي...")
            continue
            
        result = run_backtest(symbol, df, INITIAL_BALANCE)
        symbol_trades[symbol] = result['trades']
        
        # طباعة ملخص سريع للعملة لـ 365 يوم
        metrics_365 = calculate_metrics(result, INITIAL_BALANCE)
        print_report(symbol, metrics_365, result['trades'])

    # فترات الاختبار المطلوبة
    periods = [30, 90, 180, 365]
    all_flat_trades = []
    for sym, trs in symbol_trades.items():
        all_flat_trades.extend(trs)
    save_trades_csv(all_flat_trades)
    
    # توليد التقارير المجمعة لكل فترة ولكل فئة
    report_file = "backtest_summary_report.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# 📊 تقرير الأداء الشامل ومتعدد الفترات للبوت الخوارزمي\n\n")
        f.write("تم إجراء اختبار عكسي واقعي للغاية يشمل الرسوم، الانزلاق السعري، التمويل، والحدود الدنيا للأوامر.\n\n")
        
        for period in periods:
            f.write(f"## 📅 نتائج الأداء لآخر {period} يوم\n\n")
            f.write("| الفئة | العملة | عدد الصفقات | نسبة الفوز | معامل الربح (PF) | أقصى تراجع | عدد الشراء | عدد البيع | العائد الصافي |\n")
            f.write("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")
            
            period_metrics_by_category = {'MAINSTREAM': [], 'MIDCAP': [], 'MEME': []}
            
            for cat_name, cat_symbols in CATEGORIES.items():
                for sym in cat_symbols:
                    if sym not in symbol_trades:
                        continue
                    trs = symbol_trades[sym]
                    m = evaluate_period(trs, period, sym, INITIAL_BALANCE)
                    if m['total_trades'] > 0:
                        period_metrics_by_category[cat_name].append(m)
                        f.write(f"| {cat_name} | {sym} | {m['total_trades']} | {m['win_rate']:.1f}% | {m['profit_factor']:.2f} | {m['max_drawdown']:.1f}% | {m['buy_count']} | {m['sell_count']} | {m['total_return']:+.2f}% |\n")
            
            # كتابة ملخص الفئات لهذه الفترة
            f.write("\n### 📉 ملخص الأداء حسب الفئات لآخر {} يوم\n\n".format(period))
            f.write("| الفئة | متوسط نسبة الفوز | متوسط معامل الربح | متوسط أقصى تراجع | إجمالي الصفقات | متوسط العائد |\n")
            f.write("| :--- | :---: | :---: | :---: | :---: | :---: |\n")
            
            for cat_name, cat_list in period_metrics_by_category.items():
                if cat_list:
                    avg_wr = np.mean([m['win_rate'] for m in cat_list])
                    avg_pf = np.mean([m['profit_factor'] for m in cat_list if m['profit_factor'] != float('inf')])
                    avg_mdd = np.mean([m['max_drawdown'] for m in cat_list])
                    total_trs = sum(m['total_trades'] for m in cat_list)
                    avg_ret = np.mean([m['total_return'] for m in cat_list])
                    f.write(f"| {cat_name} | {avg_wr:.1f}% | {avg_pf:.2f} | {avg_mdd:.1f}% | {total_trs} | {avg_ret:+.2f}% |\n")
            f.write("\n" + "-"*40 + "\n\n")

    print(f"\n✨ اكتملت المحاكاة بالكامل. تم إنشاء تقرير الأداء الشامل وحفظه في: {report_file}")
