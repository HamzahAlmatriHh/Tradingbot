import json
import os
import threading
import tempfile
import copy
from datetime import datetime
from core.logger import logger

STATE_FILE = os.getenv("STATE_FILE", "bot_state.json")

class StateManager:
    def __init__(self):
        self._lock = threading.RLock()
        self.state = {
            "last_reset_date": "",
            "initial_balance": 0.0,
            "active_symbols": [],
            "trailing_trades": {},
            "entry_metadata": {},
            "pair_locks": {},
            "consecutive_losses": {},
            "virtual_orders": {}
        }
        self.load_state()

    def get_state(self):
        with self._lock:
            return copy.deepcopy(self.state)

    def get(self, key, default=None):
        with self._lock:
            val = self.state.get(key, default)
            # إذا كانت القيمة dict أو list نعمل لها deepcopy لتجنب تعديلها خارج الـ lock
            if isinstance(val, (dict, list)):
                return copy.deepcopy(val)
            return val

    def set(self, key, value):
        with self._lock:
            self.state[key] = value
            return self.save_state()

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    saved = json.load(f)
                    # ادمج مع القيم الافتراضية لتجنب KeyError عند إضافة مفاتيح جديدة
                    self.state.update(saved)
                logger.info(f"تم تحميل الحالة المحفوظة. الصفقات المفتوحة: {self.state.get('active_symbols', [])}")
            except Exception as e:
                logger.error(f"فشل قراءة ملف الحالة، يبدأ من نقطة صفر: {e}")

    def save_state(self):
        try:
            with self._lock:
                directory = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
                fd, tmp_path = tempfile.mkstemp(dir=directory, prefix="bot_state_", suffix=".tmp")

                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(tmp_path, STATE_FILE)
                return True
        except Exception as e:
            logger.error(f"فشل حفظ ملف الحالة: {e}")
            return False

    # --- إدارة الرصيد اليومي ---
    def update_daily_balance(self, current_equity):
        """تحديث الرصيد المرجعي لبداية اليوم (يُعاد تعيينه مرة واحدة فقط يومياً)"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.state["last_reset_date"] != today:
            logger.info(f"يوم جديد! تعيين الرصيد المرجعي اليومي إلى: {current_equity:.2f} USDT")
            self.state["last_reset_date"] = today
            self.state["initial_balance"] = current_equity
            self.save_state()

    def get_initial_balance(self):
        return float(self.state.get("initial_balance", 0.0))

    # --- إدارة الصفقات المفتوحة ---
    def save_active_symbols(self, symbols_set):
        self.state["active_symbols"] = list(symbols_set)
        return self.save_state()

    def get_active_symbols(self):
        return set(self.state.get("active_symbols", []))

    def remove_active_symbol(self, symbol: str):
        """حذف رمز عملة واحدة من قائمة الصفقات المفتوحة"""
        symbols = self.get_active_symbols()
        symbols.discard(symbol)
        return self.save_active_symbols(symbols)

    # --- إدارة الستوب المتحرك (Trailing Stop Persistence) ---
    def save_trailing_trades(self, trades_dict):
        """حفظ حالة الستوب المتحرك (أفضل سعر، الستوب الحالي) لكل صفقة مفتوحة"""
        self.state["trailing_trades"] = trades_dict
        return self.save_state()

    def get_trailing_trades(self):
        """تحميل حالة الستوب المتحرك عند إعادة التشغيل بعد انقطاع"""
        return self.state.get("trailing_trades", {})

    def save_entry_metadata(self, symbol: str, metadata: dict):
        """حفظ مؤشرات وبيانات دخول الصفقة لاستردادها عند الإغلاق"""
        if "entry_metadata" not in self.state:
            self.state["entry_metadata"] = {}
        self.state["entry_metadata"][symbol] = metadata
        self.save_state()

    def pop_entry_metadata(self, symbol: str) -> dict:
        """استرداد وحذف بيانات دخول الصفقة بعد إغلاقها لتسجيلها في الملف المحلي"""
        if "entry_metadata" not in self.state:
            return {}
        meta = self.state.get("entry_metadata", {}).pop(symbol, {})
        self.save_state()
        return meta

    def increment_rejection_counter(self, filter_name: str):
        """زيادة عداد الفلاتر المرفوضة (مثل Anti-Pump, Spread, Margin)"""
        if "filter_rejections" not in self.state:
            self.state["filter_rejections"] = {}
        self.state["filter_rejections"][filter_name] = self.state["filter_rejections"].get(filter_name, 0) + 1
        self.save_state()

    def get_rejection_counters(self) -> dict:
        """الحصول على عدادات التصفية والفلاتر المرفوضة الحالية"""
        return self.state.get("filter_rejections", {})

    def reset_rejection_counters(self):
        """تصفير عدادات الفلاتر للبدء من جديد (يومياً)"""
        self.state["filter_rejections"] = {}
        self.save_state()

    # --- إدارة تبريد العملات (PairLocks) ---
    def record_trade_result(self, symbol: str, is_profit: bool):
        """تسجيل نتيجة الصفقة لمعرفة الخسائر المتتالية"""
        if "consecutive_losses" not in self.state:
            self.state["consecutive_losses"] = {}
            
        if is_profit:
            self.state["consecutive_losses"][symbol] = 0
        else:
            self.state["consecutive_losses"][symbol] = self.state["consecutive_losses"].get(symbol, 0) + 1
            
        self.save_state()
        return self.state["consecutive_losses"][symbol]

    def lock_pair(self, symbol: str, lock_until_timestamp: float, reason: str):
        """قفل العملة ومنع التداول عليها حتى وقت محدد"""
        if "pair_locks" not in self.state:
            self.state["pair_locks"] = {}
        self.state["pair_locks"][symbol] = {
            "lock_until": lock_until_timestamp,
            "reason": reason
        }
        self.save_state()

    def is_pair_locked(self, symbol: str) -> bool:
        """التحقق مما إذا كانت العملة مقفولة حالياً"""
        locks = self.state.get("pair_locks", {})
        if symbol in locks:
            lock_until = locks[symbol].get("lock_until", 0)
            if datetime.now().timestamp() < lock_until:
                return True
            else:
                # انتهى الحظر، نقوم بالتنظيف
                del self.state["pair_locks"][symbol]
                # تصفير عداد الخسائر ليعطى فرصة جديدة
                if "consecutive_losses" in self.state and symbol in self.state["consecutive_losses"]:
                    self.state["consecutive_losses"][symbol] = 0
                self.save_state()
        return False
    
    def get_pair_lock_info(self, symbol: str) -> dict:
        return self.state.get("pair_locks", {}).get(symbol, {})

    # --- إدارة الأوامر المعلقة الافتراضية (Virtual Pending Orders / Sniper) ---
    def save_virtual_order(self, symbol: str, order_data: dict):
        """حفظ أمر معلق افتراضي لانتظار السعر المثالي"""
        if "virtual_orders" not in self.state:
            self.state["virtual_orders"] = {}
        self.state["virtual_orders"][symbol] = order_data
        self.save_state()

    def get_virtual_orders(self) -> dict:
        """جلب جميع الأوامر المعلقة"""
        return self.state.get("virtual_orders", {})

    def remove_virtual_order(self, symbol: str):
        """حذف أمر معلق افتراضي (إما تم تنفيذه أو انتهت صلاحيته)"""
        if "virtual_orders" in self.state and symbol in self.state["virtual_orders"]:
            del self.state["virtual_orders"][symbol]
            self.save_state()


