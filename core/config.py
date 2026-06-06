import os
from dotenv import load_dotenv

# تحميل المتغيرات من ملف .env
load_dotenv()

class Config:
    # Binance Config
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
    USE_TESTNET = True  # تفعيل بيئة الاختبار بشكل افتراضي

    # News Config
    NEWS_API_KEY = os.getenv("NEWS_API_KEY")
    CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY")
    
    # Sentiment & Social API Config
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    LUNARCRUSH_API_KEY = os.getenv("LUNARCRUSH_API_KEY")
    COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
    FRED_API_KEY = os.getenv("FRED_API_KEY")

    # Telegram Config
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    # إعدادات التداول الافتراضية
    TRADE_SYMBOL = "BTC/USDT"
    RISK_PER_TRADE_PERCENT = 0.25 # تم التخفيض بناءً على مراجعة الأمان
    LEVERAGE = 1                  # رافعة مالية 1x (بدون رافعة فعلية) للنسخة الحية
    MAX_OPEN_TRADES = 1           # صفقة واحدة فقط في نفس الوقت
    DAILY_LOSS_LIMIT_PERCENT = 1.0 # إيقاف صارم إذا خسر 1% يومياً

    # [إعدادات فلتر أمان السوق - Universe Filter]
    MIN_24H_VOLUME_USDT = 10_000_000 # الحد الأدنى لحجم التداول 10M USDT
    MAX_SPREAD_PCT = 0.001           # الحد الأقصى للسبريد 0.10%
    BLACKLIST = ['USDC/USDT', 'FDUSD/USDT', 'TUSD/USDT', 'AEUR/USDT', 'EUR/USDT']

    # [إعدادات العملات الجديدة والميمية - Meme/New Coins Mode]
    MEME_RISK_PER_TRADE_PERCENT = 0.25  # تخفيض المخاطرة لـ 0.25%
    MEME_LEVERAGE = 1                   # رافعة منخفضة 1x لضمان الأمان في الميم

    # [إعدادات الخروج الديناميكي - ATR TP/SL Config]
    USE_ATR_TARGETS = True              # تفعيل أهداف جني الأرباح ووقف الخسارة المبنية على ATR
    ATR_WINDOW = 14                     # عدد الشموع لحساب الـ ATR
    ATR_SL_MULTIPLIER = 1.5             # مضاعف وقف الخسارة للـ ATR
    ATR_TP_MULTIPLIER = 3.0             # مضاعف جني الأرباح للـ ATR (عائد لمخاطرة 1:2)

    # [إعدادات تقارير Testnet والمراقبة]
    TESTNET_REPORT_INTERVAL_HOURS = 24  # إرسال تقرير المقارنة والـ Testnet كل 24 ساعة
    DYNAMIC_BLACKLIST_ACTIVE = False    # إيقاف القائمة السوداء الديناميكية للـ Testnet
    
    # [إعدادات Testnet التفصيلية]
    TESTNET_DEBUG_MODE = True
    TESTNET_RELAXED_ENTRY = True
    TESTNET_MAX_OPEN_TRADES = 1
    TESTNET_RISK_MULTIPLIER = 1.0
    
    # ملاحظة هامة: هذا الشرط يعمل فقط في وضع التجريبي (Testnet) لمحاكاة محفظة صغيرة (مثلاً 50$). 
    # عندما تقوم بربط البوت بالحساب الحقيقي (USE_TESTNET=False)، سيتم إلغاء هذا الشرط تلقائياً وسيعتمد البوت على الرصيد الحقيقي بالكامل.
    TESTNET_SIMULATED_BALANCE = float(os.getenv("TESTNET_SIMULATED_BALANCE", 50.0))  
    
    # [إعدادات فلتر المشتقات - CoinGlass Filter]
    DERIVATIVES_FILTER_MODE = "enforce"     # off / audit (مراقبة فقط) / enforce (حظر فعلي)
    MAX_FUNDING_RATE_ABS = 0.001          # 0.1% الحد الأقصى لمعدل التمويل المطلق المسموح به
    OI_CHANGE_LOOKBACK = 4                # عدد الشموع لمراقبة تغير الاهتمام المفتوح
    MAX_LONG_SHORT_RATIO = 2.5            # الحد الأقصى لنسبة الشراء/البيع المزدحمة
    COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")
    
    # العملات المطلوب فرض فحصها واختبارها في بيئة Testnet
    TESTNET_FORCE_SYMBOLS = [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "DOGE/USDT",
        "XRP/USDT",
        "ADA/USDT",
        "AVAX/USDT",
        "NEAR/USDT",
        "LINK/USDT",
        "1000PEPE/USDT",
        
        # عملات السكالبينج الإضافية المقترحة
        "ZEC/USDT",
        "DEGEN/USDT",
        "HOME/USDT"
    ]


