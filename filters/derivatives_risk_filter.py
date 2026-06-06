import os
import csv
import random
import requests
from datetime import datetime
from core.config import Config
from core.logger import logger

class DerivativesRiskFilter:
    def __init__(self):
        # محاولة جلب مفتاح CoinGlass من الإعدادات أو البيئة
        self.api_key = getattr(Config, 'COINGLASS_API_KEY', os.getenv("COINGLASS_API_KEY"))
        self.base_url = "https://open-api.coinglass.com/public/v2"
        
    def evaluate_risk(self, symbol: str, side: str, price: float) -> dict:
        """
        تقييم مخاطر المشتقات لزوج معين واتجاه صفقة معين.
        تُرجع قاموس يحتوي على:
        - decision: 'ALLOW' | 'WARNING' | 'BLOCK_SUGGESTED'
        - funding_rate: float
        - oi_change_pct: float
        - long_short_ratio: float
        - liquidation_bias: str (إشارة للتصفية إن وجدت)
        - reason: str
        - is_mock: bool
        """
        # قيم افتراضية محايدة
        funding_rate = 0.0
        oi_change_pct = 0.0
        long_short_ratio = 1.0
        liquidation_bias = "NEUTRAL"
        is_mock = True
        
        coin_name = symbol.split('/')[0]
        
        # محاولة جلب البيانات الحقيقية من CoinGlass إذا كان المفتاح متوفراً
        if self.api_key:
            try:
                headers = {
                    "coinglassApiKey": self.api_key,
                    "accept": "application/json"
                }
                
                # 1. جلب معدل التمويل (Funding Rate) والاهتمام المفتوح (Open Interest) للعملة
                # نستخدم endpoints العامة لكوين جلاس
                oi_url = f"{self.base_url}/open_interest?symbol={coin_name}"
                response = requests.get(oi_url, headers=headers, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == '0' and data.get('data'):
                        # استخلاص معدل التمويل وتغير الاهتمام المفتوح الفعلي
                        items = data['data']
                        if isinstance(items, list) and len(items) > 0:
                            first_item = items[0]
                            funding_rate = float(first_item.get('fundingRate', 0.0))
                            oi_change_pct = float(first_item.get('h24Change', 0.0)) / 100.0  # تحويل النسبة المئوية
                            is_mock = False
                            
                # 2. جلب نسبة الشراء إلى البيع (Long/Short Ratio)
                ls_url = f"{self.base_url}/long_short?symbol={coin_name}&timeframe=1h"
                ls_response = requests.get(ls_url, headers=headers, timeout=5)
                if ls_response.status_code == 200:
                    ls_data = ls_response.json()
                    if ls_data.get('code') == '0' and ls_data.get('data'):
                        items = ls_data['data']
                        if isinstance(items, list) and len(items) > 0:
                            long_short_ratio = float(items[0].get('longShortRatio', 1.0))
                            is_mock = False
                            
            except Exception as e:
                logger.warning(f"⚠️ فشل جلب البيانات الحقيقية من CoinGlass لـ {symbol} (سيتم استخدام محاكاة): {e}")

        # إذا كانت البيانات محاكاة (Mock Data) للتجريب والتدقيق
        if is_mock:
            # توليد بيانات عشوائية ولكن واقعية تتماشى مع طبيعة تذبذبات السوق
            funding_rate = random.uniform(-0.0006, 0.0016)   # بين -0.06% و +0.16%
            oi_change_pct = random.uniform(-0.06, 0.09)      # بين -6% و +9%
            long_short_ratio = random.uniform(0.7, 2.8)      # بين 0.7 و 2.8
            # تصفية عشوائية
            liquidation_bias = random.choice(["LONG_LIQ_RISK", "SHORT_LIQ_RISK", "NEUTRAL"])
            
        # منطق اتخاذ القرار وتصنيف الخطورة
        decision = "ALLOW"
        reasons = []
        
        # 1. فحص معدل التمويل (Funding Rate) ضد الحد الأقصى المطلق المسموح به
        max_funding = getattr(Config, 'MAX_FUNDING_RATE_ABS', 0.001)  # 0.1%
        if abs(funding_rate) > max_funding:
            if side.upper() == 'BUY' and funding_rate > max_funding:
                decision = "BLOCK_SUGGESTED"
                reasons.append(f"معدل تمويل موجب مرتفع جداً ({funding_rate:.4%}) يهدد بـ Long Squeeze")
            elif side.upper() == 'SELL' and funding_rate < -max_funding:
                decision = "BLOCK_SUGGESTED"
                reasons.append(f"معدل تمويل سالب مرتفع جداً ({funding_rate:.4%}) يهدد بـ Short Squeeze")
                
        # 2. فحص تغير الاهتمام المفتوح (Open Interest Change)
        # إذا دخلنا شراء والسعر صاعد بينما الاهتمام المفتوح يهبط بقوة، فهذا مؤشر ضعف
        if side.upper() == 'BUY' and oi_change_pct < -0.03:
            if decision != "BLOCK_SUGGESTED":
                decision = "WARNING"
            reasons.append(f"صعود سعري مصحوب بهبوط حاد في الاهتمام المفتوح ({oi_change_pct:+.2%})")
            
        # 3. فحص نسبة الشراء إلى البيع (Long/Short Ratio) ضد الحد الأقصى المسموح
        max_ls_ratio = getattr(Config, 'MAX_LONG_SHORT_RATIO', 2.5)
        if side.upper() == 'BUY' and long_short_ratio > max_ls_ratio:
            if decision != "BLOCK_SUGGESTED":
                decision = "WARNING"
            reasons.append(f"ازدحام متطرف في صفقات الشراء (L/S Ratio = {long_short_ratio:.2f})")
        elif side.upper() == 'SELL' and long_short_ratio < (1.0 / max_ls_ratio):
            if decision != "BLOCK_SUGGESTED":
                decision = "WARNING"
            reasons.append(f"ازدحام متطرف في صفقات البيع (L/S Ratio = {long_short_ratio:.2f})")
            
        # 4. فحص خطر التصفية المبيتة (Liquidation Risk Bias)
        if side.upper() == 'BUY' and liquidation_bias == "LONG_LIQ_RISK":
            if decision != "BLOCK_SUGGESTED":
                decision = "WARNING"
            reasons.append("مخاطر تصفية صفقات الشراء (Long Liquidation Risk) مرتفعة بالقرب من السعر الحالي")
        elif side.upper() == 'SELL' and liquidation_bias == "SHORT_LIQ_RISK":
            if decision != "BLOCK_SUGGESTED":
                decision = "WARNING"
            reasons.append("مخاطر تصفية صفقات البيع (Short Liquidation Risk) مرتفعة بالقرب من السعر الحالي")
            
        final_reason = " | ".join(reasons) if reasons else "مؤشرات المشتقات صحية وضمن الحدود الآمنة"
        
        return {
            'decision': decision,
            'funding_rate': funding_rate,
            'oi_change_pct': oi_change_pct,
            'long_short_ratio': long_short_ratio,
            'liquidation_bias': liquidation_bias,
            'reason': final_reason,
            'is_mock': is_mock
        }
        
    def log_audit_result(self, symbol: str, side: str, price: float, result: dict):
        """
        تسجيل نتائج فحص المشتقات في ملف CSV مستقل للمقارنة والتحليل اللاحق
        """
        file_path = "derivatives_audit_log.csv"
        file_exists = os.path.exists(file_path)
        
        headers = [
            "timestamp", "symbol", "side", "price", "decision", 
            "funding_rate", "oi_change_pct", "long_short_ratio", 
            "liquidation_bias", "reason", "is_mock"
        ]
        
        row = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "side": side.upper(),
            "price": price,
            "decision": result["decision"],
            "funding_rate": f"{result['funding_rate']:.5f}",
            "oi_change_pct": f"{result['oi_change_pct']:.4f}",
            "long_short_ratio": f"{result['long_short_ratio']:.2f}",
            "liquidation_bias": result.get("liquidation_bias", "NEUTRAL"),
            "reason": result["reason"],
            "is_mock": str(result["is_mock"])
        }
        
        try:
            # التأكد من إنشاء المجلد الرئيسي إذا لزم الأمر (عادة في المسار الحالي مباشرة)
            with open(file_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row)
            logger.info(f"📊 [تدقيق كوين جلاس] تم تسجيل تدقيق المشتقات لـ {symbol} في {file_path}")
        except Exception as e:
            logger.error(f"❌ فشل تسجيل تدقيق المشتقات في ملف CSV: {e}")
