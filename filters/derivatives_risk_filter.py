import os
import csv
import random
import requests
from datetime import datetime
from core.config import Config
from core.logger import logger
from utils.api_key_pool import APIKeyPool

class DerivativesRiskFilter:
    def __init__(self, state_manager=None):
        self.state_manager = state_manager
        self.api_keys = getattr(Config, "COINGLASS_API_KEYS", [])

        if not self.api_keys:
            single = getattr(Config, 'COINGLASS_API_KEY', os.getenv("COINGLASS_API_KEY"))
            self.api_keys = [single] if single else []

        self.key_pool = APIKeyPool(
            service_name="coinglass",
            keys=self.api_keys,
            state_manager=state_manager
        )

        self.base_url = "https://open-api-v4.coinglass.com/api/futures"
        
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
        
        keys_to_try = self.key_pool.get_available_keys() if self.key_pool else self.api_keys

        for api_key in keys_to_try:
            if not api_key:
                continue

            try:
                headers = {
                    "CG-API-KEY": api_key,
                    "accept": "application/json"
                }

                oi_url = f"{self.base_url}/open-interest/exchange-list"
                response = requests.get(
                    oi_url,
                    headers=headers,
                    params={"symbol": coin_name},
                    timeout=5
                )

                if response.status_code in [401, 403, 429]:
                    if self.key_pool:
                        self.key_pool.mark_failure(
                            api_key,
                            status_code=response.status_code,
                            reason=response.text[:200],
                            cooldown_seconds=15 * 60 if response.status_code == 429 else 6 * 60 * 60
                        )

                    logger.warning(
                        f"[CoinGlass] فشل المفتاح الحالي status={response.status_code}. تجربة المفتاح التالي..."
                    )
                    continue

                if response.status_code != 200:
                    if self.key_pool:
                        self.key_pool.mark_failure(
                            api_key,
                            status_code=response.status_code,
                            reason=response.text[:200],
                            cooldown_seconds=5 * 60
                        )
                    continue

                data = response.json()

                if not data:
                    if self.key_pool:
                        self.key_pool.mark_failure(api_key, status_code=200, reason="Empty CoinGlass response")
                    continue

                # قراءة مرنة تدعم أشكال مختلفة من v4 response
                items = data.get("data", [])
                if isinstance(items, dict):
                    items = items.get("list") or items.get("data") or []

                if not items:
                    if self.key_pool:
                        self.key_pool.mark_failure(api_key, status_code=200, reason=f"No data: {str(data)[:150]}")
                    continue

                first_item = items[0] if isinstance(items, list) else {}

                oi_change_pct = float(
                    first_item.get("h24Change")
                    or first_item.get("changePercent")
                    or first_item.get("change")
                    or 0.0
                ) / 100.0

                is_mock = False

                # long_short_ratio يبقى محايداً حتى نتأكد من endpoint v4 الخاص به
                long_short_ratio = 1.0

                if self.key_pool:
                    self.key_pool.mark_success(api_key)

                break

            except Exception as e:
                if self.key_pool:
                    self.key_pool.mark_failure(
                        api_key,
                        status_code=0,
                        reason=str(e),
                        cooldown_seconds=5 * 60
                    )

                logger.warning(f"[CoinGlass] خطأ مع أحد المفاتيح، تجربة المفتاح التالي: {e}")
                continue

        # إذا كانت البيانات غير متاحة (CoinGlass API فشل)
        if is_mock:
            # ✅ إصلاح: لا نستخدم أرقاماً عشوائية لاتخاذ قرارات مالية حقيقية.
            # بدلاً من ذلك، نُرجع قراراً محايداً صريحاً مع علامة is_mock=True
            # حتى يمكن للنظام الخارجي التعامل معها بوعي (تسجيل فقط، عدم الحظر).
            logger.warning(
                f"[CoinGlass] ⚠️ لا تتوفر بيانات مشتقات حقيقية لـ {symbol}. "
                f"القرار محايد (ALLOW) بسبب غياب البيانات — لن يُحظر التداول بناءً على بيانات وهمية."
            )
            return {
                "decision": "ALLOW",
                "funding_rate": 0.0,
                "oi_change_pct": 0.0,
                "long_short_ratio": 1.0,
                "liquidation_bias": "UNKNOWN",
                "reason": "بيانات CoinGlass غير متاحة — تم السماح بالمرور بشكل محايد (لا يُحظر بأرقام عشوائية).",
                "is_mock": True,
            }
            
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
