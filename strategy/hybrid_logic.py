from core.logger import logger

class HybridStrategy:
    """
    استراتيجية الفيتو المتقدمة (Advanced Veto Architecture)
    لا يحق للذكاء الاصطناعي بدء صفقة إذا كان التحليل الفني محايداً.
    دوره فقط تقليل المخاطرة أو المنع (Veto) في حال وجود أخبار قوية.
    """
    def __init__(self):
        self.veto_threshold = 0.70          # الحد الذي يمنع الصفقة تماماً
        self.risk_reduce_threshold = 0.55   # الحد الذي يقلل حجم الصفقة للنصف

    def decide(self, ta_signal: str, sentiment: dict, ta_data: dict = None):
        sentiment = self._normalize_sentiment(sentiment)
        
        label = sentiment["label"]
        score = sentiment["score"]

        decision = {
            "action": "hold",
            "risk_multiplier": 1.0,
            "reason": ""
        }

        # 1. فلتر الفيتو الفني (لا بيانات = لا صفقة)
        if not ta_data:
            decision["reason"] = "بيانات فنية مفقودة، تم إلغاء التنفيذ كإجراء وقائي."
            return decision

        # 2. الفيتو الذكي (لا SMC = لا دخول)
        if ta_signal not in ["buy", "sell"]:
            decision["reason"] = "لم يتم تأكيد الدخول من محرك SMC. (الذكاء الاصطناعي ممنوع من بدء الصفقات)."
            return decision

        # 3. معالجة الشراء (LONG)
        if ta_signal == "buy":
            if label == "negative" and score >= self.veto_threshold:
                decision["action"] = "hold"
                decision["risk_multiplier"] = 0.0
                decision["reason"] = "تم نقض الشراء (Veto): أخبار سلبية قوية جداً."
                return decision

            if label == "negative" and score >= self.risk_reduce_threshold:
                decision["action"] = "buy"
                decision["risk_multiplier"] = 0.5
                decision["reason"] = "شراء مسموح، لكن تم خفض حجم الصفقة 50% بسبب أخبار سلبية."
                return decision

            decision["action"] = "buy"
            decision["reason"] = "شراء مؤكد: إشارة SMC نقية."
            return decision

        # 4. معالجة البيع (SHORT)
        if ta_signal == "sell":
            if label == "positive" and score >= self.veto_threshold:
                decision["action"] = "hold"
                decision["risk_multiplier"] = 0.0
                decision["reason"] = "تم نقض البيع (Veto): أخبار إيجابية قوية جداً."
                return decision

            if label == "positive" and score >= self.risk_reduce_threshold:
                decision["action"] = "sell"
                decision["risk_multiplier"] = 0.5
                decision["reason"] = "بيع مسموح، لكن تم خفض حجم الصفقة 50% بسبب أخبار إيجابية."
                return decision

            decision["action"] = "sell"
            decision["reason"] = "بيع مؤكد: إشارة SMC نقية."
            return decision

        return decision

    def _normalize_sentiment(self, sentiment):
        """تأكيد صحة كائن المشاعر ومطابقته للمعايير"""
        if not sentiment:
            return {"label": "neutral", "score": 0.0}

        label = str(sentiment.get("label", "neutral")).lower().strip()
        score = float(sentiment.get("score", 0.0) or 0.0)

        if label not in ["positive", "negative", "neutral"]:
            label = "neutral"

        score = max(0.0, min(score, 1.0))

        # في التحديث المستقبلي يمكننا تفعيل فحص (symbol) و (timestamp) هنا إذا تم توفيرهم في ai_engine
        
        return {
            "label": label,
            "score": score
        }
