"""
Candle-Boundary Sync — بديل time.sleep() الذكي
يضمن أن البوت يبدأ دورة المسح بعد إغلاق الشمعة بـ 10 ثوانٍ بالضبط.
"""
import math
import time
from core.logger import logger


def seconds_to_next_candle(interval_seconds: int, buffer_seconds: int = 10) -> float:
    """
    يحسب الثواني اللازمة للانتظار حتى حد إغلاق الشمعة التالي + buffer.

    مثال مع interval=900 (15m):
      إذا الوقت الآن 14:07:23 → الشمعة القادمة تغلق في 14:15:00
      الانتظار = (14:15:10) - (14:07:23) = 467 ثانية

    لماذا هذا أفضل من time.sleep(900)؟
      - time.sleep(900) يبدأ العد من لحظة انتهاء المسح (متأخر أو متقدم)
      - هذه الدالة تحسب المتبقي حتى إغلاق منضبط على ساعة المنصة
      - يقضي على مشكلة Look-Ahead Bias (قراءة شمعة لم تُغلق بعد)
    """
    now  = time.time()
    # نجد أقرب حد لأعلى (ceil) من مضاعفات interval_seconds
    next_boundary = math.ceil(now / interval_seconds) * interval_seconds
    wait = next_boundary - now + buffer_seconds

    # حماية: لا يقل عن 30 ثانية لتجنب إعادة تشغيل فورية
    wait = max(wait, 30.0)

    next_dt = time.strftime("%H:%M:%S", time.localtime(next_boundary + buffer_seconds))
    logger.info(
        f"[CandleSync] الشمعة التالية تغلق في {next_dt} | "
        f"الانتظار: {wait:.0f}s ({wait/60:.1f} دقيقة)"
    )
    return wait
