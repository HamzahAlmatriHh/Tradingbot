from groq import Groq
from core.config import Config
from core.logger import logger
import json

class AIEngine:
    def __init__(self):
        self.api_key = Config.GROQ_API_KEY
        if self.api_key:
            self.client = Groq(api_key=self.api_key)
        else:
            self.client = None
        # استخدام الموديل الأسرع والأذكى Llama-3.3-70B
        self.model_id = "llama-3.3-70b-versatile"

    def analyze_sentiment(self, text):
        """
        إرسال النص إلى Groq Llama 3 لتحليل المشاعر وإرجاع النتيجة
        """
        if not self.client:
            logger.warning("مفتاح Groq غير متوفر. تخطي تحليل المشاعر.")
            return None
            
        prompt = f"""
Analyze the following financial/crypto news content and determine its sentiment impact on the cryptocurrency market (especially Bitcoin).
You must respond with ONLY a valid JSON object containing exactly two keys: "label" (which must be exactly "positive", "negative", or "neutral") and "score" (a confidence score between 0.00 and 1.00).

News Content: "{text}"

JSON Output:
"""
        try:
            logger.debug("جاري تحليل المشاعر عبر Groq Llama-3...")
            response = self.client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model=self.model_id,
                temperature=0.0, # دقة 100% بدون هلوسة
                response_format={"type": "json_object"}
            )
            
            result_str = response.choices[0].message.content
            result_json = json.loads(result_str)
            
            return {
                "label": result_json.get("label", "neutral").lower(),
                "score": float(result_json.get("score", 0.0))
            }
            
        except Exception as e:
            logger.critical(f"خطأ غير متوقع في محرك الذكاء الاصطناعي (Groq): {e}")
            
        return None

    def generate_interactive_analysis(self, symbol, current_price, ta_data, news_text, social_text):
        """
        استخدام الذكاء الاصطناعي لتحليل البيانات الفنية والأخبار وتوليد توصية تداول فورية للمستخدم باللغة العربية
        """
        if not self.client:
            return "⚠️ تعذر توليد التحليل الذكي لأن مفتاح الذكاء الاصطناعي غير متوفر."
            
        prompt = f"""
You are an expert crypto technical analyst and financial advisor.
Your user asked for an instant analysis of the coin: {symbol}.

Here is the real-time data collected just now:
- Current Price: ${current_price}
- Technical Indicators (TA):
  - RSI (14): {ta_data.get('rsi_14', 'N/A')}
  - EMA 50: {ta_data.get('ema_50', 'N/A')}
  - EMA 200: {ta_data.get('ema_200', 'N/A')}
  - ATR (Volatility): {ta_data.get('atr', 'N/A')}
  - ADX (Trend Strength): {ta_data.get('adx', 'N/A')}
  - MACD Histogram: {ta_data.get('macd_hist', 'N/A')}
  
- Latest News Context: "{news_text}"
- Social Sentiment (Twitter/Reddit): "{social_text}"

Based on this raw data, generate a Highly Professional, well-structured, and VERY SHORT Trading Advice Report in ARABIC ONLY.
Your report must strictly follow this format (use exactly these emojis and structure):

🪙 **العملة:** {symbol}
💰 **السعر اللحظي:** {current_price} $

📊 **التحليل الفني:** (1 short sentence explaining RSI, EMA, and MACD).
🛡️ **دعم مقترح:** (Value based on ATR)
⚔️ **مقاومة مقترحة:** (Value based on ATR)
📰 **المشاعر والأخبار:** (1 short sentence).

💡 **القرار الذكي:** (LONG, SHORT, or WAIT in 1 short sentence).
🎯 **الهدف (TP):** (Value) | 🛑 **الوقف (SL):** (Value)
⚖️ **حجم الدخول:** (1% to 2%)

CRITICAL INSTRUCTIONS:
- DO NOT hallucinate.
- DO NOT use Chinese, Korean, or any Asian characters at all. Use ONLY pure Arabic letters and numbers.
- Keep the response extremely short and easy to read on a mobile screen.
- DO NOT use code blocks or markdown brackets outside of bolding text.
"""
        try:
            logger.info(f"جاري توليد التقرير الذكي لعملة {symbol}...")
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model_id,
                temperature=0.3 # دقة أعلى وقليل من الإبداع للتحليل
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"فشل توليد التقرير الذكي: {e}")
            return "⚠️ عذراً، حدث خطأ أثناء تحليل البيانات باستخدام الذكاء الاصطناعي."
