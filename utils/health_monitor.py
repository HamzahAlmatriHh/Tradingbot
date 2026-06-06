import time
import psutil
from datetime import datetime
from core.logger import logger
from core.config import Config

class HealthMonitor:
    def __init__(self, client, state_manager, notifier):
        self.client = client
        self.state_manager = state_manager
        self.notifier = notifier
        self.start_time = time.time()
        
    def check_system_health(self):
        """جمع بيانات صحة الخادم (CPU, RAM, Uptime)"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            ram_percent = memory.percent
            
            uptime_seconds = time.time() - self.start_time
            uptime_hours = uptime_seconds / 3600
            
            return {
                "cpu": cpu_percent,
                "ram": ram_percent,
                "uptime_hours": uptime_hours
            }
        except Exception as e:
            logger.error(f"خطأ في قراءة صحة النظام: {e}")
            return None

    def check_exchange_connection(self):
        """فحص جودة الاتصال بباينانس والـ Latency"""
        try:
            start_ping = time.time()
            # فحص خفيف للتأكد من الشبكة
            self.client.exchange.fetch_time()
            latency_ms = (time.time() - start_ping) * 1000
            return {"status": "متصل 🟢", "latency": latency_ms}
        except Exception as e:
            logger.warning(f"تحذير من اتصال باينانس: {e}")
            return {"status": "مفصول 🔴", "latency": 0}

    def generate_health_report(self):
        sys_health = self.check_system_health()
        conn_health = self.check_exchange_connection()
        
        # جلب عدد الصفقات المعلقة (Sniper) والمفتوحة
        virtual_count = len(self.state_manager.get_virtual_orders())
        active_count = len(self.state_manager.get_active_symbols())
        locked_count = len(self.state_manager.state.get("pair_locks", {}))
        
        msg = "🩺 <b>تقرير صحة البوت (Health Monitor)</b> 🩺\n\n"
        
        # معلومات الخادم
        if sys_health:
            cpu_emoji = "🔥" if sys_health['cpu'] > 80 else "🟢"
            ram_emoji = "🔥" if sys_health['ram'] > 85 else "🟢"
            msg += f"🖥️ <b>المعالج (CPU):</b> <code>{sys_health['cpu']}%</code> {cpu_emoji}\n"
            msg += f"💾 <b>الذاكرة (RAM):</b> <code>{sys_health['ram']}%</code> {ram_emoji}\n"
            msg += f"⏱️ <b>مدة التشغيل:</b> <code>{sys_health['uptime_hours']:.1f} ساعة</code>\n\n"
        
        # معلومات الشبكة
        lat_emoji = "🟢" if conn_health['latency'] < 500 else ("🟡" if conn_health['latency'] < 1000 else "🔴")
        msg += f"🌐 <b>حالة الاتصال:</b> {conn_health['status']}\n"
        if conn_health['latency'] > 0:
            msg += f"⚡ <b>سرعة الاستجابة (Ping):</b> <code>{conn_health['latency']:.0f} ms</code> {lat_emoji}\n\n"
            
        # حالة الدورة والإحصاءات
        msg += f"📊 <b>صفقات مفتوحة:</b> <code>{active_count}</code>\n"
        msg += f"⏳ <b>فخاخ القناص:</b> <code>{virtual_count}</code>\n"
        msg += f"🔒 <b>عملات محظورة مؤقتاً:</b> <code>{locked_count}</code>\n"
        
        virtual_orders = self.state_manager.get_virtual_orders()
        if virtual_orders:
            msg += "\n⏳ <b>تفاصيل فخاخ القناص:</b>\n"
            now = datetime.now()
            
            def _parse_dt_safe(value):
                try:
                    if isinstance(value, float) or isinstance(value, int):
                        return datetime.fromtimestamp(value)
                    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    return None

            for sym, vo in list(virtual_orders.items())[:5]:
                created_at = _parse_dt_safe(vo.get("created_at"))
                expires_at = _parse_dt_safe(vo.get("expires_at"))

                age_txt = "غير معروف"
                left_txt = "غير معروف"

                if created_at:
                    age_min = int((now - created_at).total_seconds() / 60)
                    age_txt = f"{age_min} دقيقة"

                if expires_at:
                    left_min = int((expires_at - now).total_seconds() / 60)
                    left_txt = f"{max(left_min, 0)} دقيقة"

                msg += (
                    f"• <code>{sym}</code> | "
                    f"عمر: <code>{age_txt}</code> | "
                    f"ينتهي بعد: <code>{left_txt}</code>\n"
                )
                
        return msg
        
    def send_health_report(self):
        """إرسال التقرير لتليجرام"""
        msg = self.generate_health_report()
        self.notifier.send_message(msg)
