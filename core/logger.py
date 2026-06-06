import os
import logging
from logging.handlers import TimedRotatingFileHandler

def setup_logger(name="TradingBot"):
    # مسار مجلد السجلات
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # لمنع تكرار إضافة الـ handlers إذا تم استدعاء الدالة أكثر من مرة
    if not logger.handlers:
        log_format = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # 1. طباعة السجلات في موجه الأوامر (Console)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(log_format)

        # 2. كتابة السجلات في ملفات مع التناوب اليومي (Log Rotation)
        log_file = os.path.join(log_dir, "bot_activity.log")
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",     # التناوب يحدث في منتصف الليل
            interval=1,
            backupCount=30,      # الاحتفاظ بسجلات آخر 30 يوماً
            encoding="utf-8"
        )
        file_handler.suffix = "%Y-%m-%d" # إضافة التاريخ لاسم الملف
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(log_format)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger

# كائن عام للاستخدام المباشر في باقي الملفات
logger = setup_logger()
