from core.logger import logger

class TradeApproachAlertManager:
    """
    نظام تنبيهات الاقتراب من الهدف/الستوب بذكاء
    لا يزعج المستخدم بالتكرار، ويفهم السياق (R-Multiple)
    """
    def __init__(self, state_manager, notifier):
        self.state_manager = state_manager
        self.notifier = notifier

        # متى ننبه قبل الهدف؟ (0.2R)
        self.tp_alert_buffer_r = 0.2

        # متى ننبه قبل الستوب؟ (0.2R أو 0.3ATR)
        self.sl_alert_buffer_r = 0.2
        self.sl_alert_atr_buffer = 0.3

    def check_alerts(self, symbol: str, trade: dict, current_price: float, atr: float):
        trade_id = trade.get("trade_id", f"{symbol}_active")
        side = trade.get("side", "buy").lower()

        entry = trade.get("entry_price", 0)
        initial_sl = trade.get("initial_stop_loss", 0)
        
        # الستوب قد يكون متحركاً (Trailing)
        current_sl = trade.get("stop_loss", initial_sl)
        tp = trade.get("take_profit", 0)
        
        if entry == 0 or initial_sl == 0 or tp == 0:
            return

        risk = self._get_initial_risk(side, entry, initial_sl)
        if risk <= 0:
            return

        current_r = self._get_current_r(side, entry, current_price, risk)
        target_r = self._get_target_r(side, entry, tp, risk)

        alert_state = self._get_alert_state(trade_id)

        # 1) تنبيه الاقتراب من الهدف
        if not alert_state.get("tp_approach_sent", False):
            if self._is_near_tp(current_r, target_r):
                self._send_tp_alert(symbol, side, current_price, current_r, target_r, tp)
                alert_state["tp_approach_sent"] = True

        # 2) تنبيه الاقتراب من الستوب
        if not alert_state.get("sl_approach_sent", False):
            if self._is_near_sl(side, current_price, current_sl, risk, atr):
                self._send_sl_alert(symbol, side, current_price, current_r, current_sl)
                alert_state["sl_approach_sent"] = True

        self._save_alert_state(trade_id, alert_state)

    def reset_alerts(self, symbol: str):
        trade_id = f"{symbol}_active"
        key = f"trade_alerts:{trade_id}"
        self.state_manager.set(key, {})

    def _get_initial_risk(self, side, entry, initial_sl):
        return entry - initial_sl if side == "buy" else initial_sl - entry

    def _get_current_r(self, side, entry, current_price, risk):
        return (current_price - entry) / risk if side == "buy" else (entry - current_price) / risk

    def _get_target_r(self, side, entry, tp, risk):
        return (tp - entry) / risk if side == "buy" else (entry - tp) / risk

    def _is_near_tp(self, current_r, target_r):
        if target_r <= 0:
            return False
        return current_r >= target_r - self.tp_alert_buffer_r

    def _is_near_sl(self, side, current_price, current_sl, risk, atr):
        buffer_price = max(risk * self.sl_alert_buffer_r, atr * self.sl_alert_atr_buffer)
        distance_to_sl = current_price - current_sl if side == "buy" else current_sl - current_price
        return distance_to_sl <= buffer_price

    def _get_alert_state(self, trade_id):
        key = f"trade_alerts:{trade_id}"
        state = self.state_manager.get_state()
        return state.get(key, {})

    def _save_alert_state(self, trade_id, alert_state):
        key = f"trade_alerts:{trade_id}"
        state = self.state_manager.get_state()
        state[key] = alert_state
        self.state_manager.save_state()

    def _send_tp_alert(self, symbol, side, current_price, current_r, target_r, tp):
        msg = (
            f"🎯 <b>اقتراب من الهدف!</b>\n"
            f"الزوج: <code>{symbol}</code> ({side.upper()})\n"
            f"السعر الحالي: <code>{current_price:.5f}</code>\n"
            f"التقدم: {current_r:.2f}R / {target_r:.2f}R\n"
            f"الهدف الفني (TP): {tp:.5f}\n"
            f"<i>(جهز نفسك لجني الأرباح أو دع البوت يغلقها)</i>"
        )
        self.notifier.send_message(msg)

    def _send_sl_alert(self, symbol, side, current_price, current_r, current_sl):
        msg = (
            f"⚠️ <b>اقتراب من وقف الخسارة!</b>\n"
            f"الزوج: <code>{symbol}</code> ({side.upper()})\n"
            f"السعر الحالي: <code>{current_price:.5f}</code>\n"
            f"الحالة الحالية: {current_r:.2f}R\n"
            f"الستوب الفعال (SL): {current_sl:.5f}\n"
            f"<i>(السعر اقترب من الستوب، نراقب الارتداد أو نتقبل الإغلاق)</i>"
        )
        self.notifier.send_message(msg)
