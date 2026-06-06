import requests
from core.config import Config
from core.logger import logger

class SocialEngine:
    """
    محرك تحليل المشاعر الاجتماعية عبر LunarCrush API
    يوفر بيانات دقيقة عن مدى حديث الناس عن العملة + نسبة المشاعر الإيجابية/السلبية
    مثالي للعملات الصغيرة والميم التي لا تغطيها وسائل الإعلام التقليدية
    """
    
    def __init__(self):
        self.api_key = Config.LUNARCRUSH_API_KEY
        self.base_url = "https://lunarcrush.com/api4/public"
        
    def get_social_sentiment(self, coin_symbol: str):
        """
        جلب مشاعر السوق الاجتماعية لعملة معينة من LunarCrush
        
        يُعيد قاموساً يحتوي على:
        - label: 'positive', 'negative', 'neutral'
        - score: قوة الإشارة (0.0 - 1.0)
        - galaxy_score: نقاط LunarCrush الكلية (0-100)
        - social_volume: حجم التفاعل الاجتماعي
        - social_dominance: نسبة الهيمنة الاجتماعية
        """
        if not self.api_key:
            logger.debug("مفتاح LunarCrush غير متوفر.")
            return None
            
        # تحويل الرمز للشكل الصحيح (BTC/USDT -> BTC)
        symbol = coin_symbol.upper().replace('USDT', '').replace('/', '').replace(':', '').strip()
        
        url = f"{self.base_url}/coins/{symbol}/v1"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        
        try:
            logger.debug(f"[LunarCrush] جاري جلب مشاعر السوق الاجتماعية لـ {symbol}...")
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 404:
                logger.debug(f"[LunarCrush] {symbol} غير موجود في قاعدة بيانات LunarCrush.")
                return None
                
            response.raise_for_status()
            data = response.json().get('data', {})
            
            if not data:
                return None
                
            # استخراج مؤشرات المشاعر الرئيسية
            galaxy_score    = data.get('galaxy_score', 50)       # 0-100 (أعلى = أفضل)
            social_volume   = data.get('social_volume', 0)       # حجم التفاعل
            social_dom      = data.get('social_dominance', 0)    # نسبة الهيمنة
            sentiment_pct   = data.get('sentiment', 50)          # % مشاعر إيجابية (0-100)
            alt_rank        = data.get('alt_rank', 500)          # ترتيب LunarCrush (أقل = أفضل)
            
            logger.info(f"[LunarCrush] {symbol}: Galaxy={galaxy_score} | Sentiment={sentiment_pct}% | Volume={social_volume:,} | AltRank=#{alt_rank}")
            
            # تحويل نتائج LunarCrush لتنسيق موحد مع ai_engine
            # نعتمد على Galaxy Score و AltRank لأن Sentiment% قد يكون ثابتاً في الخطة المجانية
            #
            # Galaxy Score > 75 + AltRank منخفض جداً = إيجابي قوي
            # Galaxy Score < 40 = سلبي / عملة في حالة سيئة
            if galaxy_score >= 75 and alt_rank <= 100:
                label = 'positive'
                score = round(galaxy_score / 100, 2)
            elif galaxy_score >= 65 and alt_rank <= 200:
                label = 'positive'
                score = round(galaxy_score / 120, 2)
            elif galaxy_score < 40:
                label = 'negative'
                score = round((100 - galaxy_score) / 100, 2)
            else:
                label = 'neutral'
                score = 0.5
                
            return {
                'label': label,
                'score': min(score, 1.0),  # لا يتجاوز 1.0
                'galaxy_score': galaxy_score,
                'social_volume': social_volume,
                'social_dominance': social_dom,
                'raw_sentiment_pct': sentiment_pct,
                'source': 'lunarcrush'
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"[LunarCrush] فشل الاتصال لـ {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"[LunarCrush] خطأ غير متوقع لـ {symbol}: {e}")
            return None
