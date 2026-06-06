# strategy/filter_profiles.py

from core.logger import logger


FILTER_PROFILE_KEY = "trading_filter_profile"

FILTER_PROFILES = {
    "strict": {
        "label": "🔒 فلاتر شديدة وفرص قليلة",
        "short": "Strict",
        "description": (
            "أعلى مستوى أمان. يشترط شمعة تأكيد + فوليوم + Sweep لحظة الدخول، "
            "ولا يسمح بهدف بديل إذا لم يوجد هدف SMC صالح."
        ),
        "require_volume": True,
        "require_entry_sweep": True,
        "allow_rr_fallback": False,
        "fallback_rr": None,
        "min_rr": 1.5,
    },
    "medium": {
        "label": "⚖️ فلاتر متوسطة",
        "short": "Medium",
        "description": (
            "توازن بين الأمان وعدد الفرص. يشترط شمعة تأكيد + فوليوم، "
            "ولا يشترط Sweep مرة ثانية عند الدخول. يسمح بهدف RR بديل في Testnet."
        ),
        "require_volume": True,
        "require_entry_sweep": False,
        "allow_rr_fallback": True,
        "fallback_rr": 1.5,
        "min_rr": 1.5,
    },
    "relaxed": {
        "label": "⚡ فلاتر خفيفة وفرص أكثر",
        "short": "Relaxed",
        "description": (
            "وضع اختبار فقط. يشترط شمعة تأكيد فقط، والفوليوم والسويب اختياريان. "
            "يزيد عدد الفرص لكنه أعلى مخاطرة."
        ),
        "require_volume": False,
        "require_entry_sweep": False,
        "allow_rr_fallback": True,
        "fallback_rr": 1.3,
        "min_rr": 1.2,
    },
}


def normalize_filter_profile(value: str) -> str:
    value = str(value or "").lower().strip()
    if value not in FILTER_PROFILES:
        return "strict"
    return value


def get_filter_profile(state_manager=None) -> dict:
    """
    قراءة وضع الفلاتر الحالي من state_manager.
    الافتراضي strict حفاظاً على الأمان.
    """
    profile_name = "strict"

    try:
        if state_manager:
            profile_name = state_manager.get(FILTER_PROFILE_KEY, "strict")
    except Exception as e:
        logger.warning(f"[FilterProfile] تعذر قراءة وضع الفلاتر، سيتم استخدام strict: {e}")
        profile_name = "strict"

    profile_name = normalize_filter_profile(profile_name)
    profile = FILTER_PROFILES[profile_name].copy()
    profile["key"] = profile_name
    return profile


def set_filter_profile(state_manager, profile_name: str) -> dict:
    profile_name = normalize_filter_profile(profile_name)

    if state_manager:
        state_manager.set(FILTER_PROFILE_KEY, profile_name)

    profile = FILTER_PROFILES[profile_name].copy()
    profile["key"] = profile_name

    logger.info(f"[FilterProfile] تم تغيير وضع الفلاتر إلى: {profile_name}")
    return profile


def format_filter_profile_status(state_manager=None) -> str:
    profile = get_filter_profile(state_manager)

    return (
        "🎚️ <b>نسبة الفلاتر والشروط</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"الوضع الحالي: <b>{profile['label']}</b>\n"
        f"الكود: <code>{profile['key']}</code>\n\n"
        f"📝 <b>الوصف:</b>\n{profile['description']}\n\n"
        "⚙️ <b>الشروط الحالية:</b>\n"
        f"• Volume Confirm: <code>{'مطلوب' if profile['require_volume'] else 'اختياري'}</code>\n"
        f"• Entry Sweep: <code>{'مطلوب' if profile['require_entry_sweep'] else 'اختياري'}</code>\n"
        f"• Fallback RR: <code>{'مفعل' if profile['allow_rr_fallback'] else 'مغلق'}</code>\n"
        f"• Min RR: <code>{profile['min_rr']}</code>\n\n"
        "⚠️ <b>تنبيه:</b> وضع الفلاتر الخفيفة مخصص للاختبار فقط، وليس للحساب الحقيقي."
    )
