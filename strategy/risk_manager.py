from core.logger import logger
from core.config import Config

class RiskManager:
    """
    إدارة المخاطر الذكية المستندة إلى السيولة (SMC Smart Risk Manager)
    """
    def __init__(self):
        self.risk_percent = Config.RISK_PER_TRADE_PERCENT / 100.0  # 1%
        self.min_rr = 1.5 # الحد الأدنى لنسبة العائد للمخاطرة
        self.tp_front_run_pct = 0.0015  # الخروج قبل السيولة بـ 0.15% (Front Running)

    def calculate_position_size(self, balance: float, current_price: float, custom_risk_percent: float = None, atr: float = None, sl: float = None):
        """
        حساب الكمية بناءً على نسبة المخاطرة والمسافة الحقيقية للستوب (Live SL Distance)
        """
        if not balance or balance <= 0 or not current_price or current_price <= 0:
            return 0
            
        risk_pct = custom_risk_percent if custom_risk_percent is not None else self.risk_percent
        risk_amount = balance * risk_pct
        
        # حساب المخاطرة لكل وحدة (Live Stop Loss Distance)
        if sl and sl > 0 and sl != current_price:
            loss_per_unit = abs(current_price - sl)
        else:
            use_atr = getattr(Config, 'USE_ATR_TARGETS', False) and atr is not None and atr > 0
            if use_atr:
                atr_sl_mult = getattr(Config, 'ATR_SL_MULTIPLIER', 1.5)
                loss_per_unit = atr * atr_sl_mult
            else:
                loss_per_unit = current_price * 0.02
            
        if loss_per_unit <= 0:
            loss_per_unit = current_price * 0.02
            
        amount = risk_amount / loss_per_unit
        logger.info(f"حساب المخاطرة: رصيد={balance:.2f}, مخاطرة مسموحة={risk_amount:.2f}$, كمية={amount:.6f}")
        return amount

    def calculate_trade_plan(self, side: str, entry: float, sl: float, market_levels: dict):
        """
        حساب خطة التداول باختيار أفضل مجمع سيولة يعطي RR ممتاز
        """
        risk = self._risk(side, entry, sl)
        if risk <= 0:
            logger.warning(f"خطر: الستوب لوز {sl} غير منطقي للدخول {entry} من جهة {side}")
            return {"valid": False, "reason": "Invalid risk"}

        targets = self._collect_liquidity_targets(side, entry, market_levels)
        if not targets:
            logger.warning("لا توجد مجمعات سيولة (أهداف) متاحة أعلى/أسفل السعر الحالي")
            return {"valid": False, "reason": "No SMC target found"}

        # ترتيب الأهداف من الأقرب إلى الأبعد
        targets = sorted(targets, key=lambda x: abs(x["price"] - entry))
        
        valid_targets = []
        blocking_target = None
        
        for target in targets:
            tp = self._front_run_tp(side, target["price"])
            reward = self._reward(side, entry, tp)
            
            # منع القسمة على الصفر
            rr = reward / risk if risk > 0 else 0
            
            target["tp"] = tp
            target["rr"] = rr
            
            # إذا كان أول هدف هو منطقة سيولة قوية جداً ولكنه لا يعطي RR جيد، نمنع الصفقة
            if rr < self.min_rr and target.get("quality", 1.0) >= 1.2:
                blocking_target = target
                break
                
            if rr >= self.min_rr:
                valid_targets.append(target)
                
        if blocking_target:
            logger.warning(f"تم حظر الصفقة: أقرب مقاومة قوية تعطي RR سيء ({blocking_target['rr']:.2f}). النوع: {blocking_target['type']}")
            return {
                "valid": False,
                "reason": "Strong opposing liquidity is too close. Poor RR.",
                "blocking_target": blocking_target,
                "rr": blocking_target["rr"]
            }

        if not valid_targets:
            best_available = max(targets, key=lambda x: x.get("rr", 0))
            logger.warning(f"لا يوجد هدف سيولة يحقق RR {self.min_rr}. أفضل متاح: {best_available.get('rr',0):.2f}")
            return {
                "valid": False,
                "reason": "No SMC target provides acceptable RR.",
                "best_available_rr": round(best_available.get("rr", 0), 2)
            }

        # خذ أول هدف يحقق الشرط
        selected = valid_targets[0]
        logger.info(f"🎯 تم اختيار هدف سيولة SMC! الدخول: {entry}, الوقف: {sl}, الهدف: {selected['tp']}, RR: {selected['rr']:.2f}, نوع الهدف: {selected['type']}")

        return {
            "valid": True,
            "entry": entry,
            "sl": sl,
            "tp": selected["tp"],
            "rr": round(selected["rr"], 2),
            "target_type": selected["type"],
            "target_raw_price": selected["price"]
        }

    def _risk(self, side, entry, sl):
        return entry - sl if side == "buy" else sl - entry

    def _reward(self, side, entry, tp):
        return tp - entry if side == "buy" else entry - tp

    def _front_run_tp(self, side, raw_target_price):
        """نخرج قبل الهدف المؤسسي بمسافة طفيفة لضمان التنفيذ"""
        return raw_target_price * (1 - self.tp_front_run_pct) if side == "buy" else raw_target_price * (1 + self.tp_front_run_pct)

    def _collect_liquidity_targets(self, side, entry, levels):
        targets = []
        if not levels:
            return targets

        if side == "buy":
            for fvg in levels.get("bearish_fvgs", []):
                if fvg["low"] > entry and fvg["low"] > 0:
                    targets.append({"type": "bearish_fvg", "price": fvg["low"], "quality": 1.1})
            for ob in levels.get("bearish_obs", []):
                if ob["low"] > entry and ob["low"] > 0:
                    targets.append({"type": "bearish_ob_supply", "price": ob["low"], "quality": 1.3})
        else:
            for fvg in levels.get("bullish_fvgs", []):
                if fvg["high"] < entry and fvg["high"] > 0:
                    targets.append({"type": "bullish_fvg", "price": fvg["high"], "quality": 1.1})
            for ob in levels.get("bullish_obs", []):
                if ob["high"] < entry and ob["high"] > 0:
                    targets.append({"type": "bullish_ob_demand", "price": ob["high"], "quality": 1.3})
                    
        return targets

    def calculate_sl_tp(self, side: str, entry_price: float, support: float = None, resistance: float = None, atr: float = None, invalidation_level: float = None, market_levels: dict = None):
        """
        طريقة متوافقة مع الكود القديم لـ main.py تقوم بحساب الـ SL وتستخدم خطة التداول لاستخراج الـ TP.
        إذا لم تتوفر خطة تحقق RR يرجع None للهدف لإلغاء الصفقة.
        """
        atr_sl_mult = getattr(Config, 'ATR_SL_MULTIPLIER', 1.0)
        use_atr = getattr(Config, 'USE_ATR_TARGETS', False) and atr is not None and atr > 0
        
        # 1. تحديد وقف الخسارة
        if invalidation_level and invalidation_level > 0:
            sl = invalidation_level - (atr * atr_sl_mult if use_atr else entry_price * 0.005) if side == 'buy' else invalidation_level + (atr * atr_sl_mult if use_atr else entry_price * 0.005)
        else:
            sl = entry_price - (atr * atr_sl_mult) if side == 'buy' else entry_price + (atr * atr_sl_mult)

        # 2. خطة التداول لاختيار الـ SMC TP
        if market_levels:
            plan = self.calculate_trade_plan(side, entry_price, sl, market_levels)
            if plan.get("valid"):
                return sl, plan["tp"]
            else:
                logger.warning(f"فشل في اختيار هدف يطابق المواصفات بسبب: {plan.get('reason')}")
                # نرجع None للـ TP حتى يعرف main.py أن هذه الصفقة يجب رفضها لضعف الـ RR
                return sl, None
                
        # (Fallback القديم للطوارئ إذا لم توجد market_levels)
        risk_amount = entry_price - sl if side == 'buy' else sl - entry_price
        min_tp = entry_price + (risk_amount * 1.5) if side == 'buy' else entry_price - (risk_amount * 1.5)
        tp = max(resistance * 0.995 if resistance else min_tp, min_tp) if side == 'buy' else min(support * 1.005 if support else min_tp, min_tp)
        return sl, tp
