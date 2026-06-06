import requests
import threading
import time
import os
import sys
from html import escape
from core.config import Config
from core.logger import logger
from utils.performance_tracker import PerformanceTracker


class TelegramNotifier:
    """
    Telegram GUI/UX Enhanced Notifier

    ✅ التحسينات هنا تخص تجربة المستخدم في تيليجرام فقط:
    - أزرار Inline Keyboard احترافية.
    - قائمة رئيسية ثابتة.
    - أزرار داخل رسائل الحالة والتحليل.
    - دعم callback_query للأزرار.
    - رسائل ترحيب ومساعدة محسّنة.
    - تنسيق أفضل للتقارير.
    - لا يوجد تغيير في منطق التداول أو إدارة الصفقات.
    """

    def __init__(self):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"

        # حالات انتظار إدخال المستخدم، مفصولة حسب chat_id لتجنب تداخل الأوامر.
        self.user_state = {}

        self.set_bot_commands()

    # ==========================================================
    # Telegram API Helpers
    # ==========================================================
    def _is_configured(self):
        return bool(
            self.token
            and self.token != "your_telegram_bot_token_here"
            and self.chat_id
        )

    def _post(self, method: str, payload: dict, timeout: int = 10):
        if not self._is_configured():
            logger.debug("إعدادات تيليجرام غير مكتملة، تم تخطي الطلب.")
            return None

        url = f"{self.base_url}/{method}"
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"فشل طلب تيليجرام {method}: {e}")
            return None

    def _get(self, method: str, params: dict = None, timeout: int = 10):
        if not self._is_configured():
            logger.debug("إعدادات تيليجرام غير مكتملة، تم تخطي الطلب.")
            return None

        url = f"{self.base_url}/{method}"
        try:
            response = requests.get(url, params=params or {}, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"فشل طلب تيليجرام {method}: {e}")
            return None

    def _safe_html(self, value):
        return escape(str(value), quote=False)

    # ==========================================================
    # GUI Keyboards
    # ==========================================================
    def main_menu_keyboard(self):
        """
        القائمة الرئيسية التفاعلية.
        """
        return {
            "inline_keyboard": [
                [
                    {"text": "📊 حالة البوت", "callback_data": "gui_status"},
                    {"text": "🧠 تحليل عملة", "callback_data": "gui_analyze"},
                ],
                [
                    {"text": "📋 التقارير والأداء", "callback_data": "gui_reports_menu"},
                ],
                [
                    {"text": "⏱️ فترة المسح", "callback_data": "gui_scan_interval"},
                    {"text": "⚡ مسح الآن", "callback_data": "force_scan_now"},
                ],
                [
                    {"text": "🩺 صحة النظام", "callback_data": "gui_health"},
                    {"text": "⚙️ الحد الأقصى للصفقات", "callback_data": "gui_setmax"},
                ],
                [
                    {"text": "🔄 إعادة تشغيل", "callback_data": "gui_restart_confirm"},
                    {"text": "🛑 إيقاف طوارئ", "callback_data": "gui_stop_confirm"},
                ],
                [
                    {"text": "❓ المساعدة", "callback_data": "gui_help"},
                    {"text": "🏠 القائمة الرئيسية", "callback_data": "gui_home"},
                ],
            ]
        }

    def back_home_keyboard(self):
        return {
            "inline_keyboard": [
                [
                    {"text": "🏠 القائمة الرئيسية", "callback_data": "gui_home"},
                    {"text": "🔄 تحديث الحالة", "callback_data": "gui_status"},
                ]
            ]
        }

    def analyze_keyboard(self):
        return {
            "inline_keyboard": [
                [
                    {"text": "BTC", "callback_data": "analyze_BTC"},
                    {"text": "ETH", "callback_data": "analyze_ETH"},
                    {"text": "BNB", "callback_data": "analyze_BNB"},
                ],
                [
                    {"text": "SOL", "callback_data": "analyze_SOL"},
                    {"text": "XRP", "callback_data": "analyze_XRP"},
                    {"text": "DOGE", "callback_data": "analyze_DOGE"},
                ],
                [
                    {"text": "✍️ إدخال يدوي", "callback_data": "gui_analyze_manual"},
                    {"text": "🏠 الرئيسية", "callback_data": "gui_home"},
                ],
            ]
        }

    def reports_menu_keyboard(self):
        """
        قائمة التقارير الفرعية.
        """
        return {
            "inline_keyboard": [
                [
                    {"text": "📆 تقرير يومي", "callback_data": "report_daily"},
                    {"text": "🗓️ تقرير أسبوعي", "callback_data": "report_weekly"},
                ],
                [
                    {"text": "📅 تقرير شهري", "callback_data": "report_monthly"},
                    {"text": "🏆 تقرير سنوي", "callback_data": "report_yearly"},
                ],
                [
                    {"text": "🏠 الرجوع للرئيسية", "callback_data": "gui_home"},
                ],
            ]
        }

    def setmax_keyboard(self):
        return {
            "inline_keyboard": [
                [
                    {"text": "1", "callback_data": "setmax_1"},
                    {"text": "2", "callback_data": "setmax_2"},
                    {"text": "3", "callback_data": "setmax_3"},
                    {"text": "5", "callback_data": "setmax_5"},
                ],
                [
                    {"text": "10", "callback_data": "setmax_10"},
                    {"text": "✍️ إدخال يدوي", "callback_data": "gui_setmax_manual"},
                ],
                [
                    {"text": "🏠 الرئيسية", "callback_data": "gui_home"},
                ],
            ]
        }

    def scan_interval_keyboard(self):
        """قائمة إعداد فترة المسح"""
        return {
            "inline_keyboard": [
                [
                    {"text": "5 دقائق", "callback_data": "setscan_300"},
                    {"text": "10 دقائق", "callback_data": "setscan_600"},
                    {"text": "15 دقيقة", "callback_data": "setscan_900"},
                ],
                [
                    {"text": "30 دقيقة", "callback_data": "setscan_1800"},
                    {"text": "ساعة", "callback_data": "setscan_3600"},
                ],
                [
                    {"text": "✍️ إدخال مخصص", "callback_data": "gui_setscan_manual"},
                    {"text": "🏠 الرجوع", "callback_data": "gui_home"},
                ],
            ]
        }

    def confirm_restart_keyboard(self):
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ نعم، أعد التشغيل", "callback_data": "confirm_restart"},
                    {"text": "❌ إلغاء", "callback_data": "gui_home"},
                ]
            ]
        }

    def confirm_stop_keyboard(self):
        return {
            "inline_keyboard": [
                [
                    {"text": "🛑 نعم، أوقف الآن", "callback_data": "confirm_stop"},
                    {"text": "❌ إلغاء", "callback_data": "gui_home"},
                ]
            ]
        }

    # ==========================================================
    # Commands Registration
    # ==========================================================
    def set_bot_commands(self):
        """
        تسجيل الأوامر لدى خوادم تيليجرام لتظهر تلقائياً للمستخدم في القائمة.
        """
        if not self._is_configured():
            return

        commands = [
            {"command": "start", "description": "فتح لوحة التحكم الرئيسية"},
            {"command": "menu", "description": "عرض القائمة الرئيسية"},
            {"command": "analyze", "description": "تحليل ذكي لعملة معينة"},
            {"command": "status", "description": "عرض حالة الرصيد والصفقات المفتوحة"},
            {"command": "health", "description": "عرض صحة الخادم والشبكة"},
            {"command": "setmax", "description": "تغيير الحد الأقصى للصفقات المتزامنة"},
            {"command": "restart", "description": "إعادة تشغيل البوت بالكامل"},
            {"command": "stop", "description": "إيقاف طوارئ"},
            {"command": "help", "description": "شرح استخدام البوت"},
        ]

        payload = {
            "commands": commands,
            "scope": {"type": "default"},
            "language_code": "ar",
        }

        try:
            requests.post(f"{self.base_url}/setMyCommands", json=payload, timeout=5)
            logger.info("تم تسجيل أوامر القائمة في تيليجرام بنجاح.")
        except Exception as e:
            logger.debug(f"فشل تسجيل أوامر تيليجرام: {e}")

    # ==========================================================
    # Sending / Editing Messages
    # ==========================================================
    def send_message(self, text: str, reply_markup: dict = None, disable_web_page_preview: bool = True):
        """
        إرسال رسالة نصية إلى تيليجرام مع دعم:
        - تقسيم الرسائل الطويلة.
        - أزرار تفاعلية.
        - HTML parse mode.
        """
        if not self._is_configured():
            logger.debug("إعدادات تيليجرام غير مكتملة، تم تخطي إرسال الإشعار.")
            return False

        MAX_LEN = 4096
        chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)]
        success = True

        for index, chunk in enumerate(chunks):
            payload = {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": disable_web_page_preview,
            }

            # نضيف الأزرار فقط لآخر جزء حتى لا تتكرر مع الرسائل الطويلة.
            if reply_markup and index == len(chunks) - 1:
                payload["reply_markup"] = reply_markup

            try:
                response = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=10)
                response.raise_for_status()
            except Exception as e:
                logger.error(f"فشل إرسال إشعار تيليجرام: {e}")
                success = False

        if success:
            logger.info("تم إرسال إشعار تيليجرام بنجاح.")
        return success

    def edit_message(self, chat_id, message_id, text: str, reply_markup: dict = None):
        """
        تعديل رسالة موجودة عند الضغط على الأزرار.
        """
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        if reply_markup:
            payload["reply_markup"] = reply_markup

        return self._post("editMessageText", payload, timeout=10)

    def answer_callback(self, callback_query_id, text: str = "تم ✅", show_alert: bool = False):
        payload = {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert,
        }
        return self._post("answerCallbackQuery", payload, timeout=5)

    # ==========================================================
    # GUI Pages
    # ==========================================================
    def welcome_text(self):
        return """
🤖 <b>لوحة التحكم الذكية لبوت التداول</b>

مرحباً بك في واجهة التحكم الجديدة.
يمكنك الآن إدارة البوت من الأزرار مباشرة بدون كتابة الأوامر يدوياً.

<b>اختر من القائمة:</b>
📊 متابعة الرصيد والصفقات.
🧠 تحليل عملة بالذكاء الاصطناعي.
🩺 فحص صحة النظام.
⚙️ تعديل الحد الأقصى للصفقات.
🔄 إعادة تشغيل آمنة.
🛑 إيقاف طوارئ.
"""

    def help_text(self):
        return """
❓ <b>دليل استخدام البوت</b>

<b>الأوامر المتاحة:</b>

🏠 <code>/start</code> أو <code>/menu</code>
فتح لوحة التحكم الرئيسية.

📊 <code>/status</code>
عرض الرصيد، الربح/الخسارة، والصفقات المفتوحة.

🧠 <code>/analyze BTC</code>
تحليل عملة محددة. مثال:
<code>/analyze BTC</code>
<code>/analyze ETH</code>

⚙️ <code>/setmax 5</code>
تغيير الحد الأقصى للصفقات المفتوحة.

🩺 <code>/health</code>
عرض تقرير صحة النظام.

🔄 <code>/restart</code>
إعادة تشغيل البوت بعد حفظ الحالة.

🛑 <code>/stop</code>
إيقاف طوارئ بعد حفظ الحالة.

<b>ملاحظة:</b>
يمكنك تنفيذ أغلب الأوامر عبر الأزرار بدون كتابة.
"""

    def send_home(self):
        return self.send_message(self.welcome_text(), reply_markup=self.main_menu_keyboard())

    def send_help(self):
        return self.send_message(self.help_text(), reply_markup=self.main_menu_keyboard())

    # ==========================================================
    # Alerts
    # ==========================================================
    def send_trade_alert(self, symbol, side, amount, price, sl, tp, sentiment_label):
        """
        تنسيق وإرسال رسالة تنبيه بصفقة جديدة.
        """
        emoji = "🟢" if side.lower() == "buy" else "🔴"
        action = "شراء (LONG)" if side.lower() == "buy" else "بيع (SHORT)"

        text = f"""
{emoji} <b>تنفيذ صفقة جديدة</b> {emoji}

📌 <b>الزوج:</b> <code>{self._safe_html(symbol)}</code>
📈 <b>العملية:</b> {action}
⚖️ <b>الكمية:</b> <code>{self._safe_html(amount)}</code>

💰 <b>دخول:</b> <code>{float(price):.6f}</code>
🛑 <b>وقف:</b> <code>{float(sl):.6f}</code>
🎯 <b>هدف:</b> <code>{float(tp):.6f}</code>

🧠 <b>المشاعر:</b> {self._safe_html(str(sentiment_label).upper())}
"""

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "📊 حالة البوت", "callback_data": "gui_status"},
                    {"text": "🧠 تحليل نفس العملة", "callback_data": f"analyze_{symbol.split('/')[0].replace(':', '')}"},
                ],
                [
                    {"text": "🏠 القائمة الرئيسية", "callback_data": "gui_home"},
                ],
            ]
        }

        return self.send_message(text, reply_markup=keyboard)

    def send_pnl_alert(self, symbol, order_type, price, pnl, pnl_pct=0.0):
        """
        إرسال إشعار عند إغلاق صفقة.
        """
        is_profit = float(pnl) > 0
        emoji = "🎉" if is_profit else "⚠️"

        if "TAKE_PROFIT" in order_type:
            result_text = "🎯 الحمد لله ضربت الهدف"
        elif "STOP" in order_type and is_profit:
            result_text = "🛡️ إغلاق بستوب متحرك رابح (Trailing Stop)"
        else:
            result_text = "🛑 الحمد لله ضربت ستوب، معوضين إن شاء الله بعملة أخرى"

        pnl_color = "🟢 ربح" if is_profit else "🔴 خسارة"

        text = f"""
{emoji} <b>إغلاق صفقة</b> {emoji}

📌 <b>الزوج:</b> <code>{self._safe_html(symbol)}</code>
🚦 <b>النتيجة:</b> {result_text}

💰 <b>سعر الإغلاق:</b> <code>{float(price):.6f}</code>
💵 <b>الصافي:</b> <code>{float(pnl):+.2f} USDT</code> {pnl_color}
📊 <b>النسبة المئوية:</b> <code>{float(pnl_pct):+.2f}%</code>
"""

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "📊 تحديث الحالة", "callback_data": "gui_status"},
                    {"text": "🧠 تحليل نفس العملة", "callback_data": f"analyze_{symbol.split('/')[0].replace(':', '')}"},
                ],
                [
                    {"text": "🏠 القائمة الرئيسية", "callback_data": "gui_home"},
                ],
            ]
        }

        return self.send_message(text, reply_markup=keyboard)

    # ==========================================================
    # Status Report
    # ==========================================================
    def build_status_message(self, client, state_manager):
        """
        بناء رسالة حالة البوت.
        تم فصلها حتى تعمل مع الأمر النصي ومع الأزرار.
        """
        # 1. جلب الرصيد والـ Equity.
        bal = client.get_balance()
        info = bal.get("info", {}) if bal else {}

        usdt_available = bal.get("USDT", {}).get("free", 0) if bal else 0

        wallet_balance = float(info.get("totalWalletBalance", usdt_available))
        total_unrealized = float(info.get("totalUnrealizedProfit", 0))
        total_margin = float(info.get("totalMarginBalance", usdt_available))

        # ميزة الرصيد الوهمي المقيد.
        simulated_cap = getattr(Config, "TESTNET_SIMULATED_BALANCE", 0)
        if getattr(Config, "USE_TESTNET", False) and simulated_cap > 0:
            tracker = PerformanceTracker(state_manager)
            sim_wallet = tracker.get_wallet(unrealized_pnl=total_unrealized)

            wallet_balance = sim_wallet["equity"]
            usdt_available = sim_wallet["available"]
            total_margin = sim_wallet["reserved_margin"]
            pnl_day = wallet_balance - state_manager.get_initial_balance() if state_manager.get_initial_balance() else 0
        else:
            initial = state_manager.get_initial_balance()
            pnl_day = wallet_balance - initial if initial else 0

        # 2. جلب الصفقات مباشرة من باينانس.
        try:
            all_positions = client.exchange.fetch_positions()
            live_positions = [
                p for p in all_positions
                if float(p.get("info", {}).get("positionAmt", 0)) != 0
            ]
        except Exception as e:
            logger.error(f"فشل جلب الصفقات المباشرة في /status: {e}")
            live_positions = []

        # تحديث ذاكرة البوت المحلية لتتطابق مع المنصة.
        live_symbols = {p["symbol"].split(":")[0] for p in live_positions}
        state_manager.save_active_symbols(live_symbols)

        msg = "📊 <b>حالة البوت الحالية</b>\n"
        msg += "━━━━━━━━━━━━━━\n\n"
        msg += f"💼 <b>الرصيد الإجمالي Equity:</b> <code>{wallet_balance:.2f}</code> USDT\n"
        msg += f"💵 <b>الرصيد المتاح:</b> <code>{float(usdt_available):.2f}</code> USDT\n"
        msg += f"📈 <b>ربح/خسارة اليوم:</b> <code>{pnl_day:+.2f}</code> USDT\n"
        msg += f"📌 <b>الهامش/المحفظة:</b> <code>{float(total_margin):.2f}</code> USDT\n"
        msg += f"🟢 <b>الصفقات المفتوحة:</b> <code>{len(live_positions)}</code>\n"

        if getattr(Config, "USE_TESTNET", False) and simulated_cap > 0:
            msg += f"🧪 <b>وضع المحفظة:</b> <code>Simulated Wallet</code>\n"
            msg += f"🏁 <b>رأس البداية الوهمي:</b> <code>{simulated_cap:.2f}</code> USDT\n"

        max_trades = getattr(Config, "TESTNET_MAX_OPEN_TRADES", None)
        if max_trades is not None:
            msg += f"⚙️ <b>الحد الأقصى للصفقات:</b> <code>{max_trades}</code>\n"

        if live_positions:
            msg += "\n<b>تفاصيل الصفقات المفتوحة:</b>\n"
            msg += "━━━━━━━━━━━━━━\n"

            try:
                for pos in live_positions:
                    amt = float(pos.get("info", {}).get("positionAmt", 0))
                    sym = pos.get("symbol", "").split(":")[0]
                    unrealized_pnl = float(pos.get("info", {}).get("unRealizedProfit", 0))
                    entry_price = float(pos.get("entryPrice", 0))
                    leverage = float(pos.get("info", {}).get("leverage", 1))

                    initial_margin = (abs(amt) * entry_price) / leverage if leverage else 0
                    roe = (unrealized_pnl / initial_margin * 100) if initial_margin > 0 else 0.0

                    pos_type = "شراء LONG 🟢" if amt > 0 else "بيع SHORT 🔴"
                    pnl_emoji = "🟢" if unrealized_pnl >= 0 else "🔴"
                    sign = "+" if unrealized_pnl >= 0 else ""

                    mark_price = float(pos.get("info", {}).get("markPrice", 0))
                    if mark_price == 0:
                        mark_price = client.get_current_price(sym) or entry_price

                    coin_name = sym.split("/")[0]

                    msg += f"\n🔹 <b>{self._safe_html(sym)}</b> | {pos_type}\n"
                    msg += f"   ⚖️ الكمية: <code>{abs(amt)} {self._safe_html(coin_name)}</code>\n"
                    msg += f"   💰 الدخول: <code>{entry_price:.5f}</code>\n"
                    msg += f"   📍 الحالي: <code>{mark_price:.5f}</code>\n"
                    msg += f"   {pnl_emoji} الربح: <code>{sign}{unrealized_pnl:.2f} USDT</code> | ROE <code>{sign}{roe:.2f}%</code>\n"

            except Exception as e:
                logger.error(f"خطأ في جلب PNL للتيليجرام: {e}")
                msg += "\n⚠️ تعذر جلب بعض تفاصيل الصفقات.\n"
        else:
            msg += "\n✅ لا توجد صفقات مفتوحة حالياً.\n"

        msg += "\n━━━━━━━━━━━━━━\n"
        msg += "استخدم الأزرار بالأسفل للتحديث أو الرجوع."

        return msg

    def send_status(self, client, state_manager):
        try:
            msg = self.build_status_message(client, state_manager)
            return self.send_message(msg, reply_markup=self.back_home_keyboard())
        except Exception as e:
            logger.error(f"خطأ في بناء حالة البوت: {e}")
            return self.send_message("⚠️ تعذر عرض حالة البوت حالياً.", reply_markup=self.main_menu_keyboard())

    # ==========================================================
    # Polling / Commands / Callback GUI
    # ==========================================================
    def start_polling(self, client, state_manager, ai_engine=None, ta_engine=None, news_engine=None, social_engine=None):
        """
        تشغيل خيط بالخلفية للاستماع إلى أوامر التيليجرام.
        تم تحسينه لدعم الأزرار GUI عبر callback_query.
        """
        if not self._is_configured():
            return

        def poll():
            offset = None
            session = requests.Session()

            # تفريغ الرسائل القديمة لتجنب تنفيذ أوامر قديمة.
            try:
                init_resp = session.get(
                    f"{self.base_url}/getUpdates",
                    params={"offset": -1, "timeout": 5},
                    timeout=10,
                )
                if init_resp.status_code == 200:
                    init_data = init_resp.json()
                    if init_data.get("ok") and init_data.get("result"):
                        last_update_id = init_data["result"][0]["update_id"]
                        offset = last_update_id + 1
                        session.get(
                            f"{self.base_url}/getUpdates",
                            params={"offset": offset, "timeout": 5},
                            timeout=10,
                        )
            except Exception:
                pass

            logger.info("📡 بدء الاستماع لأوامر تيليجرام مع واجهة GUI محسّنة.")

            while True:
                try:
                    params = {"timeout": 30}
                    if offset:
                        params["offset"] = offset

                    response = session.get(f"{self.base_url}/getUpdates", params=params, timeout=35)
                    data = response.json()

                    if data.get("ok"):
                        for update in data.get("result", []):
                            offset = update["update_id"] + 1

                            if "callback_query" in update:
                                self.handle_callback_query(
                                    update["callback_query"],
                                    client,
                                    state_manager,
                                    ai_engine,
                                    ta_engine,
                                    news_engine,
                                    social_engine,
                                    session,
                                    offset,
                                )
                                continue

                            message = update.get("message", {})
                            text = message.get("text", "") or ""
                            chat_id = message.get("chat", {}).get("id")

                            if str(chat_id) != str(self.chat_id):
                                continue

                            self.handle_text_message(
                                text,
                                chat_id,
                                client,
                                state_manager,
                                ai_engine,
                                ta_engine,
                                news_engine,
                                social_engine,
                                session,
                                offset,
                            )

                except requests.exceptions.ReadTimeout:
                    time.sleep(1)
                except requests.exceptions.ConnectionError:
                    time.sleep(5)
                except Exception as e:
                    logger.error(f"خطأ في الاستماع لأوامر التيليجرام: {e}")
                    time.sleep(5)

        t = threading.Thread(target=poll, daemon=True)
        t.start()

    def handle_text_message(
        self,
        text,
        chat_id,
        client,
        state_manager,
        ai_engine,
        ta_engine,
        news_engine,
        social_engine,
        session=None,
        offset=None,
    ):
        """
        معالجة الرسائل النصية العادية.
        """
        user_key = str(chat_id)
        text = (text or "").strip()

        # معالجة حالات الانتظار أولاً.
        pending = self.user_state.get(user_key)

        if pending == "awaiting_setmax":
            if text.isdigit():
                new_max = int(text)
                Config.TESTNET_MAX_OPEN_TRADES = new_max
                self.user_state.pop(user_key, None)
                self.send_message(
                    f"✅ تم تغيير الحد الأقصى للصفقات المفتوحة إلى: <b>{new_max}</b> صفقات.",
                    reply_markup=self.main_menu_keyboard(),
                )
                logger.info(f"تم تغيير الحد الأقصى للصفقات المفتوحة إلى {new_max} عبر تيليجرام.")
            else:
                self.user_state.pop(user_key, None)
                self.send_message(
                    "⚠️ إدخال غير صحيح. تم إلغاء العملية.",
                    reply_markup=self.main_menu_keyboard(),
                )
            return

        if pending == "awaiting_setscan":
            self.user_state.pop(user_key, None)
            if text.isdigit():
                minutes = int(text)
                minutes = max(5, min(minutes, 60))
                new_sec = minutes * 60

                state_manager.set("scan_interval_seconds", new_sec)
                self.send_message(
                    f"✅ تم تغيير فترة المسح إلى: <b>{minutes}</b> دقيقة.",
                    reply_markup=self.main_menu_keyboard(),
                )
                logger.info(f"تم تغيير فترة المسح إلى {new_sec} ثانية عبر تيليجرام.")
            else:
                self.send_message(
                    "⚠️ إدخال غير صحيح. الرجاء إدخال رقم بالدقائق.",
                    reply_markup=self.main_menu_keyboard(),
                )
            return

        if pending == "awaiting_analyze":
            sym = self.normalize_symbol(text)
            self.user_state.pop(user_key, None)
            threading.Thread(
                target=self.handle_analyze_request,
                args=(sym, client, ai_engine, ta_engine, news_engine, social_engine),
                daemon=True,
            ).start()
            return

        # الأوامر.
        if text in ("/start", "/menu"):
            self.send_home()

        elif text == "/help":
            self.send_help()

        elif text == "/status":
            self.send_status(client, state_manager)

        elif text == "/health":
            self.run_health_report(client, state_manager)

        elif text == "/stop":
            self.stop_bot(state_manager, session=session, offset=offset)

        elif text.startswith("/setmax"):
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                new_max = int(parts[1])
                Config.TESTNET_MAX_OPEN_TRADES = new_max
                self.send_message(
                    f"✅ تم تغيير الحد الأقصى للصفقات المفتوحة المتزامنة إلى: <b>{new_max}</b> صفقات.",
                    reply_markup=self.main_menu_keyboard(),
                )
                logger.info(f"تم تغيير الحد الأقصى للصفقات المفتوحة إلى {new_max} عبر تيليجرام.")
            else:
                self.user_state[user_key] = "awaiting_setmax"
                self.send_message(
                    "🔢 <b>أرسل الرقم الجديد فقط:</b>\nمثال: <code>5</code>",
                    reply_markup=self.setmax_keyboard(),
                )

        elif text == "/restart":
            self.restart_bot(state_manager, session=session, offset=offset)

        elif text.startswith("/analyze"):
            parts = text.split()
            if len(parts) == 2:
                sym = self.normalize_symbol(parts[1])
                threading.Thread(
                    target=self.handle_analyze_request,
                    args=(sym, client, ai_engine, ta_engine, news_engine, social_engine),
                    daemon=True,
                ).start()
            else:
                self.user_state[user_key] = "awaiting_analyze"
                self.send_message(
                    "🔍 <b>اختر عملة من الأزرار أو أرسل رمز العملة:</b>\nمثال: <code>BTC</code> أو <code>ETH</code>",
                    reply_markup=self.analyze_keyboard(),
                )

        else:
            self.send_message(
                "لم أفهم الأمر. افتح القائمة الرئيسية واختر من الأزرار 👇",
                reply_markup=self.main_menu_keyboard(),
            )

    def handle_callback_query(
        self,
        callback,
        client,
        state_manager,
        ai_engine,
        ta_engine,
        news_engine,
        social_engine,
        session=None,
        offset=None,
    ):
        """
        معالجة ضغطات الأزرار GUI.
        """
        callback_id = callback.get("id")
        data = callback.get("data", "")
        message = callback.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")

        if str(chat_id) != str(self.chat_id):
            self.answer_callback(callback_id, "غير مصرح لك باستخدام هذا البوت.", show_alert=True)
            return

        self.answer_callback(callback_id)

        try:
            if data == "gui_home":
                self.edit_message(chat_id, message_id, self.welcome_text(), self.main_menu_keyboard())

            elif data == "gui_reports_menu":
                self.edit_message(
                    chat_id,
                    message_id,
                    "📋 <b>قائمة التقارير والأداء:</b>\nاختر نوع التقرير الذي تريد عرضه:",
                    self.reports_menu_keyboard()
                )

            elif data == "gui_help":
                self.edit_message(chat_id, message_id, self.help_text(), self.main_menu_keyboard())

            elif data == "gui_status":
                try:
                    msg = self.build_status_message(client, state_manager)
                    self.edit_message(chat_id, message_id, msg, self.back_home_keyboard())
                except Exception as e:
                    logger.error(f"خطأ في تحديث الحالة من الزر: {e}")
                    self.edit_message(
                        chat_id,
                        message_id,
                        "⚠️ تعذر تحديث حالة البوت حالياً.",
                        self.main_menu_keyboard(),
                    )

            elif data in ["report_daily", "report_weekly", "report_monthly", "report_yearly"]:
                period = data.replace("report_", "")
                tracker = PerformanceTracker(state_manager)
                text = tracker.format_report(period)

                self.edit_message(
                    chat_id,
                    message_id,
                    text,
                    reply_markup=self.back_home_keyboard()
                )
                self.answer_callback(callback_id, "تم إنشاء التقرير ✅")

            elif data == "gui_health":
                self.edit_message(
                    chat_id,
                    message_id,
                    "🩺 <b>جاري تجهيز تقرير صحة النظام...</b>",
                    self.main_menu_keyboard(),
                )
                self.run_health_report(client, state_manager)

            elif data == "gui_analyze":
                self.edit_message(
                    chat_id,
                    message_id,
                    "🧠 <b>اختر العملة التي تريد تحليلها:</b>\nأو اضغط إدخال يدوي.",
                    self.analyze_keyboard(),
                )

            elif data == "gui_analyze_manual":
                self.user_state[str(chat_id)] = "awaiting_analyze"
                self.edit_message(
                    chat_id,
                    message_id,
                    "✍️ <b>أرسل رمز العملة الآن:</b>\nمثال: <code>BTC</code> أو <code>ETH</code>",
                    self.main_menu_keyboard(),
                )

            elif data.startswith("analyze_"):
                raw_symbol = data.replace("analyze_", "", 1)
                sym = self.normalize_symbol(raw_symbol)
                self.edit_message(
                    chat_id,
                    message_id,
                    f"⏳ جاري تحليل <b>{self._safe_html(sym)}</b>...\nسيصل التقرير هنا برسالة مستقلة.",
                    self.main_menu_keyboard(),
                )
                threading.Thread(
                    target=self.handle_analyze_request,
                    args=(sym, client, ai_engine, ta_engine, news_engine, social_engine),
                    daemon=True,
                ).start()

            elif data == "gui_setmax":
                current_max = getattr(Config, "TESTNET_MAX_OPEN_TRADES", "غير محدد")
                self.edit_message(
                    chat_id,
                    message_id,
                    f"⚙️ <b>الحد الأقصى الحالي للصفقات:</b> <code>{current_max}</code>\n\nاختر رقم من الأزرار أو إدخال يدوي:",
                    self.setmax_keyboard(),
                )

            elif data == "gui_setmax_manual":
                self.user_state[str(chat_id)] = "awaiting_setmax"
                self.edit_message(
                    chat_id,
                    message_id,
                    "🔢 <b>أرسل الرقم الجديد فقط:</b>\nمثال: <code>5</code>",
                    self.main_menu_keyboard(),
                )

            elif data.startswith("setmax_"):
                new_max = int(data.replace("setmax_", "", 1))
                Config.TESTNET_MAX_OPEN_TRADES = new_max
                self.edit_message(
                    chat_id,
                    message_id,
                    f"✅ تم تغيير الحد الأقصى للصفقات المفتوحة إلى: <b>{new_max}</b> صفقات.",
                    self.main_menu_keyboard(),
                )
                logger.info(f"تم تغيير الحد الأقصى للصفقات المفتوحة إلى {new_max} عبر GUI تيليجرام.")

            elif data == "gui_scan_interval":
                current_sec = int(state_manager.get("scan_interval_seconds", 600))
                self.edit_message(
                    chat_id,
                    message_id,
                    f"⏱️ <b>فترة المسح الحالية:</b> {current_sec // 60} دقيقة\n\nاختر فترة المسح الجديدة:",
                    self.scan_interval_keyboard()
                )

            elif data == "gui_setscan_manual":
                self.user_state[str(chat_id)] = "awaiting_setscan"
                self.edit_message(
                    chat_id,
                    message_id,
                    "🔢 <b>أرسل فترة المسح بالدقائق فقط:</b>\nمثال: <code>20</code>",
                    self.main_menu_keyboard()
                )

            elif data.startswith("setscan_"):
                new_interval_sec = int(data.replace("setscan_", "", 1))
                state_manager.set("scan_interval_seconds", new_interval_sec)
                self.edit_message(
                    chat_id,
                    message_id,
                    f"✅ تم تعيين فترة المسح بنجاح إلى {new_interval_sec // 60} دقيقة.",
                    self.main_menu_keyboard()
                )
                logger.info(f"تم تغيير فترة المسح إلى {new_interval_sec} ثانية من تيليجرام.")

            elif data == "force_scan_now":
                state_manager.set("force_scan_now", True)
                self.edit_message(
                    chat_id,
                    message_id,
                    "⚡ تم إرسال أمر المسح الفوري بنجاح! سيبدأ البوت بالبحث عن فرص فوراً.",
                    self.main_menu_keyboard()
                )
                logger.info("تم تفعيل المسح الفوري من تيليجرام.")

            elif data == "gui_restart_confirm":
                self.edit_message(
                    chat_id,
                    message_id,
                    "🔄 <b>تأكيد إعادة التشغيل</b>\n\nهل تريد حفظ الحالة وإعادة تشغيل البوت الآن؟",
                    self.confirm_restart_keyboard(),
                )

            elif data == "confirm_restart":
                self.edit_message(
                    chat_id,
                    message_id,
                    "🔄 <b>تم التأكيد.</b>\nجاري حفظ الحالة وإعادة التشغيل...",
                    None,
                )
                self.restart_bot(state_manager, session=session, offset=offset)

            elif data == "gui_stop_confirm":
                self.edit_message(
                    chat_id,
                    message_id,
                    "🛑 <b>تأكيد إيقاف الطوارئ</b>\n\nهذا الأمر سيوقف البوت فوراً بعد حفظ الحالة.",
                    self.confirm_stop_keyboard(),
                )

            elif data == "confirm_stop":
                self.edit_message(
                    chat_id,
                    message_id,
                    "🛑 <b>تم التأكيد.</b>\nجاري حفظ الحالة وإيقاف البوت فوراً...",
                    None,
                )
                self.stop_bot(state_manager, session=session, offset=offset)

            else:
                self.edit_message(
                    chat_id,
                    message_id,
                    "⚠️ زر غير معروف. عد إلى القائمة الرئيسية.",
                    self.main_menu_keyboard(),
                )

        except Exception as e:
            logger.error(f"خطأ في معالجة زر تيليجرام: {e}")
            self.send_message("⚠️ حدث خطأ أثناء تنفيذ الأمر من الواجهة.", reply_markup=self.main_menu_keyboard())

    # ==========================================================
    # Actions
    # ==========================================================
    def normalize_symbol(self, symbol: str):
        """
        تحويل إدخال المستخدم إلى صيغة تداول موحدة.
        BTC      -> BTC/USDT
        BTCUSDT  -> BTC/USDT
        BTC/USDT -> BTC/USDT
        """
        sym = (symbol or "").strip().upper()
        sym = sym.replace(" ", "")

        if not sym:
            return "BTC/USDT"

        if "/" in sym:
            return sym

        if sym.endswith("USDT"):
            return sym.replace("USDT", "/USDT")

        return f"{sym}/USDT"

    def run_health_report(self, client, state_manager):
        try:
            from utils.health_monitor import HealthMonitor
            health = HealthMonitor(client, state_manager, self)
            health.send_health_report()
        except Exception as e:
            logger.error(f"فشل إرسال تقرير الصحة: {e}")
            self.send_message("⚠️ تعذر توليد تقرير صحة النظام حالياً.", reply_markup=self.main_menu_keyboard())

    def stop_bot(self, state_manager, session=None, offset=None):
        logger.critical("تم إيقاف البوت يدوياً عبر تيليجرام.")
        saved = state_manager.save_state()

        if saved:
            self.send_message("🛑 <b>أمر طوارئ:</b> تم حفظ البيانات. جاري إيقاف البوت فوراً!")
        else:
            self.send_message("⚠️ <b>تحذير:</b> فشل حفظ البيانات! جاري الإيقاف مع احتمال فقدان بيانات.")

        try:
            if session and offset:
                session.get(f"{self.base_url}/getUpdates", params={"offset": offset}, timeout=5)
        except Exception:
            pass

        os._exit(0)

    def restart_bot(self, state_manager, session=None, offset=None):
        logger.critical("تم طلب إعادة تشغيل البوت عبر تيليجرام.")
        state_manager.save_state()
        self.send_message("🔄 <b>أمر إعادة تشغيل:</b> تم حفظ البيانات. جاري إعادة تشغيل البوت الآن...")

        try:
            if session and offset:
                session.get(f"{self.base_url}/getUpdates", params={"offset": offset}, timeout=5)
        except Exception:
            pass

        os.execv(sys.executable, ["python"] + sys.argv)

    # ==========================================================
    # AI Analysis
    # ==========================================================
    def handle_analyze_request(self, symbol, client, ai_engine, ta_engine, news_engine, social_engine):
        """
        معالجة طلب التحليل الذكي وتوليد التقرير.
        """
        if not ai_engine or not ta_engine:
            self.send_message("⚠️ عذراً، محركات التحليل غير متوفرة حالياً.", reply_markup=self.main_menu_keyboard())
            return

        self.send_message(
            f"⏳ جاري تجميع البيانات والتحليل الذكي لعملة <b>{self._safe_html(symbol)}</b>...\nيرجى الانتظار قليلاً 🤖",
            reply_markup=self.main_menu_keyboard(),
        )

        try:
            # 1. جلب السعر.
            current_price = client.get_current_price(symbol)
            if not current_price:
                self.send_message(
                    f"⚠️ لم أتمكن من العثور على العملة <b>{self._safe_html(symbol)}</b>. تأكد من صحة الرمز.",
                    reply_markup=self.analyze_keyboard(),
                )
                return

            # 2. التحليل الفني.
            ta_data = ta_engine.analyze(symbol, client)
            if not ta_data:
                ta_data = {}

            # 3. الأخبار والمشاعر.
            coin_name = symbol.split("/")[0]

            news_text = ""
            if news_engine:
                articles = news_engine.fetch_news_for_coin(coin_name)
                news_text = " | ".join([a.get("title", "") for a in articles[:3]]) if articles else "لا توجد أخبار هامة."

            social_text = ""
            if social_engine:
                sentiment = social_engine.get_social_sentiment(coin_name)
                if sentiment:
                    social_text = (
                        f"حالة السوشيال: {sentiment.get('label', 'محايد')} "
                        f"(Galaxy Score: {sentiment.get('galaxy_score', '?')})"
                    )
                else:
                    social_text = "تفاعل ضعيف في السوشيال ميديا."

            # 4. توليد التقرير بالذكاء الاصطناعي.
            report = ai_engine.generate_interactive_analysis(
                symbol,
                current_price,
                ta_data,
                news_text,
                social_text,
            )

            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "📊 حالة البوت", "callback_data": "gui_status"},
                        {"text": "🔄 تحليل مرة أخرى", "callback_data": f"analyze_{coin_name}"},
                    ],
                    [
                        {"text": "🧠 تحليل عملة أخرى", "callback_data": "gui_analyze"},
                        {"text": "🏠 الرئيسية", "callback_data": "gui_home"},
                    ],
                ]
            }

            self.send_message(report, reply_markup=keyboard)

        except Exception as e:
            logger.error(f"خطأ أثناء توليد التقرير التفاعلي: {e}")
            self.send_message(
                f"⚠️ حدث خطأ أثناء تحليل <b>{self._safe_html(symbol)}</b>. الرجاء المحاولة لاحقاً.",
                reply_markup=self.main_menu_keyboard(),
            )
