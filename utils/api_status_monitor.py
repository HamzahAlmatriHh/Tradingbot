# utils/api_status_monitor.py

import os
import time
import requests
from datetime import datetime
from html import escape as html_escape
from core.config import Config
from core.logger import logger
from utils.api_key_pool import APIKeyPool


class APIStatusMonitor:
    """
    فاحص شامل لكل الخدمات الخارجية المستخدمة في البوت.
    لا يعرض أي مفاتيح API.
    يخزن آخر النتائج في state_manager ويرسل تنبيهات عند تعطل أي خدمة.
    """

    def __init__(self, client=None, state_manager=None, notifier=None):
        self.client = client
        self.state_manager = state_manager
        self.notifier = notifier
        self.timeout = getattr(Config, "API_HEALTH_TIMEOUT_SECONDS", 15)

    # ==========================================================
    # Helpers
    # ==========================================================
    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _mask_present(self, value):
        if isinstance(value, list):
            return any(self._mask_present(v) for v in value)
        return bool(value and str(value).strip() and "your_" not in str(value).lower())

    def _result(self, name, status, latency_ms=0, reason="", required=True, details=None):
        return {
            "name": name,
            "status": status,  # OK | WARN | DOWN | SKIP
            "latency_ms": round(float(latency_ms or 0), 2),
            "reason": str(reason or ""),
            "required": bool(required),
            "checked_at": self._now(),
            "details": details or {},
        }

    def _request_json(self, method, url, **kwargs):
        started = time.time()
        response = requests.request(method, url, timeout=self.timeout, **kwargs)
        latency_ms = (time.time() - started) * 1000
        return response, latency_ms

    # ==========================================================
    # Individual Checks
    # ==========================================================
    def check_binance_public(self):
        if not self.client:
            return self._result("Binance Public", "DOWN", reason="Exchange client is missing.")

        try:
            started = time.time()
            server_time = self.client.exchange.fetch_time()
            latency_ms = (time.time() - started) * 1000

            if server_time:
                return self._result("Binance Public", "OK", latency_ms, "fetch_time OK.")

            return self._result("Binance Public", "WARN", latency_ms, "fetch_time returned empty value.")

        except Exception as e:
            return self._result("Binance Public", "DOWN", reason=str(e))

    def check_binance_private(self):
        if not self.client:
            return self._result("Binance Private/Auth", "DOWN", reason="Exchange client is missing.")

        if not self._mask_present(Config.BINANCE_API_KEY) or not self._mask_present(Config.BINANCE_API_SECRET):
            return self._result("Binance Private/Auth", "DOWN", reason="Missing Binance API key/secret.")

        try:
            started = time.time()
            balance = self.client.exchange.fetch_balance()
            latency_ms = (time.time() - started) * 1000

            if balance is not None:
                return self._result("Binance Private/Auth", "OK", latency_ms, "fetch_balance OK.")

            return self._result("Binance Private/Auth", "WARN", latency_ms, "fetch_balance returned empty.")

        except Exception as e:
            return self._result("Binance Private/Auth", "DOWN", reason=str(e))

    def check_telegram(self):
        if not self._mask_present(Config.TELEGRAM_BOT_TOKEN) or not self._mask_present(Config.TELEGRAM_CHAT_ID):
            return self._result("Telegram", "DOWN", reason="Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")

        try:
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/getMe"
            response, latency_ms = self._request_json("GET", url)
            data = response.json() if response.text else {}

            if response.ok and data.get("ok"):
                return self._result("Telegram", "OK", latency_ms, "getMe OK.")

            return self._result("Telegram", "DOWN", latency_ms, f"HTTP {response.status_code}: {data}")

        except Exception as e:
            return self._result("Telegram", "DOWN", reason=str(e))

    def check_groq(self):
        keys = getattr(Config, "GROQ_API_KEYS", [])
        if not self._mask_present(keys):
            return self._result("Groq", "SKIP", reason="GROQ_API_KEYS missing.", required=False)
            
        pool = APIKeyPool("groq", keys, self.state_manager)
        available = pool.get_available_keys()
        last_reason = ""
        
        for key in available:
            try:
                headers = {"Authorization": f"Bearer {key}"}
                response, latency_ms = self._request_json(
                    "GET",
                    "https://api.groq.com/openai/v1/models",
                    headers=headers,
                )
    
                if response.status_code == 200:
                    pool.mark_success(key)
                    return self._result("Groq", "OK", latency_ms, "Models endpoint OK.", details={"keys": pool.stats()})
    
                pool.mark_failure(key, response.status_code, response.text[:180])
                last_reason = f"HTTP {response.status_code}: {response.text[:180]}"
            except Exception as e:
                pool.mark_failure(key, 0, str(e))
                last_reason = str(e)
                
        return self._result("Groq", "DOWN", reason=f"All keys failed. Last: {last_reason}", details={"keys": pool.stats()})

    def check_newsapi(self):
        keys = getattr(Config, "NEWS_API_KEYS", [])
        if not self._mask_present(keys):
            return self._result("NewsAPI", "SKIP", reason="NEWS_API_KEYS missing.", required=False)

        pool = APIKeyPool("newsapi", keys, self.state_manager)
        available = pool.get_available_keys()
        last_reason = ""

        for key in available:
            try:
                params = {
                    "q": "bitcoin",
                    "language": "en",
                    "pageSize": 1,
                    "apiKey": key,
                }
                response, latency_ms = self._request_json(
                    "GET",
                    "https://newsapi.org/v2/everything",
                    params=params,
                )
                data = response.json() if response.text else {}
    
                if response.ok and data.get("status") == "ok":
                    pool.mark_success(key)
                    return self._result("NewsAPI", "OK", latency_ms, "NewsAPI OK.", details={"keys": pool.stats()})
    
                code = data.get("code") or response.status_code
                pool.mark_failure(key, response.status_code, f"NewsAPI problem: {code}")
                last_reason = f"HTTP {response.status_code}: {code}"
    
            except Exception as e:
                pool.mark_failure(key, 0, str(e))
                last_reason = str(e)
                
        return self._result("NewsAPI", "DOWN", reason=f"All keys failed. Last: {last_reason}", details={"keys": pool.stats()})


    def check_cryptopanic(self):
        keys = getattr(Config, "CRYPTOPANIC_API_KEYS", [])
        if not self._mask_present(keys):
            return self._result("CryptoPanic", "SKIP", reason="CRYPTOPANIC_API_KEYS missing.", required=False)

        pool = APIKeyPool("cryptopanic", keys, self.state_manager)
        available = pool.get_available_keys()
        last_reason = ""

        for key in available:
            try:
                params = {
                    "auth_token": key,
                    "public": "true",
                    "currencies": "BTC",
                }
                response, latency_ms = self._request_json(
                    "GET",
                    "https://cryptopanic.com/api/v1/posts/",
                    params=params,
                )

                content_type = response.headers.get("content-type", "")
                raw_text = response.text or ""

                if not response.ok:
                    pool.mark_failure(
                        key,
                        response.status_code,
                        raw_text[:180] or f"HTTP {response.status_code}"
                    )
                    last_reason = f"HTTP {response.status_code}: {raw_text[:180]}"
                    continue

                if "application/json" not in content_type.lower():
                    pool.mark_failure(
                        key,
                        response.status_code,
                        f"Non-JSON response: {raw_text[:180]}"
                    )
                    last_reason = f"Non-JSON response: {raw_text[:180]}"
                    continue

                data = response.json()

                if "results" in data:
                    pool.mark_success(key)
                    return self._result("CryptoPanic", "OK", latency_ms, "CryptoPanic OK.", required=False, details={"keys": pool.stats()})

                pool.mark_failure(key, response.status_code, str(data)[:180])
                last_reason = str(data)[:180]

            except Exception as e:
                pool.mark_failure(key, 0, str(e))
                last_reason = str(e)
                
        return self._result("CryptoPanic", "DOWN", reason=f"All keys failed. Last: {last_reason}", required=False, details={"keys": pool.stats()})


    def check_lunarcrush(self):
        keys = getattr(Config, "LUNARCRUSH_API_KEYS", [])
        if not self._mask_present(keys):
            return self._result("LunarCrush", "SKIP", reason="LUNARCRUSH_API_KEYS missing.", required=False)

        pool = APIKeyPool("lunarcrush", keys, self.state_manager)
        available = pool.get_available_keys()
        last_reason = ""

        for key in available:
            try:
                headers = {"Authorization": f"Bearer {key}"}
                response, latency_ms = self._request_json(
                    "GET",
                    "https://lunarcrush.com/api4/public/coins/BTC/v1",
                    headers=headers,
                )
    
                if response.status_code == 200:
                    data = response.json() if response.text else {}
                    if data.get("data"):
                        pool.mark_success(key)
                        return self._result("LunarCrush", "OK", latency_ms, "LunarCrush BTC OK.", details={"keys": pool.stats()})
                    last_reason = "No data returned."
                    pool.mark_failure(key, response.status_code, last_reason)
                    continue
    
                pool.mark_failure(key, response.status_code, response.text[:180])
                last_reason = f"HTTP {response.status_code}: {response.text[:180]}"
    
            except Exception as e:
                pool.mark_failure(key, 0, str(e))
                last_reason = str(e)

        return self._result("LunarCrush", "DOWN", reason=f"All keys failed. Last: {last_reason}", details={"keys": pool.stats()})

    def check_coinglass(self):
        keys = getattr(Config, "COINGLASS_API_KEYS", [])
        if not keys:
            return self._result("CoinGlass", "SKIP", reason="No CoinGlass keys configured.", required=False)

        pool = APIKeyPool("coinglass", keys, self.state_manager)
        available = pool.get_available_keys()

        last_reason = ""

        for key in available:
            try:
                headers = {
                    "CG-API-KEY": key,
                    "accept": "application/json",
                }
                response, latency_ms = self._request_json(
                    "GET",
                    "https://open-api-v4.coinglass.com/api/futures/supported-coins",
                    headers=headers,
                )

                if response.status_code == 200:
                    data = response.json() if response.text else {}
                    if data:
                        pool.mark_success(key)
                        details = {"keys": pool.stats()}
                        return self._result("CoinGlass", "OK", latency_ms, "CoinGlass v4 supported-coins OK.", details=details)

                pool.mark_failure(key, response.status_code, response.text[:180])
                last_reason = f"HTTP {response.status_code}: {response.text[:180]}"

            except Exception as e:
                pool.mark_failure(key, 0, str(e))
                last_reason = str(e)

        return self._result(
            "CoinGlass",
            "DOWN",
            reason=f"All CoinGlass keys failed. Last: {last_reason}",
            details={"keys": pool.stats()}
        )

    def check_coingecko(self):
        try:
            keys = getattr(Config, "COINGECKO_API_KEYS", [])
            key = keys[0] if keys else None
            headers = {}
            if self._mask_present(key):
                headers["x-cg-demo-api-key"] = key

            response, latency_ms = self._request_json(
                "GET",
                "https://api.coingecko.com/api/v3/ping",
                headers=headers,
            )

            if response.status_code == 200:
                return self._result("CoinGecko", "OK", latency_ms, "CoinGecko ping OK.", required=False)

            if response.status_code in [401, 403, 429]:
                return self._result("CoinGecko", "WARN", latency_ms, f"HTTP {response.status_code}: {response.text[:180]}", required=False)

            return self._result("CoinGecko", "WARN", latency_ms, f"HTTP {response.status_code}: {response.text[:180]}", required=False)

        except Exception as e:
            return self._result("CoinGecko", "DOWN", reason=str(e), required=False)

    def check_fred(self):
        if not self._mask_present(Config.FRED_API_KEY):
            return self._result("FRED", "SKIP", reason="FRED_API_KEY is missing.", required=False)

        try:
            params = {
                "series_id": "DFF",
                "api_key": Config.FRED_API_KEY,
                "file_type": "json",
                "limit": 1,
                "sort_order": "desc",
            }
            response, latency_ms = self._request_json(
                "GET",
                "https://api.stlouisfed.org/fred/series/observations",
                params=params,
            )

            if response.status_code == 200:
                data = response.json() if response.text else {}
                if "observations" in data:
                    return self._result("FRED", "OK", latency_ms, "FRED observations OK.", required=False)
                return self._result("FRED", "WARN", latency_ms, str(data)[:180], required=False)

            if response.status_code in [400, 401, 403, 429]:
                return self._result("FRED", "DOWN", latency_ms, f"HTTP {response.status_code}: {response.text[:180]}", required=False)

            return self._result("FRED", "WARN", latency_ms, f"HTTP {response.status_code}: {response.text[:180]}", required=False)

        except Exception as e:
            return self._result("FRED", "DOWN", reason=str(e), required=False)

    def check_volume_and_state_files(self):
        try:
            state_file = os.getenv("STATE_FILE", "bot_state.json")
            data_dir = getattr(Config, "DATA_DIR", ".")
            trades_log = getattr(Config, "TESTNET_TRADES_LOG", "testnet_trades_log.csv")

            paths = {
                "STATE_FILE": state_file,
                "DATA_DIR": data_dir,
                "TESTNET_TRADES_LOG": trades_log,
            }

            problems = []

            for label, path in paths.items():
                abs_path = os.path.abspath(path)
                directory = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path) or "."
                if not os.path.exists(directory):
                    problems.append(f"{label} directory does not exist: {directory}")
                elif not os.access(directory, os.W_OK):
                    problems.append(f"{label} directory not writable: {directory}")

            if problems:
                return self._result("Railway Volume / Files", "DOWN", reason=" | ".join(problems))

            # اختبار كتابة خفيف
            test_path = os.path.join(os.path.abspath(data_dir), ".health_write_test")
            try:
                os.makedirs(os.path.abspath(data_dir), exist_ok=True)
                with open(test_path, "w", encoding="utf-8") as f:
                    f.write("ok")
                os.remove(test_path)
            except Exception as e:
                return self._result("Railway Volume / Files", "DOWN", reason=f"Write test failed: {e}")

            return self._result("Railway Volume / Files", "OK", reason="State/data paths writable.")

        except Exception as e:
            return self._result("Railway Volume / Files", "DOWN", reason=str(e))

    # ==========================================================
    # Full Check
    # ==========================================================
    def run_full_check(self):
        checks = [
            self.check_binance_public,
            self.check_binance_private,
            self.check_telegram,
            self.check_groq,
            self.check_newsapi,
            self.check_cryptopanic,
            self.check_lunarcrush,
            self.check_coinglass,
            self.check_coingecko,
            self.check_fred,
            self.check_volume_and_state_files,
        ]

        results = []

        for check in checks:
            try:
                results.append(check())
                time.sleep(0.3)
            except Exception as e:
                results.append(self._result(check.__name__, "DOWN", reason=str(e)))

        summary = self.summarize(results)

        if self.state_manager:
            self.state_manager.set("api_status_last_results", {
                "summary": summary,
                "results": results,
                "checked_at": self._now(),
            })

        return {
            "summary": summary,
            "results": results,
            "checked_at": self._now(),
        }

    def summarize(self, results):
        total = len(results)
        ok = len([r for r in results if r["status"] == "OK"])
        warn = len([r for r in results if r["status"] == "WARN"])
        down = len([r for r in results if r["status"] == "DOWN"])
        skip = len([r for r in results if r["status"] == "SKIP"])

        critical_down = [
            r for r in results
            if r["status"] == "DOWN" and r.get("required", True)
        ]

        if critical_down:
            overall = "DOWN"
        elif down or warn:
            overall = "WARN"
        else:
            overall = "OK"

        return {
            "overall": overall,
            "total": total,
            "ok": ok,
            "warn": warn,
            "down": down,
            "skip": skip,
            "critical_down": [r["name"] for r in critical_down],
        }

    # ==========================================================
    # Formatting & Notifications
    # ==========================================================
    def format_report(self, payload):
        summary = payload["summary"]
        results = payload["results"]

        overall_emoji = {
            "OK": "🟢",
            "WARN": "🟡",
            "DOWN": "🔴",
        }.get(summary["overall"], "⚪")

        msg = "🧪 <b>تقرير فحص خدمات البوت / API Status</b>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"{overall_emoji} <b>الحالة العامة:</b> <code>{summary['overall']}</code>\n"
        msg += f"✅ OK: <code>{summary['ok']}</code> | 🟡 WARN: <code>{summary['warn']}</code> | 🔴 DOWN: <code>{summary['down']}</code> | ⚪ SKIP: <code>{summary['skip']}</code>\n"
        msg += f"🕒 <b>وقت الفحص:</b> <code>{payload['checked_at']}</code>\n\n"

        for r in results:
            emoji = {
                "OK": "✅",
                "WARN": "🟡",
                "DOWN": "🔴",
                "SKIP": "⚪",
            }.get(r["status"], "⚪")

            required = "أساسي" if r.get("required", True) else "اختياري"

            safe_name = html_escape(str(r.get("name", "")), quote=False)
            safe_status = html_escape(str(r.get("status", "")), quote=False)
            safe_reason = html_escape(str(r.get("reason", ""))[:220], quote=False)
            safe_required = html_escape(str(required), quote=False)

            msg += (
                f"{emoji} <b>{safe_name}</b> "
                f"(<code>{safe_required}</code>)\n"
                f"• الحالة: <code>{safe_status}</code>\n"
                f"• الزمن: <code>{r['latency_ms']:.0f} ms</code>\n"
                f"• السبب: <code>{safe_reason}</code>\n"
            )
            
            if r.get("details", {}).get("keys"):
                msg += f"• حالة المفاتيح: <code>{len(r['details']['keys'])} مفاتيح مكوّنة</code>\n"
            msg += "\n"

        if summary["critical_down"]:
            msg += "🚨 <b>خدمات أساسية متوقفة:</b>\n"
            for name in summary["critical_down"]:
                msg += f"• <code>{name}</code>\n"

        return msg

    def notify_if_needed(self, payload):
        if not self.notifier or not self.state_manager:
            return

        if not getattr(Config, "API_HEALTH_CHECK_ENABLED", True):
            return

        summary = payload["summary"]
        results = payload["results"]

        problem_results = []
        for r in results:
            if r["status"] == "DOWN":
                problem_results.append(r)
            elif r["status"] == "WARN" and getattr(Config, "API_HEALTH_NOTIFY_WARNINGS", True):
                problem_results.append(r)

        if not problem_results:
            return

        now_ts = time.time()
        cooldown = getattr(Config, "API_HEALTH_ALERT_COOLDOWN_MINUTES", 60) * 60
        last_alerts = self.state_manager.get("api_status_last_alerts", {})

        to_alert = []

        for r in problem_results:
            key = r["name"]
            previous = last_alerts.get(key, {})
            prev_status = previous.get("status")
            prev_time = float(previous.get("time", 0) or 0)

            if prev_status != r["status"] or now_ts - prev_time >= cooldown:
                to_alert.append(r)
                last_alerts[key] = {
                    "status": r["status"],
                    "time": now_ts,
                    "reason": r["reason"],
                }

        if not to_alert:
            return

        self.state_manager.set("api_status_last_alerts", last_alerts)

        msg = "🚨 <b>تنبيه خدمات البوت</b>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"الحالة العامة: <code>{summary['overall']}</code>\n\n"

        for r in to_alert:
            emoji = "🔴" if r["status"] == "DOWN" else "🟡"
            msg += f"{emoji} <b>{r['name']}</b>: <code>{r['status']}</code>\n"
            msg += f"السبب: <code>{str(r['reason'])[:220]}</code>\n\n"

        msg += "حدّث المفتاح في Railway Variables ثم اضغط زر فحص الخدمات من تيليجرام."

        self.notifier.send_message(msg)
