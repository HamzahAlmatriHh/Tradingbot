import requests
import time
from core.config import Config
from core.logger import logger

class NewsEngine:
    def __init__(self):
        self.newsapi_key = Config.NEWS_API_KEY
        self.base_url = "https://newsapi.org/v2/everything"
        self._cache = {}  # {coin_name: {'data': news_list, 'timestamp': time.time()}}
        self.cache_ttl = 3600  # نحتفظ بالأخبار لمدة ساعة (3600 ثانية) لتوفير الكوتا

    def fetch_news_for_coin(self, coin_name, page_size=3):
        """
        جلب أخبار مخصصة للعملة المحددة فقط.
        الحل: نبحث عن اسم العملة مع كلمات الكريبتو بدون OR لكلمات عامة
        """
        if not self.newsapi_key or self.newsapi_key == "your_news_api_key_here":
            logger.warning("مفتاح NewsAPI غير متوفر.")
            return []

        # 1. فحص الـ Cache أولاً لتجنب استهلاك كوتا الـ API المجانية
        if coin_name in self._cache:
            cache_entry = self._cache[coin_name]
            if time.time() - cache_entry['timestamp'] < self.cache_ttl:
                logger.debug(f"استرجاع أخبار '{coin_name}' من الـ Cache (لتوفير كوتا NewsAPI).")
                return cache_entry['data']

        # البحث الدقيق: اسم العملة + كلمات الكريبتو فقط
        # لا نضيف OR "Federal Reserve" لأنه يُعيد أخباراً عامة لا علاقة لها بالعملة
        query = f'"{coin_name}" AND (crypto OR cryptocurrency OR token OR blockchain OR price OR trading)'
        
        # مصادر موثوقة للحد من العناوين المضللة (Clickbait) كما اقترح الصديق
        domains = "reuters.com,bloomberg.com,coindesk.com,cointelegraph.com,cnbc.com,wsj.com"
        
        params = {
            "q": query,
            "domains": domains,
            "language": "en",
            "sortBy": "publishedAt",  # الأحدث أولاً لأن الكريبتو يتحرك بسرعة
            "pageSize": page_size,
            "apiKey": self.newsapi_key
        }
        
        try:
            logger.debug(f"جاري جلب أخبار مخصصة لـ '{coin_name}' من NewsAPI...")
            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            articles = data.get("articles", [])
            
            # التحقق أن الخبر فعلاً يذكر العملة (فلترة إضافية)
            relevant = [a for a in articles if coin_name.lower() in (a.get('title', '') + a.get('description', '')).lower()]
            
            
            if relevant:
                logger.debug(f"تم إيجاد {len(relevant)} خبر مخصص لـ {coin_name}")
                self._cache[coin_name] = {'data': relevant, 'timestamp': time.time()}
                return relevant
            else:
                logger.debug(f"لا توجد أخبار مخصصة كافية لـ {coin_name} - تخطي تحليل الأخبار")
                self._cache[coin_name] = {'data': [], 'timestamp': time.time()}
                return []
                
        except Exception as e:
            logger.error(f"فشل الاتصال بـ NewsAPI: {e}")
            
        return []
