# utils/api_key_pool.py

import time
import hashlib
from core.logger import logger


class APIKeyPool:
    """
    يدير مجموعة مفاتيح API لخدمة واحدة.
    لا يطبع المفاتيح أبداً.
    يحفظ حالة كل مفتاح داخل state_manager:
    - rate_limited_until
    - disabled_until
    - fail_count
    - success_count
    - last_status
    """

    def __init__(self, service_name, keys, state_manager=None):
        self.service_name = service_name
        self.keys = [k for k in keys if k]
        self.state_manager = state_manager

    def _fingerprint(self, key):
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]

    def _state_key(self):
        return f"api_key_pool:{self.service_name}"

    def _load_state(self):
        if not self.state_manager:
            return {}
        return self.state_manager.get(self._state_key(), {})

    def _save_state(self, state):
        if self.state_manager:
            self.state_manager.set(self._state_key(), state)

    def get_available_keys(self, allow_exhausted=True):
        """
        يرجع المفاتيح المتاحة الآن.
        المفاتيح التي عليها cooldown يتم تأخيرها.
        """
        now = time.time()
        state = self._load_state()

        available = []

        for index, key in enumerate(self.keys):
            fp = self._fingerprint(key)
            meta = state.get(fp, {})

            rate_limited_until = float(meta.get("rate_limited_until", 0) or 0)
            disabled_until = float(meta.get("disabled_until", 0) or 0)

            if disabled_until > now or rate_limited_until > now:
                continue

            available.append(key)

        if not available:
            return self.keys if allow_exhausted else []

        return available

    def mark_success(self, key):
        state = self._load_state()
        fp = self._fingerprint(key)
        meta = state.get(fp, {})

        meta["last_status"] = "OK"
        meta["last_success_at"] = time.time()
        meta["success_count"] = int(meta.get("success_count", 0) or 0) + 1
        meta["fail_count"] = 0
        meta["rate_limited_until"] = 0

        state[fp] = meta
        self._save_state(state)

    def mark_failure(self, key, status_code=None, reason="", cooldown_seconds=None):
        state = self._load_state()
        fp = self._fingerprint(key)
        meta = state.get(fp, {})

        now = time.time()
        status_code = int(status_code or 0)

        meta["last_status"] = "FAIL"
        meta["last_failure_at"] = now
        meta["last_status_code"] = status_code
        meta["last_reason"] = str(reason)[:250]
        meta["fail_count"] = int(meta.get("fail_count", 0) or 0) + 1

        # 429 = Rate limit مؤقت
        if status_code == 429:
            meta["rate_limited_until"] = now + (cooldown_seconds or 15 * 60)

        # 401/403 غالباً مفتاح غير صالح أو ممنوع
        elif status_code in [401, 403]:
            meta["disabled_until"] = now + (cooldown_seconds or 6 * 60 * 60)

        # أخطاء مؤقتة / Timeout / 5xx
        else:
            meta["rate_limited_until"] = now + (cooldown_seconds or 5 * 60)

        state[fp] = meta
        self._save_state(state)

    def stats(self):
        state = self._load_state()
        now = time.time()

        rows = []
        for i, key in enumerate(self.keys, start=1):
            fp = self._fingerprint(key)
            meta = state.get(fp, {})

            rate_limited_until = float(meta.get("rate_limited_until", 0) or 0)
            disabled_until = float(meta.get("disabled_until", 0) or 0)

            if disabled_until > now:
                status = "DISABLED"
            elif rate_limited_until > now:
                status = "COOLDOWN"
            else:
                status = meta.get("last_status", "READY")

            rows.append({
                "index": i,
                "fingerprint": fp,
                "status": status,
                "success_count": int(meta.get("success_count", 0) or 0),
                "fail_count": int(meta.get("fail_count", 0) or 0),
                "last_status_code": meta.get("last_status_code"),
                "last_reason": meta.get("last_reason", ""),
            })

        return rows
