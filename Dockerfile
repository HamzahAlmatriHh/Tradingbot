# استخدام نسخة بايثون خفيفة
FROM python:3.10-slim

# إعداد بيئة العمل
WORKDIR /app

# نسخ ملف المتطلبات وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات المشروع
COPY . .

# فتح منفذ خادم Mini App
EXPOSE 8080

# أمر تشغيل البوت
CMD ["python", "main.py"]
