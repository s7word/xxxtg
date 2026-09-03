import asyncio
import base64
import json
import os
import random
import logging
import threading
import time
import uuid
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from telethon import TelegramClient
from telethon.tl import functions, types
from telethon.errors import (
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhoneNumberUnoccupiedError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeEmptyError,
    FloodWaitError,
    SessionPasswordNeededError,
    ApiIdPublishedFloodError
)

from backend.app.config import ConfigManager, SESSIONS_DIR
from backend.app.services.code_delivery import (
    escalation_plan_after_published_flood,
    reconcile_delivery_plan_after_credentials,
    resolve_code_delivery_plan,
)
from backend.app.services.device_alignment import (
    DeviceAlignmentError,
    alignment_summary_for_log,
    classify_push_token,
    describe_push_slot,
    detect_push_slot_conflicts,
    is_strict_alignment,
    validate_strict_device_profile,
)
from backend.app.services.device_profile import DeviceProfileManager
from backend.app.services.init_connection import (
    apply_init_connection_overrides,
    describe_init_connection,
)
from backend.app.services.vault_attestation import take_injected_device_secret
from backend.app.services.vaksms import NoNumberAvailableError, VakSmsService, format_no_number_message
from backend.app.services.grizzlysms import GrizzlySmsService, PROVIDER_LABEL as GRIZZLY_PROVIDER_LABEL
from backend.app.services.smsbower import SmsBowerService, PROVIDER_LABEL as SMSBOWER_PROVIDER_LABEL
from backend.app.services.smscode import (
    SmsCodeService,
    PROVIDER_LABEL as SMSCODE_PROVIDER_LABEL,
    DEFAULT_CANCEL_RETRY_AFTER_SECONDS as SMSCODE_CANCEL_RETRY_AFTER,
)
from backend.app.services.fivesim import FiveSimService, PROVIDER_LABEL as FIVESIM_PROVIDER_LABEL
from backend.app.services.attestation_gateway import AttestationGatewayService
from backend.app.services.reghelp import PUSH_REFUND_MIN_SECONDS, PUSH_REFUND_WINDOW_SECONDS
from backend.app.services.banned_phones import (
    CATEGORY_APP_DELIVERY,
    LOCAL_BANNED_REASON,
    SOURCE_ANTISAFETY,
    SOURCE_PRECHECK,
    SOURCE_SENT_CODE,
    SOURCE_TELEGRAM_RPC,
    BannedPhonesCache,
)
from backend.app.services.phone_precheck import (
    CLEAN_LOG_TEMPLATE,
    DEGRADE_LOG_TEMPLATE,
    PRECHECK_ALREADY_REGISTERED,
    PROBE_POOL_UNUSABLE_REASONS,
    PhonePrecheckService,
    format_precheck_intercept_log,
)
from backend.app.services.push_token_vault import REUSE_PROVIDER as PUSH_REUSE_PROVIDER
from backend.app.services.recaptcha_check import (
    RecaptchaChallengeError,
    parse_recaptcha_check,
)

logger = logging.getLogger("NodeProvisioningOrchestrator")

# 延迟退订后台任务引用，避免被 GC 提前回收
_DEFERRED_SMS_CANCEL_TASKS: set = set()


def _track_background_task(task: "asyncio.Task"):
    _DEFERRED_SMS_CANCEL_TASKS.add(task)
    task.add_done_callback(_DEFERRED_SMS_CANCEL_TASKS.discard)
    return task


SYNTHETIC_IDENTITY_POOLS = {
    "cl": {
        "first": ["Mateo", "Agustín", "Santiago", "Tomás", "Lucas", "Benjamín", "Matías", "Sofía", "Isabella", "Emilia"],
        "last": ["González", "Muñoz", "Rojas", "Díaz", "Pérez", "Soto", "Contreras", "Silva", "Martínez", "Sepúlveda"]
    },
    "id": {
        "first": ["Budi", "Agus", "Putra", "Rizky", "Bayu", "Dewi", "Siti", "Nur", "Tri", "Hendra"],
        "last": ["Wijaya", "Kusuma", "Santoso", "Saputra", "Pratama", "Setiawan", "Utomo", "Gunawan", "Susanto"]
    },
    "ru": {
        "first": ["Alexandr", "Dmitry", "Maxim", "Sergey", "Andrey", "Elena", "Anna", "Olga", "Tatiana"],
        "last": ["Ivanov", "Smirnov", "Kuznetsov", "Popov", "Vasiliev", "Petrov", "Sokolov", "Mikhailov"]
    },
    "ca": {
        "first": ["Liam", "Noah", "Oliver", "Emma", "Olivia", "Charlotte", "Étienne", "Amélie", "Gabriel", "Léa", "William", "Sophia"],
        "last": ["Tremblay", "Gagnon", "Roy", "Côté", "Bouchard", "Smith", "Brown", "Wilson", "MacDonald", "Martin"]
    },
    "us": {
        "first": ["James", "Michael", "Emily", "Jessica", "Daniel", "Ashley", "Matthew", "Amanda", "Christopher", "Sarah"],
        "last": ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Wilson", "Anderson"]
    },
    "gb": {
        "first": ["Oliver", "George", "Harry", "Amelia", "Isla", "Ava", "Noah", "Lily", "Jack", "Sophie"],
        "last": ["Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Patel", "Wright"]
    },
    "au": {
        "first": ["Jack", "Olivia", "William", "Charlotte", "Noah", "Mia", "Lucas", "Amelia", "Henry", "Isla"],
        "last": ["Smith", "Jones", "Williams", "Brown", "Wilson", "Taylor", "Johnson", "White", "Martin", "Anderson"]
    },
    "de": {
        "first": ["Lukas", "Maximilian", "Leon", "Hannah", "Emma", "Mia", "Felix", "Sophie", "Paul", "Lina"],
        "last": ["Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker", "Hoffmann", "Schulz"]
    },
    "fr": {
        "first": ["Louis", "Gabriel", "Hugo", "Jade", "Louise", "Emma", "Arthur", "Chloé", "Raphaël", "Manon"],
        "last": ["Martin", "Bernard", "Dubois", "Thomas", "Robert", "Richard", "Petit", "Durand", "Leroy", "Moreau"]
    },
    "jp": {
        "first": ["Haruto", "Yuto", "Sota", "Yuki", "Hana", "Sakura", "Ren", "Mei", "Kaito", "Aoi"],
        "last": ["Sato", "Suzuki", "Takahashi", "Tanaka", "Watanabe", "Ito", "Yamamoto", "Nakamura", "Kobayashi", "Kato"]
    },
    "kr": {
        "first": ["Minjun", "Seojoon", "Jisoo", "Minseo", "Jiho", "Soyeon", "Hyunwoo", "Yuna", "Jimin", "Haeun"],
        "last": ["Kim", "Lee", "Park", "Choi", "Jung", "Kang", "Cho", "Yoon", "Jang", "Lim"]
    },
    "th": {
        "first": ["Nattapong", "Somsak", "Pichai", "Siriporn", "Nongnuch", "Arthit", "Kanokwan", "Wichai", "Malee", "Prasert"],
        "last": ["Saetang", "Srisuk", "Chaiyaporn", "Wongchai", "Suwannapong", "Rattanakul", "Phanich", "Jirapong"]
    },
    "vn": {
        "first": ["Minh", "Hung", "Duc", "Linh", "Trang", "Anh", "Tuan", "Huong", "Nam", "Thao"],
        "last": ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Phan", "Vu", "Vo", "Dang", "Bui"]
    },
    "ph": {
        "first": ["Jose", "Maria", "Juan", "Ana", "Mark", "Angel", "Carlo", "Princess", "Miguel", "Nicole"],
        "last": ["Santos", "Reyes", "Cruz", "Bautista", "Gonzales", "Ramos", "Aquino", "Garcia", "Mendoza", "Torres"]
    },
    "mx": {
        "first": ["Santiago", "Mateo", "Valentina", "Ximena", "Diego", "Camila", "Sebastián", "Sofía", "Emiliano", "Regina"],
        "last": ["Hernández", "García", "Martínez", "López", "González", "Pérez", "Sánchez", "Ramírez", "Torres", "Flores"]
    },
    "co": {
        "first": ["Santiago", "Mariana", "Andrés", "Valentina", "Juan", "Isabella", "Camilo", "Laura", "Daniel", "Salomé"],
        "last": ["Rodríguez", "García", "Martínez", "López", "Hernández", "González", "Pérez", "Sánchez", "Ramírez", "Díaz"]
    },
    "pe": {
        "first": ["Luciana", "Mateo", "Valeria", "Sebastián", "Camila", "Diego", "Renata", "Joaquín", "Ana", "Luis"],
        "last": ["Quispe", "Flores", "Rojas", "Sánchez", "García", "Torres", "Díaz", "Vargas", "Castillo", "Mendoza"]
    },
    "ar": {
        "first": ["Juan", "Martina", "Benjamín", "Sofía", "Thiago", "Valentina", "Felipe", "Catalina", "Nicolás", "Emma"],
        "last": ["González", "Rodríguez", "Fernández", "López", "Martínez", "Pérez", "García", "Sánchez", "Romero", "Álvarez"]
    },
    "br": {
        "first": ["Miguel", "Alice", "Arthur", "Helena", "Heitor", "Laura", "Davi", "Valentina", "Theo", "Sophia"],
        "last": ["Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira", "Alves", "Pereira", "Lima", "Gomes"]
    },
    "tr": {
        "first": ["Mehmet", "Ayşe", "Mustafa", "Fatma", "Ahmet", "Elif", "Emre", "Zeynep", "Yusuf", "Hatice"],
        "last": ["Yılmaz", "Kaya", "Demir", "Şahin", "Çelik", "Yıldız", "Yıldırım", "Öztürk", "Aydin", "Özdemir"]
    },
    "in": {
        "first": ["Aarav", "Vivaan", "Aditya", "Ananya", "Diya", "Ishaan", "Kiara", "Rohan", "Priya", "Arjun"],
        "last": ["Sharma", "Patel", "Singh", "Kumar", "Gupta", "Reddy", "Nair", "Mehta", "Joshi", "Iyer"]
    },
    "kz": {
        "first": ["Alikhan", "Nurasyl", "Aisulu", "Amina", "Dias", "Tomiris", "Alina", "Bekzat", "Madina", "Nurlan"],
        "last": ["Nurzhanov", "Suleimenov", "Omarov", "Abdullayev", "Kim", "Ivanov", "Serikova", "Tulegenov"]
    },
    "ua": {
        "first": ["Oleksandr", "Andriy", "Dmytro", "Anna", "Olena", "Sofiia", "Maksym", "Kateryna", "Ivan", "Yulia"],
        "last": ["Shevchenko", "Kovalenko", "Bondarenko", "Tkachenko", "Kravchenko", "Melnyk", "Shevchuk", "Boyko"]
    },
    "uz": {
        "first": ["Jasur", "Dilshod", "Aziza", "Madina", "Bekzod", "Nilufar", "Sardor", "Gulnora", "Otabek", "Sevara"],
        "last": ["Karimov", "Tursunov", "Rakhimov", "Usmanov", "Alimov", "Yusupov", "Ismoilov", "Nazarov"]
    },
    "ae": {
        "first": ["Omar", "Fatima", "Khalid", "Aisha", "Mohammed", "Layla", "Sultan", "Noor", "Yousef", "Mariam"],
        "last": ["Al Maktoum", "Al Nahyan", "Al Falasi", "Al Suwaidi", "Al Mazrouei", "Hassan", "Abdullah", "Ibrahim"]
    },
    "sa": {
        "first": ["Abdullah", "Mohammed", "Sara", "Noura", "Fahad", "Lama", "Khalid", "Reem", "Saud", "Hessa"],
        "last": ["Al Saud", "Al Qahtani", "Al Ghamdi", "Al Harbi", "Al Otaibi", "Al Zahrani", "Al Dossari", "Al Shehri"]
    },
    "eg": {
        "first": ["Omar", "Youssef", "Fatma", "Nour", "Ahmed", "Mariam", "Hassan", "Salma", "Mahmoud", "Hana"],
        "last": ["Mohamed", "Ahmed", "Ibrahim", "Hassan", "Ali", "Mahmoud", "Mostafa", "Hussein", "Said", "Farouk"]
    },
    "za": {
        "first": ["Thabo", "Lerato", "Sipho", "Nomsa", "Johan", "Anika", "Kagiso", "Zanele", "Pieter", "Amara"],
        "last": ["Dlamini", "Ndlovu", "Botha", "Van der Merwe", "Nkosi", "Mokoena", "Naidoo", "Pretorius"]
    },
    "ng": {
        "first": ["Chinedu", "Amina", "Emeka", "Ngozi", "Tunde", "Fatima", "Ifeanyi", "Blessing", "Oluwaseun", "Aisha"],
        "last": ["Okafor", "Adeyemi", "Ibrahim", "Okeke", "Balogun", "Musa", "Eze", "Abdullahi", "Okonkwo"]
    },
    "ke": {
        "first": ["Brian", "Faith", "Kevin", "Mercy", "Daniel", "Aisha", "Samuel", "Wanjiku", "Collins", "Grace"],
        "last": ["Otieno", "Mwangi", "Kamau", "Wanjala", "Omondi", "Njeri", "Kipchoge", "Achieng", "Mutua"]
    },
    "af": {
        "first": ["Ahmad", "Omar", "Fatima", "Zahra", "Hassan", "Maryam", "Karim", "Laila", "Farid", "Soraya"],
        "last": ["Ahmadi", "Rahimi", "Mohammadi", "Karimi", "Hosseini", "Nazari", "Sadat", "Stanikzai"]
    },
    "default": {
        "first": ["James", "Alex", "David", "Elena", "Marcus", "Lucas", "Sophie", "Michael", "Daniel"],
        "last": ["Smith", "Brown", "Wilson", "Taylor", "Anderson", "White", "Miller", "Davis"]
    }
}
GEO_NAME_POOLS = SYNTHETIC_IDENTITY_POOLS

CONNECT_TIMEOUT_SECONDS = 45.0
MAX_RETAINED_TASKS = 200
TERMINAL_TASK_STATUSES = frozenset({"success", "failed", "filtered", "canceled"})
MAX_RESEND_WAIT_SECONDS = 90.0
DEFAULT_SMS_POLL_ATTEMPTS = 30
SMS_POLL_INTERVAL_SECONDS = 4.0
PUSH_REFUND_SETSTATUS_RESERVE_SECONDS = 25.0
FAST_FAIL_SMS_POLL_ATTEMPTS = 3
BATCH_COUNT_MIN = 1
BATCH_COUNT_MAX = 10
BATCH_CONCURRENCY_MIN = 1
BATCH_CONCURRENCY_MAX = 10

SMS_PROVIDER_ALIASES = {
    "fivesim": "fivesim",
    "5sim": "fivesim",
    "5simnet": "fivesim",
    "five_sim": "fivesim",
    "five-sim": "fivesim",
    "grizzly": "grizzlysms",
    "grizzlysms": "grizzlysms",
    "grizzly_sms": "grizzlysms",
    "grizzly-sms": "grizzlysms",
    "smsbower": "smsbower",
    "sms-bower": "smsbower",
    "sms_bower": "smsbower",
    "bower": "smsbower",
    "smsbowerapp": "smsbower",
    "smscode": "smscode",
    "sms-code": "smscode",
    "sms_code": "smscode",
    "smscodegg": "smscode",
    "sms-code-gg": "smscode",
    "smscode.gg": "smscode",
    "vak": "vaksms",
    "vaksms": "vaksms",
    "vak_sms": "vaksms",
    "vak-sms": "vaksms",
}
SMS_PROVIDER_LABELS = {
    "fivesim": FIVESIM_PROVIDER_LABEL,
    "grizzlysms": GRIZZLY_PROVIDER_LABEL,
    "smsbower": SMSBOWER_PROVIDER_LABEL,
    "smscode": SMSCODE_PROVIDER_LABEL,
    "vaksms": "Vak-SMS (vak-sms.com)",
}

# Telegram auth.sendCode 分发通道：站内信无法被 Vak-SMS 蜂窝网关接收
APP_DELIVERY_TYPE_NAMES = frozenset({
    "SentCodeTypeApp",
})
SMS_DELIVERY_TYPE_NAMES = frozenset({
    "SentCodeTypeSms",
    "SentCodeTypeFragmentSms",
    "SentCodeTypeFirebaseSms",
})
SMS_NEXT_TYPE_NAMES = frozenset({
    "CodeTypeSms",
    "SentCodeTypeSms",
})
PAYMENT_REQUIRED_TYPE_NAMES = frozenset({
    "SentCodePaymentRequired",
})
EMAIL_SETUP_TYPE_NAMES = frozenset({
    "SentCodeTypeSetUpEmailRequired",
})
EMAIL_CODE_TYPE_NAMES = frozenset({
    "SentCodeTypeEmailCode",
})
FIREBASE_SMS_TYPE_NAMES = frozenset({
    "SentCodeTypeFirebaseSms",
})
APP_STREAK_REASONS = frozenset({"SENT_CODE_TYPE_APP"})
CHANNEL_FAIL_NOTES = {
    "SENT_CODE_TYPE_APP": "auth.sendCode 仅下发站内 App 推送（未必已注册）",
    "PAYMENT_REQUIRED_OFFICIAL_ONLY": "需官方 App 内购，自动化不可完成",
    "EMAIL_SETUP_FAILED": "SetUpEmailRequired 流程失败",
    "EMAIL_CODE_UNAVAILABLE": "SentCodeTypeEmailCode 无法接收该邮箱验证码",
}


MAX_NUMBER_ATTEMPTS_CAP = 500
# 猎号默认策略：成功即停；否则尽量扫平台号码并拉黑。无库存软重试与出口/指纹轮换上限。
DEFAULT_HUNT_NO_NUMBER_RETRIES = 20
DEFAULT_HUNT_NO_NUMBER_DELAY_SEC = 2.0
DEFAULT_HUNT_PROXY_MAX_USES = 5
DEFAULT_HUNT_DEVICE_MAX_USES = 8
# 猎号联合上限：单任务取号次数 × 批次任务数 = 本次最多向接码平台租号的次数。
# 500 × 10 会一次把余额抽干，所以默认把乘积压到 200，可由 config.hunt_max_total_leases 覆盖。
DEFAULT_HUNT_MAX_TOTAL_LEASES = 200
# 猎号跨轮复用 Push Token 时，REGHelp 的 180s 退款窗口会把 OTP 轮询越压越短。
# Token 签发超过该秒数、或窗口余量已不足 HUNT_MIN_SMS_POLL_ATTEMPTS 次轮询时，
# 先退旧 Token 再换新的，保证真 SMS 号有完整收码时间。
HUNT_PUSH_TOKEN_MAX_AGE_SECONDS = 90.0
HUNT_MIN_SMS_POLL_ATTEMPTS = 8
# auth.sendCode 只投站内 App：不可用是事实，「已注册」只是推测（attach 了 Push Token 时
# 服务端也可能改走推送）。所以按 TTL 临时拉黑而不是永久 already_registered。
DEFAULT_HUNT_APP_BLACKLIST_TTL_HOURS = 48.0
# 连续 N 轮都只投 App 说明是系统性失败模式（凭证/Push/指纹），继续扫号只是把预算烧光
DEFAULT_HUNT_APP_DELIVERY_FUSE = 5
# 猎号 FLOOD_WAIT：≥该秒数直接终止整个猎号任务。未达阈值时等待完整窗口
# （不再用 30s 上限短退避后再发，否则等于把 Telegram 的 FLOOD 窗填满）。
HUNT_FLOOD_ABORT_SECONDS = 3600
HUNT_FLOOD_BACKOFF_CAP_SECONDS = 30.0  # 仅作日志/兼容；实际等待不再截断到此值
# 无 seconds 的 API_ID_PUBLISHED_FLOOD：仅作本任务冷却记录；默认不拦兄弟/新租号
DEFAULT_PUBLISHED_FLOOD_PAUSE_SECONDS = 120.0
# 猎号专用的内部原因标识 → REGHelp 已支持的规范原因。
# 主动轮换/扫尽的共同事实是「这枚 Token 始终没等到短信」，与 NO_CODE 同类（setStatus=NOSMS）；
# 被频控打死则按 FLOOD_WAIT 上报。收敛在这里，避免为猎号私有标识改动对外映射表。
HUNT_REFUND_REASON_ALIASES = {
    "HUNT_DEVICE_ROTATE": "NO_CODE",
    "HUNT_PUSH_ROTATE": "NO_CODE",
    "HUNT_EXHAUSTED": "NO_CODE",
    "HUNT_CANCELED": "NO_CODE",
    "HUNT_APP_FUSE": "NO_CODE",
    "HUNT_FLOOD_ABORT": "FLOOD_WAIT",
    "HUNT_FLOOD_NO_PROXY": "FLOOD_WAIT",
    "HUNT_FLOOD_WINDOW": "FLOOD_WAIT",
    "PROXY_COUNTRY_MISMATCH": "NO_CODE",
}

# 猎号中「换个号就可能好」的 sendCode 异常：cancel + 按规则拉黑后继续下一轮，
# 不能因为一个坏号就把剩余取号预算整体作废。
# API_ID_PUBLISHED_FLOOD：默认只表示「本号/本轮失败」；猎号继续租下一号以便排查。
# 省钱硬停（不再开新租号）需显式 flood_block_new_sends=true。
HUNT_SWAPPABLE_SEND_ERROR_REASONS = {
    PhoneNumberInvalidError: "PHONE_NUMBER_INVALID",
}
HUNT_SWAPPABLE_SEND_ERRORS = tuple(HUNT_SWAPPABLE_SEND_ERROR_REASONS)


class SentCodeAppDeliveryError(Exception):
    """服务端将验证码下发到已登录官方客户端，带外短信网关无法接收。"""

    def __init__(self, message: str, reason: str = "SENT_CODE_TYPE_APP"):
        super().__init__(message)
        self.reason = reason


class RequiredPushTokenMissingError(Exception):
    """通道计划要求 attach Push，但网关没返回可用 token，拒绝裸发 published api_id。"""

    def __init__(self, message: str, api_id: Optional[int] = None):
        super().__init__(message)
        self.api_id = api_id
        self.reason = "PUSH_TOKEN_MISSING"


class ProxyCountryMismatchError(Exception):
    """代理 geo 与号码国家不一致，拒绝租号/发码。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.reason = "PROXY_COUNTRY_MISMATCH"


class SendCodeFloodWindow:
    """进程级 FLOOD 窗：记录冷却；默认可继续新租号/发码（便于并发探测）。

    API_ID_PUBLISHED_FLOOD 只结束触发它的那一号/任务；省钱硬停需显式
    flood_block_new_sends=true。普通 FLOOD_WAIT 仍可软等满窗再发。
    """

    _instance: Optional["SendCodeFloodWindow"] = None

    def __init__(self) -> None:
        self._until = 0.0
        self._reason: Optional[str] = None
        self._hard = False
        self._api_id: Optional[int] = None

    @classmethod
    def get(cls) -> "SendCodeFloodWindow":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reset(self) -> None:
        self._until = 0.0
        self._reason = None
        self._hard = False
        self._api_id = None

    def clear_published_flood(self) -> bool:
        """清掉 API_ID_PUBLISHED_FLOOD 残留闩（不碰普通 FLOOD_WAIT 软窗）。"""
        reason = str(self._reason or "").upper()
        if reason != "API_ID_PUBLISHED_FLOOD":
            return False
        self.reset()
        return True

    def remaining(self) -> float:
        return max(0.0, self._until - time.monotonic())

    def reason(self) -> Optional[str]:
        if self.remaining() <= 0 and not self._hard:
            return None
        return self._reason

    def api_id(self) -> Optional[int]:
        if self.remaining() <= 0 and not self._hard:
            return None
        return self._api_id

    def is_hard_stop(self) -> bool:
        """硬门闩：仅在显式 flood_block_new_sends（或等价）开启时才会拦新发码。"""
        return bool(self._hard) and self.remaining() > 0

    def trip(
        self,
        *,
        reason: str,
        seconds: float = 0.0,
        hard: bool = False,
        api_id: Optional[int] = None,
        hold_seconds: Optional[float] = None,
    ) -> float:
        wait = max(float(seconds or 0.0), 0.0)
        if wait <= 0:
            wait = float(HUNT_FLOOD_ABORT_SECONDS) if hard else float(DEFAULT_PUBLISHED_FLOOD_PAUSE_SECONDS)
        wait = min(wait, float(HUNT_FLOOD_ABORT_SECONDS))
        until = time.monotonic() + wait
        if until > self._until:
            self._until = until
            self._reason = reason
        elif not self._reason:
            self._reason = reason
        if api_id is not None:
            try:
                self._api_id = int(api_id)
            except (TypeError, ValueError):
                self._api_id = None
        if hard:
            self._hard = True
            # published 闸默认用较短冷却（可配置）；小时级 FLOOD_WAIT 仍顶满中止阈值
            hold = hold_seconds
            if hold is None:
                if str(reason or "").upper() == "API_ID_PUBLISHED_FLOOD":
                    hold = float(DEFAULT_PUBLISHED_FLOOD_PAUSE_SECONDS)
                else:
                    hold = float(HUNT_FLOOD_ABORT_SECONDS)
            hard_until = time.monotonic() + max(float(hold), wait)
            if hard_until > self._until:
                self._until = hard_until
        return wait


class RegistrationTaskManager:
    """边缘节点引导任务与状态机审计追踪管理器 (Node Provisioning Task Manager)"""
    _instance = None
    _lock = threading.RLock()
    tasks: Dict[str, Dict[str, Any]] = {}
    batches: Dict[str, Dict[str, Any]] = {}
    max_retained_tasks = MAX_RETAINED_TASKS

    @classmethod
    def get_instance(cls) -> "RegistrationTaskManager":
        if cls._instance is None:
            cls._instance = RegistrationTaskManager()
        return cls._instance

    def _evict_overflow_unlocked(self) -> None:
        """超过容量时淘汰最旧的已完成任务，避免内存无限增长。"""
        limit = getattr(self, "max_retained_tasks", MAX_RETAINED_TASKS) or MAX_RETAINED_TASKS
        overflow = len(self.tasks) - limit + 1
        if overflow <= 0:
            return
        completed = [
            (tid, task)
            for tid, task in self.tasks.items()
            if (task.get("status") or "") in TERMINAL_TASK_STATUSES
        ]
        completed.sort(key=lambda item: item[1].get("updated_at") or item[1].get("created_at") or "")
        for tid, _ in completed[:overflow]:
            self.tasks.pop(tid, None)

    def create_task(self, batch_id: Optional[str] = None) -> str:
        task_id = str(uuid.uuid4())[:8]
        now = datetime.datetime.now().isoformat()
        with self._lock:
            self._evict_overflow_unlocked()
            self.tasks[task_id] = {
                "task_id": task_id,
                "status": "pending",
                "phone": None,
                "user_id": None,
                "error": None,
                "logs": [],
                "batch_id": batch_id,
                "precheck_intercepted": False,
                "precheck_user_id": None,
                "banned_cache_hit": False,
                "no_number": False,
                "created_at": now,
                "updated_at": now
            }
        return task_id

    def create_batch(
        self,
        count: int,
        concurrency: int,
        country: Optional[str] = None,
        app_type: Optional[str] = None,
    ) -> Tuple[str, List[str]]:
        """创建一批并行引导任务，返回 (batch_id, task_ids)。"""
        batch_id = str(uuid.uuid4())[:8]
        now = datetime.datetime.now().isoformat()
        task_ids: List[str] = []
        with self._lock:
            for _ in range(count):
                self._evict_overflow_unlocked()
                task_id = str(uuid.uuid4())[:8]
                self.tasks[task_id] = {
                    "task_id": task_id,
                    "status": "pending",
                    "phone": None,
                    "user_id": None,
                    "error": None,
                    "logs": [],
                    "batch_id": batch_id,
                    "precheck_intercepted": False,
                    "precheck_user_id": None,
                    "banned_cache_hit": False,
                    "no_number": False,
                    "created_at": now,
                    "updated_at": now,
                }
                task_ids.append(task_id)
            self.batches[batch_id] = {
                "batch_id": batch_id,
                "task_ids": list(task_ids),
                "count": count,
                "concurrency": concurrency,
                "country": country,
                "app_type": app_type,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            }
        return batch_id, task_ids

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.tasks.get(task_id)

    def _enrich_batch_unlocked(self, batch: Dict[str, Any]) -> Dict[str, Any]:
        task_ids = list(batch.get("task_ids") or [])
        statuses: List[str] = []
        success = running = failed = pending = 0
        precheck_intercepted = 0
        no_number = 0
        for tid in task_ids:
            task = self.tasks.get(tid) or {}
            status = task.get("status") or "pending"
            statuses.append(status)
            if status == "success":
                success += 1
            elif status == "failed":
                failed += 1
            elif status == "filtered":
                failed += 1
            elif status == "canceled":
                failed += 1
            elif status in {"running", "waiting_code", "logging_in"}:
                running += 1
            else:
                pending += 1
            if task.get("precheck_intercepted") or (
                "PRECHECK_PHONE_ALREADY_REGISTERED" in str(task.get("error") or "")
            ):
                precheck_intercepted += 1
            if task.get("no_number"):
                no_number += 1
        if task_ids and all(s == "success" for s in statuses):
            agg = "success"
        elif task_ids and all(s in TERMINAL_TASK_STATUSES for s in statuses):
            agg = "failed" if failed == len(statuses) else "partial"
        elif running:
            agg = "running"
        else:
            agg = "pending"
        enriched = dict(batch)
        enriched.update({
            "status": agg,
            "success": success,
            "failed": failed,
            "running": running,
            "pending": pending,
            "precheck_intercepted": precheck_intercepted,
            "no_number": no_number,
        })
        return enriched

    def get_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            batch = self.batches.get(batch_id)
            if not batch:
                return None
            return self._enrich_batch_unlocked(batch)

    def list_batches(self) -> List[Dict[str, Any]]:
        with self._lock:
            snapshot = [self._enrich_batch_unlocked(b) for b in self.batches.values()]
        return sorted(snapshot, key=lambda x: x.get("created_at") or "", reverse=True)

    def list_tasks(
        self,
        batch_id: Optional[str] = None,
        *,
        include_logs: bool = False,
        active_task_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出任务快照。

        默认不回传全量 logs（轮询场景下数百任务 × 长日志会拖垮前后端）。
        仅对 active_task_id 或 include_logs=True 保留完整日志；其余只带 last_log / log_count。
        """
        with self._lock:
            snapshot = [dict(item) for item in self.tasks.values()]
        if batch_id:
            snapshot = [item for item in snapshot if item.get("batch_id") == batch_id]
        if include_logs:
            return sorted(snapshot, key=lambda x: x["created_at"], reverse=True)

        slim: List[Dict[str, Any]] = []
        active = (active_task_id or "").strip()
        for item in snapshot:
            logs = list(item.get("logs") or [])
            row = dict(item)
            row["log_count"] = len(logs)
            row["last_log"] = logs[-1] if logs else None
            if active and item.get("task_id") == active:
                row["logs"] = logs
            else:
                # 列表/徽章只需要状态；保留末尾几行供合并视图冷启动
                row["logs"] = logs[-3:] if logs else []
            slim.append(row)
        return sorted(slim, key=lambda x: x["created_at"], reverse=True)

    async def append_log(self, task_id: str, message: str):
        with self._lock:
            if task_id not in self.tasks:
                return
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            log_entry = f"[{timestamp}] {message}"
            self.tasks[task_id]["logs"].append(log_entry)
            self.tasks[task_id]["updated_at"] = datetime.datetime.now().isoformat()
        logger.info(f"[{task_id}] {message}")

    def update_task_status(self, task_id: str, status: str, **kwargs):
        with self._lock:
            if task_id not in self.tasks:
                return
            self.tasks[task_id]["status"] = status
            self.tasks[task_id]["updated_at"] = datetime.datetime.now().isoformat()
            for k, v in kwargs.items():
                self.tasks[task_id][k] = v

    def request_cancel(self, task_id: str) -> Optional[Dict[str, Any]]:
        """给任务置取消标记，返回 (状态, 是否已直接终止) 摘要；任务不存在返回 None。

        pending 任务还没有进入编排循环，直接置终态 canceled 即可；running 及其后续阶段
        只能置 cancel_requested，由 `_hunt_cancel_requested` 在下一轮取号前收住。
        """
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return None
            status = task.get("status") or "pending"
            now = datetime.datetime.now().isoformat()
            if status in TERMINAL_TASK_STATUSES:
                return {"task_id": task_id, "status": status, "changed": False, "terminated": False}
            task["cancel_requested"] = True
            task["cancel_requested_at"] = now
            task["updated_at"] = now
            if status == "pending":
                task["status"] = "canceled"
                return {"task_id": task_id, "status": "canceled", "changed": True, "terminated": True}
            return {"task_id": task_id, "status": status, "changed": True, "terminated": False}

    def request_batch_cancel(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """批次级取消：逐个任务置标记，返回受理 / 直接终止 / 已终态的分组。"""
        with self._lock:
            batch = self.batches.get(batch_id)
            if not batch:
                return None
            task_ids = list(batch.get("task_ids") or [])
            batch["cancel_requested"] = True
            batch["updated_at"] = datetime.datetime.now().isoformat()
        requested: List[str] = []
        terminated: List[str] = []
        skipped: List[str] = []
        for tid in task_ids:
            result = self.request_cancel(tid)
            if result is None or not result["changed"]:
                skipped.append(tid)
            elif result["terminated"]:
                terminated.append(tid)
            else:
                requested.append(tid)
        return {
            "batch_id": batch_id,
            "task_ids": task_ids,
            "requested": requested,
            "terminated": terminated,
            "skipped": skipped,
        }

    def cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            return task.get("status") == "canceled" or bool(task.get("cancel_requested"))


NodeProvisioningTaskManager = RegistrationTaskManager


class RegistrationOrchestrator:
    """分布式边缘节点引导与密码学状态机编排引擎 (Node Provisioning & Cryptographic State Orchestrator)"""

    @staticmethod
    def _get_random_name(country: str) -> Tuple[str, str]:
        pool = SYNTHETIC_IDENTITY_POOLS.get(country.lower(), SYNTHETIC_IDENTITY_POOLS["default"])
        return random.choice(pool["first"]), random.choice(pool["last"])

    @staticmethod
    def normalize_sms_provider(value: Optional[str] = None) -> str:
        token = str(value or "").strip().lower()
        if not token:
            return "fivesim"
        compact = token.replace("-", "").replace("_", "")
        return SMS_PROVIDER_ALIASES.get(token) or SMS_PROVIDER_ALIASES.get(compact) or "fivesim"

    @classmethod
    def resolve_sms_provider(cls, config=None, sms_provider: Optional[str] = None) -> str:
        if sms_provider:
            return cls.normalize_sms_provider(sms_provider)
        configured = getattr(config, "sms_provider", None) if config is not None else None
        return cls.normalize_sms_provider(configured)

    @classmethod
    def _create_sms_service(cls, config, sms_provider: Optional[str] = None):
        """按全局配置或单次任务覆盖动态实例化接码客户端。"""
        provider = cls.resolve_sms_provider(config, sms_provider)
        if provider == "vaksms":
            return VakSmsService(getattr(config, "vak_sms_api_key", "") or "")
        if provider == "grizzlysms":
            return GrizzlySmsService(getattr(config, "grizzly_sms_api_key", "") or "")
        if provider == "smsbower":
            return SmsBowerService(getattr(config, "smsbower_api_key", "") or "")
        if provider == "smscode":
            return SmsCodeService(getattr(config, "smscode_api_key", "") or "")
        return FiveSimService(getattr(config, "fivesim_api_key", "") or "")

    @staticmethod
    def resolve_sms_max_price(config=None, max_price=None):
        """任务级 max_price 优先，其次系统 sms_max_price；均未设置则返回 None。"""
        from backend.app.models.schemas import normalize_sms_max_price

        bid = normalize_sms_max_price(max_price)
        if bid is not None:
            return bid
        if config is None:
            return None
        return normalize_sms_max_price(getattr(config, "sms_max_price", None))

    @classmethod
    def _sms_provider_label(cls, sms_svc, provider: Optional[str] = None) -> str:
        label = getattr(sms_svc, "PROVIDER_LABEL", None)
        if label and not hasattr(label, "_mock_name"):
            return str(label)
        name = getattr(sms_svc, "PROVIDER_NAME", None) or provider
        if not name or hasattr(name, "_mock_name"):
            cls_name = type(sms_svc).__name__.lower()
            if "vak" in cls_name:
                name = "vaksms"
            elif "smsbower" in cls_name or "bower" in cls_name:
                name = "smsbower"
            elif "smscode" in cls_name:
                name = "smscode"
            elif "grizzly" in cls_name:
                name = "grizzlysms"
            elif "fivesim" in cls_name or "five" in cls_name:
                name = "fivesim"
        return SMS_PROVIDER_LABELS.get(cls.normalize_sms_provider(name), SMS_PROVIDER_LABELS["fivesim"])

    @classmethod
    async def _deferred_smscode_cancel(
        cls,
        api_key: str,
        act_id: str,
        wait_seconds: float,
        task_id: str,
        manager: RegistrationTaskManager,
        reason: str,
    ) -> None:
        """SMSCode CANCEL_TOO_EARLY：等冷却后再 cancel，避免订单挂着扣余额。"""
        delay = max(1.0, float(wait_seconds or SMSCODE_CANCEL_RETRY_AFTER) + 1.0)
        try:
            await asyncio.sleep(delay)
            async with SmsCodeService(api_key=api_key) as svc:
                result = await svc.cancel(act_id, wait_if_too_early=True, max_wait=90.0)
            if result.get("success"):
                await manager.append_log(
                    task_id,
                    f"[后台延迟退订完成] SMSCode act_id={act_id} "
                    f"(等 {delay:.0f}s 后成功，原因: {reason})",
                )
            else:
                await manager.append_log(
                    task_id,
                    f"⚠️ [后台延迟退订仍失败] SMSCode act_id={act_id}: "
                    f"{result.get('error') or result.get('status')}",
                )
        except Exception as exc:
            logger.warning("SMSCode 后台延迟退订异常 act_id=%s: %s", act_id, exc)
            try:
                await manager.append_log(
                    task_id,
                    f"⚠️ [后台延迟退订异常] SMSCode act_id={act_id}: {exc}",
                )
            except Exception:
                pass

    @classmethod
    async def _refund_and_revoke_channel(
        cls,
        sms_svc,
        act_id: Optional[str],
        task_id: str,
        manager: RegistrationTaskManager,
        reason: str,
    ) -> None:
        """失败路径统一走对应接码源 cancel()，触发取消与自动退款。"""
        if not act_id:
            return
        result = await sms_svc.cancel(act_id)
        if result.get("skipped"):
            return
        status = result.get("status")
        provider_label = cls._sms_provider_label(sms_svc)
        if result.get("success"):
            await manager.append_log(
                task_id,
                f"[自动退订/撤销信道句柄完成] act_id={act_id} ({provider_label} status={status}, 原因: {reason})"
            )
            return

        too_early = bool(result.get("early_cancel") or result.get("status") == "CANCEL_TOO_EARLY")
        api_key = str(getattr(sms_svc, "api_key", "") or "").strip()
        if too_early and api_key and isinstance(sms_svc, SmsCodeService):
            wait_s = float(result.get("retry_after") or SMSCODE_CANCEL_RETRY_AFTER)
            await manager.append_log(
                task_id,
                f"⚠️ SMSCode 取消过早 (CANCEL_TOO_EARLY，约需再等 {wait_s:.0f}s)；"
                f"已安排后台延迟退订 act_id={act_id}（原因: {reason}），本任务继续换号",
            )
            task = asyncio.create_task(
                cls._deferred_smscode_cancel(
                    api_key, str(act_id), wait_s, task_id, manager, reason
                )
            )
            _track_background_task(task)
            return

        detail = result.get("error") or result.get("data") or "unknown"
        await manager.append_log(
            task_id,
            f"⚠️ 自动退订/撤销信道句柄未成功 (act_id={act_id}, 原因: {reason}): {detail}"
        )

    @classmethod
    def _sms_poll_attempts_for_push_window(
        cls,
        requested_attempts: int,
        push_provider: Optional[str],
        push_token_obtained_at: Optional[float],
        interval: float = SMS_POLL_INTERVAL_SECONDS,
        min_attempts: int = 1,
    ) -> int:
        """REGHelp 路径把短信轮询截断到退款窗口内，给 setStatus 留出余量。

        `min_attempts` 是收码保底：猎号会传 HUNT_MIN_SMS_POLL_ATTEMPTS，宁可丢掉这枚
        Token 的退款也不能把真 SMS 号的收码窗口砍到收不到码（退款只是成本，收不到码是白扫）。
        """
        requested = max(1, int(requested_attempts or 1))
        floor = max(1, min(int(min_attempts or 1), requested))
        # 复用令牌（reghelp_reuse）永远不会再走 setStatus 退款，没有需要保护的窗口，
        # 因此不做截断；它的「太老了」问题由 _push_token_window_exhausted 换新解决。
        if push_provider != "reghelp" or push_token_obtained_at is None:
            return requested
        elapsed = time.monotonic() - push_token_obtained_at
        remain = PUSH_REFUND_WINDOW_SECONDS - PUSH_REFUND_SETSTATUS_RESERVE_SECONDS - elapsed
        if remain <= interval:
            return floor
        return min(requested, max(floor, int(remain // interval)))

    @classmethod
    def _push_token_age_seconds(
        cls,
        push_provider: Optional[str],
        push_token_obtained_at: Optional[float],
        push_task_id: Optional[str] = None,
        push_token: Optional[str] = None,
    ) -> Optional[float]:
        """Push Token 的真实签发年龄（秒）。

        新签发走 `push_token_obtained_at`（本进程 monotonic）；复用令牌必须回库存读
        `created_at`——它在被本任务租到之前可能已经躺了很久，只按租到的时刻算会把老令牌
        当新的用，收码窗口再次被吃掉。
        """
        ages = []
        if push_token_obtained_at is not None:
            ages.append(max(0.0, time.monotonic() - push_token_obtained_at))
        if push_provider == PUSH_REUSE_PROVIDER and (push_task_id or push_token):
            try:
                from backend.app.services.push_token_vault import PushTokenVault

                row = PushTokenVault.get_instance().find_view(
                    reghelp_task_id=push_task_id, token=push_token
                )
                created = row.get("created_at") if row else None
                if created:
                    issued = datetime.datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    if issued.tzinfo is None:
                        issued = issued.replace(tzinfo=datetime.timezone.utc)
                    ages.append(
                        max(0.0, (datetime.datetime.now(datetime.timezone.utc) - issued).total_seconds())
                    )
            except Exception as exc:
                logger.debug("读取复用 Push Token 签发时间失败: %s", exc)
        if not ages:
            return None
        return max(ages)

    @classmethod
    def _push_token_window_exhausted(
        cls,
        push_provider: Optional[str],
        push_token_obtained_at: Optional[float],
        requested_attempts: int = DEFAULT_SMS_POLL_ATTEMPTS,
        token_age_seconds: Optional[float] = None,
    ) -> bool:
        """猎号中的 Push Token 是否已老到会拖累下一轮收码窗口。

        新签发与复用（reghelp_reuse）都要体检：复用令牌的年龄按库存 `created_at` 计算，
        不能因为 `provider != reghelp` 就整枚跳过老化判断。
        """
        if push_provider not in {"reghelp", PUSH_REUSE_PROVIDER}:
            return False
        age = token_age_seconds
        if age is None and push_token_obtained_at is not None:
            age = time.monotonic() - push_token_obtained_at
        if age is not None and age >= HUNT_PUSH_TOKEN_MAX_AGE_SECONDS:
            return True
        if push_provider != "reghelp" or push_token_obtained_at is None:
            return False
        capped = cls._sms_poll_attempts_for_push_window(
            requested_attempts, push_provider, push_token_obtained_at
        )
        return capped < HUNT_MIN_SMS_POLL_ATTEMPTS

    @staticmethod
    def _app_blacklist_expiry_label(record: Any, ttl_hours: float) -> str:
        """临时拉黑到期时间的可读标签；库存未返回记录时退化为相对时长。"""
        expires_at = getattr(record, "expires_at", None)
        if isinstance(expires_at, str) and expires_at:
            return expires_at
        return f"约 {ttl_hours:.0f}h 后"

    @classmethod
    def _release_push_token_leases(cls, task_id: str) -> int:
        """归还本任务持有的全部 Push Token 租约；库存不可用时静默跳过。"""
        try:
            from backend.app.services.push_token_vault import PushTokenVault

            return PushTokenVault.get_instance().release_task_leases(task_id)
        except Exception as exc:
            logger.debug("归还 Push Token 租约失败 (task=%s): %s", task_id, exc)
            return 0

    @classmethod
    async def _refund_push_token(
        cls,
        bypass_svc: Optional[AttestationGatewayService],
        push_task_id: Optional[str],
        push_provider: Optional[str],
        push_token_obtained_at: Optional[float],
        phone: Optional[str],
        task_id: str,
        manager: RegistrationTaskManager,
        reason: str,
        retire: bool = False,
    ) -> None:
        """失败/退订分支尝试触发 REGHelp Push Token `setStatus` 自动退款审计。

        仅当 `push_provider == "reghelp"` 且已持有 `push_task_id` 时才会发起请求。
        Token 签发未满 60s 时先等待再 setStatus；超 180s 仍会尝试（平台可能拒绝）。
        未映射原因与平台拒绝会写进任务日志，绝不导致任务失败。

        `retire=True` 用于猎号主动轮换（换设备指纹 / 换收码窗口）：无论退款是否被平台
        受理，都把该 Token 移出本地复用候选，避免下一轮立刻又把同一枚租回来。

        所有库存写入都带上本任务 `task_id` 作为租约持有者：库存层会拒绝非持有者的
        retire / refund，避免把别的任务正在用的 Token 打掉。
        """
        from backend.app.services.push_token_vault import PushTokenVault, REUSE_PROVIDER

        vault = PushTokenVault.get_instance()

        def mark_unused() -> None:
            fn = vault.mark_retired if retire else vault.mark_failed_keep
            fn(reghelp_task_id=push_task_id, reason=reason, lease_task_id=task_id)

        if push_provider == REUSE_PROVIDER:
            mark_unused()
            return
        if not bypass_svc or not push_task_id or push_provider != "reghelp":
            if push_task_id or push_provider == "reghelp":
                mark_unused()
            return
        if push_token_obtained_at is not None:
            elapsed = time.monotonic() - push_token_obtained_at
            if elapsed < PUSH_REFUND_MIN_SECONDS:
                wait_s = PUSH_REFUND_MIN_SECONDS - elapsed
                skip_wait = os.getenv("EDGENODE_SKIP_PUSH_REFUND_WAIT", "").strip().lower() in {
                    "1", "true", "yes", "on",
                }
                if wait_s > 0.05 and not skip_wait:
                    await manager.append_log(
                        task_id,
                        f"[REGHelp 退款] Token 签发仅 {elapsed:.0f}s，等待 {wait_s:.0f}s "
                        f"至官方 {int(PUSH_REFUND_MIN_SECONDS)}s 窗口后再 setStatus id={push_task_id}"
                    )
                    await asyncio.sleep(wait_s)
                    elapsed = time.monotonic() - push_token_obtained_at
            if elapsed > PUSH_REFUND_WINDOW_SECONDS:
                await manager.append_log(
                    task_id,
                    f"⚠️ [REGHelp 退款] 距 Push Token 签发已 {elapsed:.0f}s，"
                    f"超过官方约 {PUSH_REFUND_WINDOW_SECONDS:.0f}s 窗口，"
                    f"仍尝试 setStatus id={push_task_id}"
                )
        try:
            refund_status = await bypass_svc.refund_push_token(
                push_task_id,
                phone,
                HUNT_REFUND_REASON_ALIASES.get(reason, reason),
                log_callback=lambda msg: manager.append_log(task_id, msg),
            )
            if refund_status:
                vault.mark_refunded(reghelp_task_id=push_task_id, lease_task_id=task_id)
            else:
                mark_unused()
        except Exception as exc:
            logger.warning("REGHelp Push Token 退款回写异常 (id=%s, reason=%s): %s", push_task_id, reason, exc)
            mark_unused()
            await manager.append_log(
                task_id,
                f"⚠️ [REGHelp 退款] setStatus 异常 id={push_task_id} reason={reason}: {exc}"
            )

    @classmethod
    async def _apply_banned_cache_gate(
        cls,
        phone: str,
        act_id: Optional[str],
        sms_svc,
        task_id: str,
        manager: RegistrationTaskManager,
        cache=None,
        soft: bool = False,
    ) -> bool:
        """租号后最先检查本地已确认封禁库。命中则立即退订，不消耗 Push Token。

        返回 True 表示可以继续；False 表示已拦截并退订，调用方必须立即 return。
        `soft=True`（猎号中途）只退订不落终态，避免 filtered/error/banned_cache_hit
        残留到后续轮次成功后的任务快照里。
        """
        service = cache or BannedPhonesCache
        record = service.lookup(phone)
        if not record:
            return True
        # 再次租到同一号时累加命中，便于管理页观察平台复用频率
        if hasattr(service, "touch"):
            touched = service.touch(phone)
            if touched:
                record = touched
        category = getattr(record, "category", "") or "banned"
        await manager.append_log(
            task_id,
            f"[号码黑名单拦截] 通信句柄 {phone} 已在本地黑名单 "
            f"(分类={category}, 原因={record.reason}, 来源={record.source}, 命中={record.hits}次)，"
            "跳过白号预检 / Push Token / auth.sendCode，直接撤销退订换号",
        )
        await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, LOCAL_BANNED_REASON)
        if not soft:
            manager.update_task_status(
                task_id,
                "filtered",
                error=f"{LOCAL_BANNED_REASON}: 号码 {phone} 已被本机确认为 {record.reason}",
                phone=phone,
                banned_cache_hit=True,
                blacklist_category=category,
            )
        return False

    @classmethod
    async def _apply_phone_precheck(
        cls,
        phone: str,
        act_id: Optional[str],
        sms_svc,
        task_id: str,
        manager: RegistrationTaskManager,
        proxy: Optional[Dict[str, Any]] = None,
        precheck_svc=None,
        config=None,
        soft: bool = False,
        hunt: bool = False,
    ) -> bool:
        """租号后、申请 Push Token 之前做白号预检。

        返回 True 表示可以继续注册流水线；False 表示已拦截并退订，调用方必须立即 return。
        `soft=True`（猎号中途）只退订不落终态，避免 filtered/error 残留污染最终结果。
        `hunt=True` 时，探测池整体不可用会额外打一条显式告警（每任务只打一次）。
        """
        service = precheck_svc or PhonePrecheckService
        await manager.append_log(task_id, f"正在对通信句柄 {phone} 执行 Telegram 号码注册状态预检探测...")
        result = await service.check_phone(
            phone,
            proxy=proxy,
            log_callback=lambda msg: manager.append_log(task_id, msg),
            config=config,
        )
        if result.intercept or result.is_registered is True:
            intercept_log = format_precheck_intercept_log(phone, result.user_id)
            await manager.append_log(task_id, intercept_log)
            BannedPhonesCache.remember(
                phone,
                reason=PRECHECK_ALREADY_REGISTERED,
                source=SOURCE_PRECHECK,
                category="already_registered",
                note=f"precheck uid={result.user_id}" if result.user_id else "precheck registered",
            )
            await cls._refund_and_revoke_channel(
                sms_svc, act_id, task_id, manager, PRECHECK_ALREADY_REGISTERED
            )
            if not soft:
                manager.update_task_status(
                    task_id,
                    "filtered",
                    error=f"{PRECHECK_ALREADY_REGISTERED}: 号码 {phone} 已在 Telegram 注册 (uid={result.user_id})",
                    phone=phone,
                    precheck_intercepted=True,
                    precheck_user_id=result.user_id,
                )
            return False
        if result.degraded or result.is_registered is None:
            reason = result.reason or ""
            if reason in {"PRECHECK_NO_PROBE_SESSION", "PRECHECK_DISABLED", ""}:
                degrade_msg = DEGRADE_LOG_TEMPLATE
            else:
                degrade_msg = f"⚠️ 预检未得到明确结论 ({reason})，优雅降级走现有流程"
            await manager.append_log(task_id, degrade_msg)
            status_extra: Dict[str, Any] = {"precheck_intercepted": False}
            # 探测池整体不可用不能静默降级：猎号会误以为有预检兜底，直到把预算烧完才发现
            if hunt and reason in PROBE_POOL_UNUSABLE_REASONS:
                task = manager.get_task(task_id) or {}
                if not task.get("precheck_pool_alerted"):
                    await manager.append_log(
                        task_id,
                        f"⚠️⚠️ [猎号] 白号预检探测池当前整体不可用（{reason}）："
                        "本任务后续换号等同于关闭预检，只能等 auth.sendCode 之后才发现二手号，"
                        "Push Token 与租号成本会明显上升。"
                        "请在「预检探针」页恢复至少一个已授权 session 后再跑大轮次猎号。"
                    )
                    status_extra["precheck_pool_alerted"] = True
                    status_extra["precheck_pool_reason"] = reason
            manager.update_task_status(task_id, "running", **status_extra)
            return True
        await manager.append_log(task_id, CLEAN_LOG_TEMPLATE.format(phone=phone))
        manager.update_task_status(task_id, "running", precheck_intercepted=False)
        return True

    @classmethod
    def _should_enforce_proxy_country(cls, config, *, pinned: bool) -> bool:
        if pinned:
            return False
        if is_strict_alignment(config):
            return True
        return bool(getattr(config, "proxy_require_country_match", True))

    @classmethod
    def _reject_foreign_proxy(
        cls,
        proxy: Optional[Dict[str, Any]],
        target_country: str,
        config,
        *,
        pinned: bool,
    ) -> None:
        """已标注异国的出口不得用于该号国。未标注全球节点放行。"""
        if not cls._should_enforce_proxy_country(config, pinned=pinned):
            return
        from backend.app.services.proxy_manager import proxy_is_labeled_foreign
        from backend.app.services.proxyseller import format_proxy_endpoint

        if proxy_is_labeled_foreign(proxy, target_country):
            endpoint = format_proxy_endpoint(proxy or {})
            label = (
                (proxy or {}).get("assigned_country")
                or (proxy or {}).get("country_code")
                or (proxy or {}).get("egress_country_code")
                or (proxy or {}).get("country")
                or "?"
            )
            raise ProxyCountryMismatchError(
                f"PROXY_COUNTRY_MISMATCH: 出口 {endpoint} 标注为 {label}，"
                f"与号国 {str(target_country or '').upper()} 不一致，拒绝租号/发码"
            )

    @classmethod
    def _flood_gate_policy(cls, config=None) -> Dict[str, Any]:
        """读配置：门闩作用域 / 是否拦新发码 / 是否忽略 published 窗。"""
        cfg = config
        if cfg is None:
            try:
                cfg = ConfigManager.get_instance().config
            except Exception:
                cfg = None
        scope = str(getattr(cfg, "flood_window_scope", None) or "process").strip().lower()
        if scope not in {"process", "task"}:
            scope = "process"
        ignore = bool(getattr(cfg, "ignore_published_flood_window", False))
        # 默认不拦新测试：省钱硬停需显式 flood_block_new_sends=true
        block_new = getattr(cfg, "flood_block_new_sends", False)
        if block_new is None:
            block_new = False
        hold = getattr(cfg, "published_flood_hold_seconds", None)
        try:
            hold_sec = float(hold) if hold is not None else float(DEFAULT_PUBLISHED_FLOOD_PAUSE_SECONDS)
        except (TypeError, ValueError):
            hold_sec = float(DEFAULT_PUBLISHED_FLOOD_PAUSE_SECONDS)
        hold_sec = max(30.0, min(hold_sec, float(HUNT_FLOOD_ABORT_SECONDS)))
        return {
            "scope": scope,
            "ignore_published": ignore,
            "block_new_sends": bool(block_new),
            "published_hold_seconds": hold_sec,
        }

    @classmethod
    async def _respect_flood_window(
        cls,
        task_id: str,
        manager: RegistrationTaskManager,
        config=None,
    ) -> Optional[str]:
        """FLOOD 窗未结束则软等待，或（opt-in）跳过新租号/发码。

        默认：API_ID_PUBLISHED_FLOOD 不拦兄弟/后续任务的租号与 sendCode。
        省钱硬停：flood_block_new_sends=true（可选再配合 process 作用域）。
        普通 FLOOD_WAIT 仍等满窗再发，但不因此 cancel 整批。
        """
        gate = SendCodeFloodWindow.get()
        policy = cls._flood_gate_policy(config)
        reason = gate.reason() or "FLOOD"
        api_id = gate.api_id()
        api_hint = f" api_id={api_id}" if api_id is not None else ""
        reason_u = str(reason).upper()

        # PUBLISHED_FLOOD：默认只是「某号失败」的记录，绝不拦新任务/新租号。
        if reason_u == "API_ID_PUBLISHED_FLOOD":
            if policy["ignore_published"] or policy["scope"] == "task" or not policy["block_new_sends"]:
                if gate.remaining() > 0 or gate._hard:
                    await manager.append_log(
                        task_id,
                        f"[FLOOD窗] 历史 {reason}{api_hint} 仅表示曾有号码失败；"
                        f"默认不拦本任务，继续租号/发码（省钱硬停请开 flood_block_new_sends）。",
                    )
                    gate.clear_published_flood()
                return None
            left = gate.remaining()
            if left <= 0 and not gate.is_hard_stop():
                return None
            await manager.append_log(
                task_id,
                f"⛔ [FLOOD窗] flood_block_new_sends=开：同 published api_id 冷却中"
                f"（{reason}{api_hint}，剩余 {left:.0f}s），跳过本任务租号/发码（省钱硬停）。"
                f"已开跑且已过门闩的任务不会被 cancel。",
            )
            return "HUNT_FLOOD_WINDOW"

        if gate.is_hard_stop():
            # 小时级 FLOOD_WAIT 等硬停：仅 opt-in 时跳过；否则软等有限时间后继续
            left = gate.remaining()
            if policy["block_new_sends"]:
                await manager.append_log(
                    task_id,
                    f"⛔ [FLOOD窗] flood_block_new_sends=开：硬冷却中（{reason}{api_hint}，"
                    f"剩余 {left:.0f}s），跳过本任务租号/发码",
                )
                return "HUNT_FLOOD_WINDOW"
            await manager.append_log(
                task_id,
                f"[FLOOD窗] 硬冷却记录中（{reason}{api_hint}，剩余 {left:.0f}s）；"
                f"默认不取消新任务，短暂退避后继续",
            )
            if left > 0:
                await asyncio.sleep(min(left, 5.0))
            return None

        left = gate.remaining()
        if left > 0:
            await manager.append_log(
                task_id,
                f"[FLOOD窗] 窗口未结束，暂停租号/发码 {left:.0f}s（reason={reason}{api_hint}）",
            )
            await asyncio.sleep(left)
            if gate.is_hard_stop():
                # 软等待结束后若升级为硬停，再走一遍策略
                return await cls._respect_flood_window(task_id, manager, config=config)
        return None

    @classmethod
    def _trip_flood_window(
        cls,
        *,
        reason: str,
        seconds: float = 0.0,
        hard: bool = False,
        api_id: Optional[int] = None,
        config=None,
    ) -> float:
        policy = cls._flood_gate_policy(config)
        hold = None
        if str(reason or "").upper() == "API_ID_PUBLISHED_FLOOD":
            hold = policy["published_hold_seconds"]
            # 默认 / 探测模式：只打点，不设进程硬门闩拦兄弟任务
            if (
                policy["scope"] == "task"
                or policy["ignore_published"]
                or not policy["block_new_sends"]
            ):
                return SendCodeFloodWindow.get().trip(
                    reason=reason,
                    seconds=0.05,
                    hard=False,
                    api_id=api_id,
                    hold_seconds=None,
                )
            if hard and seconds <= 0:
                seconds = hold
        return SendCodeFloodWindow.get().trip(
            reason=reason,
            seconds=seconds,
            hard=hard,
            api_id=api_id,
            hold_seconds=hold if hard else None,
        )

    @classmethod
    async def resolve_active_proxy(
        cls,
        config,
        target_country: str,
        task_id: str,
        manager: RegistrationTaskManager,
        proxy_override: Optional[Dict[str, Any]] = None,
        proxy_id: Optional[str] = None,
        proxy_mode: str = "custom_pool",
        exclude: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """按用户策略解析本次任务实际使用的出口代理（显式 / 自建池 / API / 后备）。

        `exclude` 仅作用于自建池路径：猎号轮换时把当前出口身份排除，避免「换了个寂寞」。
        """
        from backend.app.models.schemas import normalize_proxy_mode
        from backend.app.services.proxy_manager import find_custom_proxy
        from backend.app.services.proxyseller import format_proxy_endpoint

        mode = normalize_proxy_mode(proxy_mode)
        if proxy_id and mode == "custom_pool":
            mode = "explicit"

        if proxy_override:
            from backend.app.services.proxyseller import format_proxy_endpoint

            await manager.append_log(
                task_id,
                f"[代理槽位] 1:1 绑定预分配出口 {format_proxy_endpoint(proxy_override)}"
                f"（同国 {target_country.upper()}，禁止跨区 fallback）",
            )
            return dict(proxy_override)

        # 使用者决定配对关系：explicit 100% 遵从指定节点，不施加隐式国家约束
        active_proxy = None
        user_pinned = False
        if not active_proxy and (proxy_id or mode == "explicit"):
            if proxy_id:
                found = find_custom_proxy(proxy_id=proxy_id)
                if found:
                    active_proxy = found
                    user_pinned = True
                    await manager.append_log(
                        task_id,
                        f"[代理配对] 100% 遵从用户指定节点 {format_proxy_endpoint(found)}，"
                        "不施加隐式国家约束"
                    )
                else:
                    await manager.append_log(
                        task_id,
                        f"[代理配对] 未找到显式指定代理 {proxy_id}，回退自建池轮换"
                    )
                    mode = "custom_pool"
            elif mode == "explicit":
                await manager.append_log(task_id, "[代理配对] 已选显式模式但未提供 proxy_id，回退自建池轮换")
                mode = "custom_pool"

        if not active_proxy and mode in {"custom_pool", "explicit"}:
            active_proxy = await cls._resolve_custom_proxy(
                config=config,
                target_country=target_country,
                task_id=task_id,
                manager=manager,
                exclude=exclude,
            )
        if not active_proxy and mode == "auto":
            active_proxy = await cls._resolve_proxy_seller_auto(
                config=config,
                target_country=target_country,
                task_id=task_id,
                manager=manager,
            )
        if (
            not active_proxy
            and mode == "custom_pool"
            and getattr(config, "use_proxy_seller_auto", False)
        ):
            active_proxy = await cls._resolve_proxy_seller_auto(
                config=config,
                target_country=target_country,
                task_id=task_id,
                manager=manager,
            )

        if not active_proxy:
            fallback = getattr(config, "fallback_proxy", None)
            if hasattr(fallback, "model_dump"):
                active_proxy = fallback.model_dump()
            elif isinstance(fallback, dict):
                active_proxy = dict(fallback)
            else:
                active_proxy = {
                    "proxy_type": "socks5",
                    "addr": "127.0.0.1",
                    "port": 10808,
                }
            await manager.append_log(
                task_id,
                f"[多径中继网关] 使用静态后备中继 {active_proxy.get('proxy_type', 'socks5')}://"
                f"{active_proxy.get('addr')}:{active_proxy.get('port')}"
                + (f"（策略={mode}）" if mode == "fallback" else "")
            )
        cls._reject_foreign_proxy(
            active_proxy, target_country, config, pinned=user_pinned,
        )
        return active_proxy

    @classmethod
    async def _rotate_hunt_proxy(
        cls,
        config,
        target_country: str,
        task_id: str,
        manager: RegistrationTaskManager,
        current_proxy: Optional[Dict[str, Any]],
        proxy_mode: str,
        reason: str,
        proxy_override: Optional[Dict[str, Any]] = None,
        proxy_id: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """猎号轮换出口，返回 (生效代理, 是否真的换掉了)。

        代理是 1:1 预分配（批量槽位 proxy_override）或用户显式指定（proxy_id）时不存在
        「池内换一个」的语义，直接如实记录不轮换，绝不打出假的「已轮换」日志。
        换出来的节点身份与原节点相同（池里只有一个候选）时同样返回 False。
        """
        from backend.app.services.proxyseller import format_proxy_endpoint, proxy_identity

        current_identity = proxy_identity(current_proxy) if current_proxy else None
        if proxy_override or proxy_id:
            await manager.append_log(
                task_id,
                f"[猎号] {reason}：当前为"
                + ("批量槽位 1:1 绑定" if proxy_override else "用户显式指定")
                + f"出口 {format_proxy_endpoint(current_proxy or {})}，本模式不轮换代理"
            )
            return (current_proxy or {}), False

        rotated_proxy = await cls.resolve_active_proxy(
            config=config,
            target_country=target_country,
            task_id=task_id,
            manager=manager,
            proxy_override=None,
            proxy_id=None,
            proxy_mode=proxy_mode,
            exclude=[current_identity] if current_identity else None,
        )
        new_identity = proxy_identity(rotated_proxy) if rotated_proxy else None
        if not rotated_proxy or (current_identity and new_identity == current_identity):
            await manager.append_log(
                task_id,
                f"⚠️ [猎号] {reason}：可用注册代理池内没有其它候选节点，出口仍为 "
                f"{format_proxy_endpoint(current_proxy or {})}（未轮换）"
            )
            return (rotated_proxy or current_proxy or {}), False
        await manager.append_log(
            task_id,
            f"[猎号] {reason}：出口已轮换 {format_proxy_endpoint(current_proxy or {})} → "
            f"{format_proxy_endpoint(rotated_proxy)}"
        )
        return rotated_proxy, True

    @classmethod
    async def _resolve_custom_proxy(
        cls,
        config,
        target_country: str,
        task_id: str,
        manager: RegistrationTaskManager,
        exclude: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """优先从用户自建代理池按目标国家匹配节点。

        `exclude` 传入当前出口身份（proxy_identity），猎号轮换时用于真正换节点；
        池内无其它候选时仍返回原节点，由调用方按身份比对如实记录「未换成」。
        """
        from backend.app.services.proxy_manager import custom_pool_summary, select_proxy_for_registration
        from backend.app.services.proxyseller import format_proxy_endpoint

        if not getattr(config, "custom_proxies", None):
            return None
        summary = custom_pool_summary(target_country)
        if not summary.get("total"):
            return None
        chosen = select_proxy_for_registration(target_country, exclude=exclude)
        if not chosen:
            await manager.append_log(
                task_id,
                f"[自建代理池] 已载入 {summary['total']} 条自定义代理，"
                f"但没有可用于注册的节点（角色 registration/all，绑定 {target_country.upper()} 或全球通用）"
                + (f"；池内区域: {', '.join(summary.get('countries') or [])}" if summary.get("countries") else "")
            )
            return None
        await manager.append_log(
            task_id,
            f"[自建代理池] 成功匹配 {target_country.upper()} 注册通道: {format_proxy_endpoint(chosen)}"
            + f" 角色={chosen.get('role') or 'all'}"
            + (f" 绑定={chosen.get('assigned_country')}" if chosen.get("assigned_country") else " 绑定=全球")
            + (f" 延迟={chosen.get('latency_ms')}ms" if chosen.get("latency_ms") is not None else "")
        )
        if chosen.get("egress_ip") or chosen.get("egress_country") or chosen.get("country"):
            await manager.append_log(
                task_id,
                f"[自建代理池] 出口拓扑: IP={chosen.get('egress_ip') or '-'} "
                f"国家={chosen.get('egress_country') or chosen.get('country') or target_country.upper()} "
                f"城市={chosen.get('city') or '-'}"
            )
        return chosen

    @classmethod
    async def _resolve_proxy_seller_auto(
        cls,
        config,
        target_country: str,
        task_id: str,
        manager: RegistrationTaskManager,
    ) -> Optional[Dict[str, Any]]:
        """当 use_proxy_seller_auto 开启时，按目标国家自动匹配并测活 Proxy-Seller 区域代理。

        选中的动态代理会同时注入 MTProto 通道、Attestation 网关以及 RecaptchaMobile 解题通道，
        以保证手机号国家、语言、时区与出口 IP 拓扑对齐。
        """
        from backend.app.services.proxyseller import (
            ProxySellerService,
            format_proxy_endpoint,
            is_custom_proxy,
            is_resident_tg,
            is_static_residential,
        )

        await manager.append_log(
            task_id,
            f"[多径中继网关] 正在检索 {target_country.upper()} 区域代理"
            "（自建池 + xxxtg 住宅列表 + API + 内置静态住宅池）..."
        )
        ps_svc = ProxySellerService(config.proxy_seller_key)
        try:
            ensure_fn = getattr(ps_svc, "ensure_tg_resident_list", None)
            if target_country and callable(ensure_fn):
                try:
                    ensured = await ensure_fn(target_country, create=True)
                    title = ensured.get("title")
                    created = bool(ensured.get("created"))
                    n_nodes = len(ensured.get("proxies") or [])
                    if created and title:
                        await manager.append_log(
                            task_id,
                            f"[多径中继网关] 已自主创建 {title}（{n_nodes} 个节点）",
                        )
                    elif n_nodes and title:
                        await manager.append_log(
                            task_id,
                            f"[多径中继网关] 复用已有 {title}（{n_nodes} 个节点）",
                        )
                    invalidate = getattr(ps_svc, "invalidate_cache", None)
                    if callable(invalidate) and (created or n_nodes):
                        invalidate()
                except Exception as exc:
                    await manager.append_log(
                        task_id,
                        f"[多径中继网关] 自主拉取 _tg 列表未成功 ({exc})，继续使用现有池",
                    )
            regional = await ps_svc.get_proxy_list(country=target_country, refresh=True)
            if regional:
                selection = await ps_svc.select_best_proxy(
                    target_country=target_country,
                    probe=True,
                    allow_fallback=False,
                    refresh=False,
                    max_probes=min(3, len(regional)),
                )
                chosen = selection.get("proxy")
                if chosen:
                    endpoint = format_proxy_endpoint(chosen)
                    if is_custom_proxy(chosen):
                        origin = "用户自建代理池"
                    elif is_resident_tg(chosen):
                        origin = "xxxtg 专用住宅列表"
                    elif is_static_residential(chosen):
                        origin = "内置静态住宅代理池"
                    else:
                        origin = "Proxy-Seller API"
                    await manager.append_log(
                        task_id,
                        f"[多径中继网关] 成功从 {origin} 自动匹配到 {target_country.upper()} "
                        f"区域代理: {endpoint}"
                    )
                    if chosen.get("egress_ip") or chosen.get("egress_country"):
                        await manager.append_log(
                            task_id,
                            f"[多径中继网关] 出口拓扑对齐: IP={chosen.get('egress_ip') or '-'} "
                            f"国家={chosen.get('egress_country') or chosen.get('country') or target_country.upper()} "
                            f"(手机号区域/语言/时区将按 {target_country.upper()} 对齐)"
                        )
                    return chosen
                await manager.append_log(
                    task_id,
                    f"[多径中继网关] 已检索到 {len(regional)} 个 {target_country.upper()} "
                    f"节点但未能完成测活选择，回退至列表首个节点"
                )
                first = regional[0]
                if is_custom_proxy(first):
                    origin = "用户自建代理池"
                elif is_resident_tg(first):
                    origin = "xxxtg 专用住宅列表"
                elif is_static_residential(first):
                    origin = "内置静态住宅代理池"
                else:
                    origin = "Proxy-Seller API"
                await manager.append_log(
                    task_id,
                    f"[多径中继网关] 成功从 {origin} 自动匹配到 {target_country.upper()} "
                    f"区域代理: {format_proxy_endpoint(first)}"
                )
                return first

            await manager.append_log(
                task_id,
                f"[多径中继网关] ⚠️ 目标区域 {target_country.upper()} 暂无可用区域代理"
                "（API / xxxtg 住宅列表 / 静态住宅 / 自建池均无匹配节点）。"
                "已禁止跨大区隐式兜底（不会把智利/印度等互不相干节点分配给本任务），"
                "将优雅降级至配置的 fallback_proxy。"
            )
            return None
        except Exception as exc:
            await manager.append_log(
                task_id,
                f"[多径中继网关] 动态分配未成功 ({exc})，回退至静态后备中继"
            )
            return None
        finally:
            await ps_svc.close()

    @classmethod
    async def _connect_mtproto(
        cls,
        client: TelegramClient,
        task_id: str,
        manager: RegistrationTaskManager,
        sms_svc,
        act_id: Optional[str],
        timeout: float = CONNECT_TIMEOUT_SECONDS,
        mark_failed: bool = True,
    ) -> bool:
        """带超时的 Telethon connect；超时则退号，避免任务挂起。

        单次任务默认把任务标 failed。猎号应传 mark_failed=False，由调用方换出口继续。
        """
        try:
            await asyncio.wait_for(client.connect(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            err = (
                f"MTProto 连接超时 (CONNECT_TIMEOUT {timeout:.0f}s)，"
                "已中止以防任务挂起"
            )
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(
                sms_svc, act_id, task_id, manager, "CONNECT_TIMEOUT"
            )
            if mark_failed:
                manager.update_task_status(task_id, "failed", error=err)
            return False

    @classmethod
    async def _release_registration_resources(
        cls,
        client: Optional[TelegramClient],
        sms_svc,
        bypass_svc: Optional[AttestationGatewayService],
    ) -> None:
        """三个独立 try/except，disconnect 失败也绝不阻断 httpx close。"""
        if client is not None:
            try:
                if getattr(client, "is_connected", lambda: False)():
                    await client.disconnect()
            except Exception as exc:
                logger.warning("释放 Telethon 客户端失败（已忽略，继续关闭 HTTP 资源）: %s", exc)
        if sms_svc is not None:
            try:
                await sms_svc.close()
            except Exception as exc:
                logger.warning("释放接码客户端失败: %s", exc)
        if bypass_svc is not None:
            try:
                await bypass_svc.close()
            except Exception as exc:
                logger.warning("释放 Attestation 网关客户端失败: %s", exc)


    @classmethod
    async def _disconnect_client_quiet(cls, client: Optional[TelegramClient]) -> None:
        if client is None:
            return
        try:
            if getattr(client, "is_connected", lambda: False)():
                await client.disconnect()
        except Exception as exc:
            logger.warning("断开 Telethon 客户端失败（已忽略）: %s", exc)

    @classmethod
    def _discard_incomplete_session(
        cls,
        session_path: Optional[Path] = None,
        meta_path: Optional[Path] = None,
    ) -> None:
        """换号重试时清理未完成注册的 session，避免脏文件占用手机号命名。"""
        candidates = []
        if session_path:
            sp = Path(session_path)
            candidates.extend([sp, Path(str(sp) + "-journal")])
        if meta_path:
            candidates.append(Path(meta_path))
        for path_obj in candidates:
            try:
                if path_obj.exists():
                    path_obj.unlink()
            except Exception as exc:
                logger.warning("清理未完成 session 失败 (%s): %s", path_obj, exc)

    @staticmethod
    def _should_set_2fa(config, set_2fa: Optional[bool]) -> bool:
        if set_2fa is not None:
            return bool(set_2fa)
        return bool(getattr(config, "auto_set_2fa", False))

    @staticmethod
    def _edit_2fa_kwargs(new_password: str, current_password: Optional[str] = None) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"new_password": new_password}
        if current_password:
            kwargs["current_password"] = current_password
        return kwargs

    @staticmethod
    def _build_code_settings(
        push_token: Optional[str] = None,
        *,
        allow_app_hash: bool = True,
        attach_push_token: bool = True,
        allow_firebase: bool = False,
        unknown_number: bool = False,
        allow_flashcall: bool = False,
        allow_missed_call: bool = False,
    ) -> types.CodeSettings:
        """构造 auth.sendCode 的 CodeSettings。

        Telethon CodeSettings._bytes 要求 token 与 app_sandbox 同真或同假：
        有 Push Token 时必须显式传入 app_sandbox（非沙盒为 False）；
        无 Token 时两者都必须保持 False-y（None）。
        token 传 str 即可，Telethon 会走 serialize_bytes。

        SMS 优先策略靠 attach_push_token=False 生效。
        token/app_sandbox：官方文档标为 iOS Firebase/APNS 槽；本仓历史兼容路径是把
        Android FCM 塞进去（错槽），不是 APNS，也不是在跑 iOS 客户端。
        allow_app_hash 只协商短信正文里的 Android SMS Retriever hash，应跟随设备平台
        而非投递模式。
        allow_firebase / unknown_number 由配置注入，对应官方 Android 的 Firebase
        通道协商与「号码非本机 SIM」标志。
        """
        token = push_token if (attach_push_token and push_token) else None
        return types.CodeSettings(
            allow_flashcall=bool(allow_flashcall) or None,
            current_number=False,
            allow_app_hash=allow_app_hash,
            allow_missed_call=bool(allow_missed_call) or None,
            allow_firebase=bool(allow_firebase) or None,
            unknown_number=bool(unknown_number) or None,
            token=token,
            app_sandbox=False if token else None,
        )

    @staticmethod
    def _api_hash_for_log(profile: Dict[str, Any]) -> str:
        raw = str(profile.get("api_hash") or "").strip()
        if not raw:
            return "-"
        if len(raw) <= 12:
            return raw
        return f"{raw[:8]}…{raw[-4:]}"

    @classmethod
    def _log_push_token_slot(
        cls,
        push_token: Optional[str],
        plan,
    ) -> str:
        attached = bool(getattr(plan, "attach_push_token", False) and push_token)
        info = classify_push_token(push_token)
        slot = describe_push_slot(attached)
        return (
            f"push_slot={slot} token_kind={info['kind']} "
            f"token_len={info['length']} suspicious={'是' if info['suspicious'] else '否'}"
        )

    @classmethod
    async def _append_send_code_credential_log(
        cls,
        task_id: str,
        manager: RegistrationTaskManager,
        profile: Dict[str, Any],
        push_token: Optional[str],
        plan,
        code_settings,
    ) -> None:
        await manager.append_log(
            task_id,
            "sendCode 凭证核对: "
            f"api_id={profile.get('api_id')} "
            f"api_hash={cls._api_hash_for_log(profile)} "
            f"attach_token={'是' if (plan.attach_push_token and push_token) else '否'} "
            f"push_token={'有' if push_token else '无'} "
            f"code_settings.token={'有' if getattr(code_settings, 'token', None) else '无'} "
            f"firebase={'是' if getattr(code_settings, 'allow_firebase', None) else '否'} "
            f"unknown={'是' if getattr(code_settings, 'unknown_number', None) else '否'} "
            f"flashcall={'是' if getattr(code_settings, 'allow_flashcall', None) else '否'} "
            f"missed={'是' if getattr(code_settings, 'allow_missed_call', None) else '否'} "
            f"{cls._log_push_token_slot(push_token, plan)}"
        )

    @classmethod
    def _build_code_settings_from_plan(
        cls,
        push_token: Optional[str],
        plan,
    ) -> types.CodeSettings:
        return cls._build_code_settings(
            push_token,
            allow_app_hash=plan.allow_app_hash,
            attach_push_token=plan.attach_push_token,
            allow_firebase=bool(getattr(plan, "allow_firebase", False)),
            unknown_number=bool(getattr(plan, "unknown_number", False)),
            allow_flashcall=bool(getattr(plan, "allow_flashcall", False)),
            allow_missed_call=bool(getattr(plan, "allow_missed_call", False)),
        )

    @staticmethod
    async def _log_code_delivery_plan(task_id: str, manager: RegistrationTaskManager, plan) -> None:
        await manager.append_log(task_id, f"[验证码通道] {plan.summary_for_log()}")
        for note in plan.notes:
            await manager.append_log(task_id, f"[验证码通道] {note}")

    @staticmethod
    def _tl_type_name(obj: Any) -> str:
        if obj is None:
            return ""
        return type(obj).__name__

    @classmethod
    def _sent_code_type_name(cls, sent_code: Any) -> str:
        """完整 constructor / inner type 名。

        ``auth.SentCodePaymentRequired`` / ``auth.SentCodeSuccess`` 是独立 constructor，
        没有 ``.type``；普通 ``auth.SentCode`` 用 ``.type``。
        """
        if sent_code is None:
            return "Unknown"
        obj_name = cls._tl_type_name(sent_code)
        if obj_name in PAYMENT_REQUIRED_TYPE_NAMES or obj_name == "SentCodeSuccess":
            return obj_name
        inner = cls._tl_type_name(getattr(sent_code, "type", None))
        return inner or obj_name or "Unknown"

    @classmethod
    def _is_payment_required(cls, sent_code: Any) -> bool:
        return cls._sent_code_type_name(sent_code) in PAYMENT_REQUIRED_TYPE_NAMES

    @classmethod
    def _is_email_setup(cls, sent_code: Any) -> bool:
        return cls._sent_code_type_name(sent_code) in EMAIL_SETUP_TYPE_NAMES

    @classmethod
    def _is_email_code(cls, sent_code: Any) -> bool:
        return cls._sent_code_type_name(sent_code) in EMAIL_CODE_TYPE_NAMES

    @classmethod
    def _is_firebase_sms(cls, sent_code: Any) -> bool:
        return cls._sent_code_type_name(sent_code) in FIREBASE_SMS_TYPE_NAMES

    @classmethod
    def _is_app_delivery(cls, sent_code: Any) -> bool:
        return cls._sent_code_type_name(sent_code) in APP_DELIVERY_TYPE_NAMES

    @classmethod
    def _is_sms_delivery(cls, sent_code: Any) -> bool:
        name = cls._sent_code_type_name(sent_code)
        if name in SMS_DELIVERY_TYPE_NAMES:
            return True
        return bool(name) and "Sms" in name and "App" not in name and "Firebase" not in name

    @classmethod
    def _next_type_is_sms(cls, sent_code: Any) -> bool:
        name = cls._tl_type_name(getattr(sent_code, "next_type", None))
        if not name:
            return False
        return name in SMS_NEXT_TYPE_NAMES or "Sms" in name

    @classmethod
    def _describe_sent_code(cls, sent_code: Any) -> str:
        type_name = cls._sent_code_type_name(sent_code) or "Unknown"
        next_name = cls._tl_type_name(getattr(sent_code, "next_type", None)) or "None"
        timeout = getattr(sent_code, "timeout", None)
        timeout_text = str(timeout) if timeout is not None else "None"
        extras: List[str] = []
        if type_name in PAYMENT_REQUIRED_TYPE_NAMES:
            extras.append(f"store_product={getattr(sent_code, 'store_product', None)}")
            extras.append(f"currency={getattr(sent_code, 'currency', None)}")
            extras.append(f"amount={getattr(sent_code, 'amount', None)}")
        code_type = getattr(sent_code, "type", None)
        pattern = getattr(code_type, "email_pattern", None) if code_type is not None else None
        if pattern:
            extras.append(f"email_pattern={pattern}")
        if code_type is not None and (
            getattr(code_type, "play_integrity_nonce", None) is not None
            or getattr(code_type, "nonce", None) is not None
        ):
            extras.append("play_integrity_nonce=present")
        extra_text = (" " + " ".join(extras)) if extras else ""
        return f"type={type_name} next_type={next_name} timeout={timeout_text}{extra_text}"

    @classmethod
    async def _maybe_resend_to_sms(
        cls,
        client,
        phone: str,
        sent_code: Any,
        task_id: str,
        manager: RegistrationTaskManager,
        wait_timeout: Optional[float] = None,
    ) -> Tuple[Optional[Any], Optional[Exception]]:
        """等待 next_type 冷却窗口后调用 auth.resendCode，尝试强制切换到短信通道。"""
        timeout = wait_timeout if wait_timeout is not None else getattr(sent_code, "timeout", None)
        wait_secs = 0.0
        if timeout is not None:
            try:
                wait_secs = min(max(float(timeout), 0.0), MAX_RESEND_WAIT_SECONDS)
            except (TypeError, ValueError):
                wait_secs = 0.0

        next_name = cls._tl_type_name(getattr(sent_code, "next_type", None)) or "None"
        if wait_secs > 0:
            await manager.append_log(
                task_id,
                f"检测到 next_type={next_name}，将等待服务端冷却窗口 {wait_secs:.0f}s "
                "后调用 auth.resendCode 强制切换短信通道..."
            )
            await asyncio.sleep(wait_secs)
        else:
            await manager.append_log(
                task_id,
                f"next_type={next_name} 且无有效 timeout，立即尝试 auth.resendCode 探测短信通道..."
            )

        phone_code_hash = getattr(sent_code, "phone_code_hash", None)
        try:
            resent = await client(functions.auth.ResendCodeRequest(
                phone_number=phone,
                phone_code_hash=phone_code_hash,
            ))
        except Exception as exc:
            await manager.append_log(
                task_id,
                f"⚠️ auth.resendCode 探测失败: {exc}。"
                "站内信通道无法被带外短信网关接收，将快速退订换号。"
            )
            return None, exc

        new_type = cls._tl_type_name(getattr(resent, "type", None)) or "Unknown"
        await manager.append_log(
            task_id,
            f"auth.resendCode 已返回，新分发通道类型: {new_type} ({cls._describe_sent_code(resent)})"
        )
        return resent, None

    @classmethod
    def _play_integrity_nonce(cls, code_type: Any) -> Optional[str]:
        raw = getattr(code_type, "play_integrity_nonce", None)
        if raw is None:
            raw = getattr(code_type, "nonce", None)
        if raw is None:
            return None
        if isinstance(raw, str):
            return raw
        if isinstance(raw, (bytes, bytearray)):
            return base64.urlsafe_b64encode(bytes(raw)).decode("ascii")
        return None

    @classmethod
    def _app_version_code(cls, profile: Optional[Dict[str, Any]]) -> int:
        profile = profile or {}
        for key in ("app_build", "app_version_code"):
            val = profile.get(key)
            if val is None:
                continue
            digits = "".join(ch for ch in str(val) if ch.isdigit())
            if digits:
                try:
                    return int(digits)
                except ValueError:
                    continue
        return 0

    @classmethod
    async def _complete_setup_email(
        cls,
        client,
        phone: str,
        sent_code: Any,
        task_id: str,
        manager: RegistrationTaskManager,
        bypass_svc,
        profile: Optional[Dict[str, Any]],
        emulation_label: str,
    ) -> Any:
        """官方流程：SetUpEmailRequired → REGHelp 临时邮箱 → verifyEmail → 新 sent_code。"""
        if bypass_svc is None or not hasattr(bypass_svc, "get_login_email"):
            raise SentCodeAppDeliveryError(
                "SentCodeTypeSetUpEmailRequired：无 REGHelp Email 客户端，无法完成登录邮箱绑定",
                reason="EMAIL_SETUP_FAILED",
            )
        profile = profile or {}
        phone_code_hash = getattr(sent_code, "phone_code_hash", None)
        if not phone_code_hash:
            raise SentCodeAppDeliveryError(
                "SentCodeTypeSetUpEmailRequired 缺少 phone_code_hash",
                reason="EMAIL_SETUP_FAILED",
            )
        await manager.append_log(
            task_id,
            f"[{emulation_label}] 官方流程 SetUpEmailRequired：account.sendVerifyEmailCode "
            f"(purpose=EmailVerifyPurposeLoginSetup) + REGHelp /email/getEmail",
        )
        markers = str(profile.get("app_device") or "").lower()
        preferred = "icloud" if "ios" in markers else "gmail"
        types_to_try = [preferred] + [item for item in ("gmail", "icloud") if item != preferred]
        inbox = None
        last_err: Optional[Exception] = None
        for email_type in types_to_try:
            try:
                inbox = await bypass_svc.get_login_email(
                    profile,
                    phone,
                    email_type=email_type,
                    log_callback=lambda msg: manager.append_log(task_id, msg),
                    ref=task_id,
                )
                if inbox and getattr(inbox, "email", None):
                    break
            except Exception as exc:
                last_err = exc
                await manager.append_log(task_id, f"⚠️ REGHelp Email type={email_type} 失败: {exc}")
                inbox = None
        if not inbox or not getattr(inbox, "email", None):
            raise SentCodeAppDeliveryError(
                f"REGHelp 未能提供临时邮箱: {last_err}",
                reason="EMAIL_SETUP_FAILED",
            )
        await manager.append_log(task_id, f"[{emulation_label}] 临时邮箱已就绪: {inbox.email}")
        purpose = types.EmailVerifyPurposeLoginSetup(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
        )
        try:
            await client(functions.account.SendVerifyEmailCodeRequest(
                purpose=purpose,
                email=inbox.email,
            ))
        except Exception as exc:
            raise SentCodeAppDeliveryError(
                f"account.sendVerifyEmailCode 失败: {exc}",
                reason="EMAIL_SETUP_FAILED",
            ) from exc

        code = getattr(inbox, "code", None)
        if not code:
            try:
                code = await bypass_svc.poll_email_code(
                    inbox.task_id,
                    log_callback=lambda msg: manager.append_log(task_id, msg),
                )
            except Exception as exc:
                raise SentCodeAppDeliveryError(
                    f"REGHelp Email 验证码超时/失败: {exc}",
                    reason="EMAIL_SETUP_FAILED",
                ) from exc
        if not code:
            raise SentCodeAppDeliveryError(
                "REGHelp Email 未返回验证码",
                reason="EMAIL_SETUP_FAILED",
            )
        await manager.append_log(
            task_id,
            f"[{emulation_label}] 已取得 Email 验证码，调用 account.verifyEmail",
        )
        try:
            verified = await client(functions.account.VerifyEmailRequest(
                purpose=purpose,
                verification=types.EmailVerificationCode(code=str(code)),
            ))
        except Exception as exc:
            raise SentCodeAppDeliveryError(
                f"account.verifyEmail 失败: {exc}",
                reason="EMAIL_SETUP_FAILED",
            ) from exc
        next_sent = getattr(verified, "sent_code", None)
        if next_sent is None:
            raise SentCodeAppDeliveryError(
                f"account.verifyEmail 返回 {cls._tl_type_name(verified)} 但没有 sent_code，无法继续登录",
                reason="EMAIL_SETUP_FAILED",
            )
        await manager.append_log(
            task_id,
            f"[{emulation_label}] EmailVerifiedLogin 完成，继续处理新 sent_code "
            f"{cls._sent_code_type_name(next_sent)} ({cls._describe_sent_code(next_sent)})",
        )
        return next_sent

    @classmethod
    async def _complete_firebase_sms(
        cls,
        client,
        phone: str,
        sent_code: Any,
        task_id: str,
        manager: RegistrationTaskManager,
        bypass_svc,
        profile: Optional[Dict[str, Any]],
        emulation_label: str,
    ) -> None:
        """官方流程：FirebaseSms → Play Integrity → auth.requestFirebaseSms。"""
        code_type = getattr(sent_code, "type", None)
        nonce = cls._play_integrity_nonce(code_type)
        version_code = cls._app_version_code(profile)
        if bypass_svc is None or not hasattr(bypass_svc, "get_integrity_token") or not nonce or not version_code:
            await manager.append_log(
                task_id,
                f"[{emulation_label}] SentCodeTypeFirebaseSms：缺少 Integrity 前置条件 "
                f"(svc={'有' if bypass_svc else '无'} nonce={'有' if nonce else '无'} "
                f"versionCode={version_code})，跳过 requestFirebaseSms，按短信通道继续",
            )
            return
        await manager.append_log(
            task_id,
            f"[{emulation_label}] 官方流程 FirebaseSms：REGHelp integrity/getToken "
            f"+ auth.requestFirebaseSms (versionCode={version_code})",
        )
        token = None
        injected = take_injected_device_secret(profile)
        if injected:
            await manager.append_log(
                task_id,
                f"[{emulation_label}] 尝试注入 vault device_secret 到 requestFirebaseSms "
                f"(len={len(injected)}；nonce 很可能不匹配，此路径默认应关闭)",
            )
            token = injected
        try:
            if not token:
                token = await bypass_svc.get_integrity_token(
                    profile or {},
                    nonce=nonce,
                    app_version_code=version_code,
                    log_callback=lambda msg: manager.append_log(task_id, msg),
                    ref=task_id,
                )
        except Exception as exc:
            await manager.append_log(task_id, f"⚠️ Play Integrity 失败，仍尝试短信通道: {exc}")
            return
        if not token:
            await manager.append_log(task_id, "⚠️ Play Integrity 未返回 token，仍尝试短信通道")
            return
        try:
            ok = await client(functions.auth.RequestFirebaseSmsRequest(
                phone_number=phone,
                phone_code_hash=getattr(sent_code, "phone_code_hash", None),
                play_integrity_token=token,
            ))
            await manager.append_log(
                task_id,
                f"[{emulation_label}] auth.requestFirebaseSms 返回 {ok}，继续轮询短信",
            )
        except Exception as exc:
            await manager.append_log(
                task_id,
                f"⚠️ auth.requestFirebaseSms 失败: {exc}，仍尝试短信通道",
            )

    @classmethod
    async def resolve_sent_code_channel(
        cls,
        client,
        phone: str,
        sent_code: Any,
        task_id: str,
        manager: RegistrationTaskManager,
        wait_timeout: Optional[float] = None,
        *,
        bypass_svc=None,
        profile: Optional[Dict[str, Any]] = None,
        emulation_label: str = "balanced",
        _email_depth: int = 0,
    ) -> Tuple[Any, int]:
        """解析 sendCode 分发通道。

        - SentCodeTypeApp：尝试 ResendCode 降级到短信，失败则快退
        - SetUpEmailRequired：REGHelp Email + account.verifyEmail 后继续
        - EmailCode：无对应邮箱时快退，不空等 SMS
        - PaymentRequired：标记需官方 App 内购，快退
        - FirebaseSms：Play Integrity + requestFirebaseSms 后按短信轮询
        """
        delivery_name = cls._sent_code_type_name(sent_code)
        await manager.append_log(
            task_id,
            f"挑战已由服务端下发! 分发通道类型: {delivery_name} "
            f"({cls._describe_sent_code(sent_code)}) [模式={emulation_label}]"
        )

        if cls._is_payment_required(sent_code):
            product = getattr(sent_code, "store_product", None)
            await manager.append_log(
                task_id,
                f"[{emulation_label}] PaymentRequired：需官方 App 内购 "
                f"(store_product={product})，自动化不可完成，快退以免空等 SMS"
            )
            raise SentCodeAppDeliveryError(
                f"auth.SentCodePaymentRequired store_product={product}，需官方 App 内购",
                reason="PAYMENT_REQUIRED_OFFICIAL_ONLY",
            )

        if cls._is_email_setup(sent_code):
            if _email_depth >= 2:
                raise SentCodeAppDeliveryError(
                    "SetUpEmailRequired 嵌套超过上限",
                    reason="EMAIL_SETUP_FAILED",
                )
            next_sent = await cls._complete_setup_email(
                client=client,
                phone=phone,
                sent_code=sent_code,
                task_id=task_id,
                manager=manager,
                bypass_svc=bypass_svc,
                profile=profile,
                emulation_label=emulation_label,
            )
            return await cls.resolve_sent_code_channel(
                client,
                phone,
                next_sent,
                task_id,
                manager,
                wait_timeout=wait_timeout,
                bypass_svc=bypass_svc,
                profile=profile,
                emulation_label=emulation_label,
                _email_depth=_email_depth + 1,
            )

        if cls._is_email_code(sent_code):
            pattern = getattr(getattr(sent_code, "type", None), "email_pattern", None)
            await manager.append_log(
                task_id,
                f"[{emulation_label}] SentCodeTypeEmailCode email_pattern={pattern}："
                "当前自动化不持有该邮箱，快退不空等 SMS",
            )
            raise SentCodeAppDeliveryError(
                f"SentCodeTypeEmailCode email_pattern={pattern}，无法接收该邮箱验证码",
                reason="EMAIL_CODE_UNAVAILABLE",
            )

        if cls._is_firebase_sms(sent_code):
            await cls._complete_firebase_sms(
                client=client,
                phone=phone,
                sent_code=sent_code,
                task_id=task_id,
                manager=manager,
                bypass_svc=bypass_svc,
                profile=profile,
                emulation_label=emulation_label,
            )
            await manager.append_log(task_id, "分发通道为 Firebase/运营商短信，带外遥测网关可正常接收")
            return sent_code, DEFAULT_SMS_POLL_ATTEMPTS

        if not cls._is_app_delivery(sent_code):
            if cls._is_sms_delivery(sent_code):
                await manager.append_log(task_id, "分发通道为运营商短信，带外遥测网关可正常接收")
            else:
                await manager.append_log(
                    task_id,
                    f"[{emulation_label}] 非 App 通道 {delivery_name}，不按站内信快退，"
                    "按默认窗口轮询（Call/其它类型可能收不到带外短信）",
                )
            return sent_code, DEFAULT_SMS_POLL_ATTEMPTS

        await manager.append_log(
            task_id,
            "⚠️ 服务端将验证码下发到了已有设备客户端 (SentCodeTypeApp)，带外短信通道大概率无法接收"
        )

        next_name = cls._tl_type_name(getattr(sent_code, "next_type", None))
        has_timeout = getattr(sent_code, "timeout", None) is not None
        config = ConfigManager.get_instance().config
        fast_drop = bool(getattr(config, "app_delivery_fast_drop", True)) or is_strict_alignment(config)
        if not next_name and fast_drop:
            await manager.append_log(
                task_id,
                "[Expert] SentCodeTypeApp 且 next_type=None：号码在 Telegram 侧仍挂着已授权会话，"
                "OTP 进旧客户端。快丢号，不空等 120 秒。"
            )
            raise SentCodeAppDeliveryError(
                "服务端仅通过站内信下发验证码且未提供 next_type/SMS 降级窗口，"
                "带外短信网关无法接收，已快速退订换号以免空等 120 秒",
                reason="SENT_CODE_TYPE_APP",
            )

        can_probe_resend = bool(next_name) or has_timeout or wait_timeout is not None
        if not can_probe_resend:
            raise SentCodeAppDeliveryError(
                "服务端仅通过站内信下发验证码且未提供 next_type/SMS 降级窗口，"
                "带外短信网关无法接收，已快速退订换号以免空等 120 秒",
                reason="SENT_CODE_TYPE_APP",
            )

        if next_name and not cls._next_type_is_sms(sent_code):
            await manager.append_log(
                task_id,
                f"⚠️ next_type={next_name} 不是短信通道，重发后带外网关仍可能收不到码"
            )

        resent, resend_err = await cls._maybe_resend_to_sms(
            client=client,
            phone=phone,
            sent_code=sent_code,
            task_id=task_id,
            manager=manager,
            wait_timeout=wait_timeout,
        )
        if resent is None:
            raise SentCodeAppDeliveryError(
                f"SentCodeTypeApp 且 auth.resendCode 不可用: {resend_err}",
                reason="SENT_CODE_TYPE_APP",
            )

        if cls._is_sms_delivery(resent):
            await manager.append_log(
                task_id,
                "已成功将挑战通道降级/切换为短信分发，继续轮询带外网关"
            )
            return resent, DEFAULT_SMS_POLL_ATTEMPTS

        if cls._is_app_delivery(resent):
            await manager.append_log(
                task_id,
                "重发后服务端仍将验证码下发到已有设备客户端 (SentCodeTypeApp)，将快速退订换号"
            )
            raise SentCodeAppDeliveryError(
                "重发后服务端仍将验证码下发到已有设备客户端 (SentCodeTypeApp)，"
                "带外短信网关无法接收，已快速中止以免空等 120 秒",
                reason="SENT_CODE_TYPE_APP",
            )

        await manager.append_log(
            task_id,
            f"⚠️ 重发后通道仍非短信 ({cls._tl_type_name(getattr(resent, 'type', None))})，"
            f"仅做 {FAST_FAIL_SMS_POLL_ATTEMPTS} 次短轮询后若无码则退订"
        )
        return resent, FAST_FAIL_SMS_POLL_ATTEMPTS

    @classmethod
    async def perform_handshake(cls, client: TelegramClient, profile: Dict[str, Any], task_id: str, manager: RegistrationTaskManager):
        """执行标准端点握手序列与协议状态对齐"""
        await manager.append_log(task_id, "开始执行协议端点初始化握手序列...")
        
        nearest_dc = await client(functions.help.GetNearestDcRequest())
        await manager.append_log(task_id, f"探测数据中心拓扑: 建议最近 DC {nearest_dc.nearest_dc}, 本地接入 DC {nearest_dc.this_dc}")
        await asyncio.sleep(random.uniform(0.3, 0.7))

        server_config = await client(functions.help.GetConfigRequest())
        await manager.append_log(task_id, f"同步服务端全局网络路由参数 (DC 路由节点总数: {len(server_config.dc_options)})")
        await asyncio.sleep(random.uniform(0.3, 0.6))

        await client(functions.help.GetAppConfigRequest(hash=0))
        await manager.append_log(task_id, "同步动态应用实验配置与端点流控参数 (AppConfig)")
        await asyncio.sleep(random.uniform(0.4, 0.8))

        await client(functions.langpack.GetLanguagesRequest(lang_pack=profile.get("lang_pack", "android")))
        await manager.append_log(task_id, f"载入端点本地化语言包资源: {profile.get('lang_pack')}")

        jitter = random.uniform(3.5, 5.5)
        await manager.append_log(task_id, f"执行自适应状态机停顿与流量整形 ({jitter:.1f} 秒)...")
        await asyncio.sleep(jitter)

    @classmethod
    async def _send_code_with_recaptcha(
        cls,
        client: TelegramClient,
        phone: str,
        profile: Dict[str, Any],
        code_settings,
        bypass_svc: AttestationGatewayService,
        active_proxy: Optional[Dict[str, Any]],
        task_id: str,
        manager: RegistrationTaskManager,
    ):
        """发送 auth.sendCode；若触发 RECAPTCHA_CHECK_* 则自动解题并 invokeWithReCaptcha 重发。"""
        send_req = functions.auth.SendCodeRequest(
            phone_number=phone,
            api_id=profile["api_id"],
            api_hash=profile["api_hash"],
            settings=code_settings,
        )
        try:
            return await client(send_req)
        except Exception as send_err:
            parsed = parse_recaptcha_check(send_err)
            if not parsed:
                raise
            action, site_key = parsed
            await manager.append_log(
                task_id,
                f"检测到 Telegram RECAPTCHA_CHECK 人机挑战 "
                f"(action={action}, site_key={site_key})，正在通过 REGHelp RecaptchaMobile 自动解题..."
            )
            try:
                token = await bypass_svc.get_recaptcha_mobile_token(
                    site_key=site_key,
                    action=action,
                    profile=profile,
                    proxy=active_proxy,
                    log_callback=lambda msg: manager.append_log(task_id, msg),
                )
            except Exception as solve_err:
                raise RecaptchaChallengeError(
                    f"REGHelp RecaptchaMobile 解题失败: {solve_err}",
                    action=action,
                    site_key=site_key,
                ) from solve_err

            if not token:
                raise RecaptchaChallengeError(
                    "REGHelp RecaptchaMobile 未返回 token",
                    action=action,
                    site_key=site_key,
                )

            await manager.append_log(
                task_id,
                "已获得 Recaptcha token，通过 invokeWithReCaptcha 重发 auth.sendCode..."
            )
            try:
                return await client(functions.InvokeWithReCaptchaRequest(
                    token=token,
                    query=send_req,
                ))
            except Exception as retry_err:
                retry_parsed = parse_recaptcha_check(retry_err)
                detail = str(retry_err)
                if retry_parsed:
                    detail = f"{detail} (仍为 RECAPTCHA_CHECK_{retry_parsed[0]}__{retry_parsed[1]})"
                raise RecaptchaChallengeError(
                    f"附带 Recaptcha token 重发 SendCodeRequest 仍失败: {detail}",
                    action=action,
                    site_key=site_key,
                ) from retry_err

    @classmethod
    async def _fetch_push_token_if_needed(
        cls,
        *,
        bypass_svc: AttestationGatewayService,
        profile: Dict[str, Any],
        aid: str,
        task_id: str,
        manager: RegistrationTaskManager,
        plan,
        push_token: Optional[str],
        push_task_id: Optional[str],
        push_provider: Optional[str],
        push_token_obtained_at: Optional[float],
        hunt_enabled: bool,
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[float]]:
        if push_token is not None:
            if hunt_enabled:
                attach_hint = "attach" if plan.attach_push_token else "不 attach（SMS 优先）"
                await manager.append_log(
                    task_id,
                    f"[猎号] 复用已持有 Push Token "
                    f"(提供源: {push_provider or '-'}, task_id={push_task_id or '-'}, {attach_hint})"
                )
            return push_token, push_task_id, push_provider, push_token_obtained_at

        if not plan.should_request_push_token:
            return None, None, None, None

        try:
            await manager.append_log(
                task_id, "向 Attestation 高可用网关请求平台推送握手凭证 (Signed Push Token)..."
            )
            push_token, push_task_id, push_provider = await bypass_svc.get_push_token(
                profile,
                aid=aid,
                log_callback=lambda msg: manager.append_log(task_id, msg),
                ref=task_id,
            )
            if push_token:
                push_token_obtained_at = time.monotonic()
                info = classify_push_token(push_token)
                manager.update_task_status(
                    task_id,
                    "running",
                    push_task_id=push_task_id,
                    push_provider=push_provider,
                )
                await manager.append_log(
                    task_id,
                    f"成功获取平台合规签署的 Attestation Push Token "
                    f"(提供源: {push_provider}, task_id={push_task_id or '-'}, "
                    f"kind={info['kind']} len={info['length']} "
                    f"suspicious={'是' if info['suspicious'] else '否'})"
                )
                if not info["ok"]:
                    await manager.append_log(
                        task_id,
                        f"⚠️ Push Token 形态不合格（{info['kind']} len={info['length']}），"
                        "将冷却换发"
                    )
                    cfg = ConfigManager.get_instance().config
                    if is_strict_alignment(cfg) and bool(getattr(cfg, "flood_rotate_push_token", True)):
                        await cls._refund_push_token(
                            bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                            None, task_id, manager, "PUSH_TOKEN_INVALID", retire=True,
                        )
                        return None, None, None, None
            else:
                await manager.append_log(task_id, "⚠️ Attestation Push Token 未返回，回退至标准信道...")
        except Exception as e:
            await manager.append_log(
                task_id, f"⚠️ Attestation Push 凭证请求跳过/降级 ({e})，自动切换至标准信道模式"
            )
        return push_token, push_task_id, push_provider, push_token_obtained_at

    @classmethod
    async def _send_code_respecting_delivery_plan(
        cls,
        *,
        client,
        phone: str,
        profile: Dict[str, Any],
        push_token: Optional[str],
        push_task_id: Optional[str],
        push_provider: Optional[str],
        push_token_obtained_at: Optional[float],
        delivery_plan,
        bypass_svc: AttestationGatewayService,
        active_proxy: Optional[Dict[str, Any]],
        task_id: str,
        manager: RegistrationTaskManager,
        aid: str,
        hunt_enabled: bool,
    ) -> Tuple[Any, Any, Optional[str], Optional[str], Optional[str], Optional[float], Any]:
        """sendCode + 通道解析；遇 API_ID_PUBLISHED_FLOOD 时按策略 escalate Push。

        返回 (sent_code, sms_poll_attempts, push_token, push_task_id, push_provider,
        push_token_obtained_at, delivery_plan_for_refund)。
        """
        plan = delivery_plan
        if plan.attach_push_token and not push_token:
            api_id = profile.get("api_id")
            api_hash = cls._api_hash_for_log(profile)
            raise RequiredPushTokenMissingError(
                f"通道计划要求 attach Push Token，但 Attestation 网关未返回可用凭证；"
                f"拒绝以 api_id={api_id} api_hash={api_hash} 裸发 sendCode"
                f"（否则会误报成 API_ID_PUBLISHED_FLOOD / 国家结论）",
                api_id=api_id if isinstance(api_id, int) else None,
            )
        if plan.attach_push_token and push_token:
            info = classify_push_token(push_token)
            config = ConfigManager.get_instance().config
            conflicts = detect_push_slot_conflicts(
                profile, push_token, attached=True
            )
            for item in conflicts:
                await manager.append_log(task_id, f"⚠️ [指纹/槽位冲突] {item}")
            # 已知错槽（Android FCM → iOS 文档槽）只告警；真正的类型交叉直接拒绝
            hard = [
                c for c in conflicts
                if c.startswith("类型冲突") or (info.get("suspicious") and info.get("kind") == "apns_hex" and "Android" in ",".join(conflicts))
            ]
            if hard:
                raise RequiredPushTokenMissingError(
                    "；".join(hard) + "；拒绝塞进 CodeSettings.token",
                    api_id=profile.get("api_id") if isinstance(profile.get("api_id"), int) else None,
                )
            if is_strict_alignment(config) and not info["ok"]:
                raise RequiredPushTokenMissingError(
                    f"Push Token 形态不合格（kind={info['kind']} len={info['length']}），"
                    "拒绝塞进 CodeSettings.token",
                    api_id=profile.get("api_id") if isinstance(profile.get("api_id"), int) else None,
                )
        code_settings = cls._build_code_settings_from_plan(push_token, plan)
        await cls._append_send_code_credential_log(
            task_id, manager, profile, push_token, plan, code_settings
        )
        await manager.append_log(task_id, "调用 auth.sendCode 触发服务端瞬时握手挑战分发...")
        try:
            sent_code = await cls._send_code_with_recaptcha(
                client=client,
                phone=phone,
                profile=profile,
                code_settings=code_settings,
                bypass_svc=bypass_svc,
                active_proxy=active_proxy,
                task_id=task_id,
                manager=manager,
            )
        except ApiIdPublishedFloodError:
            if not plan.can_escalate_on_published_flood:
                raise
            escalated = escalation_plan_after_published_flood(plan)
            await manager.append_log(
                task_id,
                "⚠️ sendCode 触发 API_ID_PUBLISHED_FLOOD；按通道策略 escalate："
                "申请 Push Token 并以 push_required 重试一次..."
            )
            await cls._log_code_delivery_plan(task_id, manager, escalated)
            push_token, push_task_id, push_provider, push_token_obtained_at = await cls._fetch_push_token_if_needed(
                bypass_svc=bypass_svc,
                profile=profile,
                aid=aid,
                task_id=task_id,
                manager=manager,
                plan=escalated,
                push_token=push_token,
                push_task_id=push_task_id,
                push_provider=push_provider,
                push_token_obtained_at=push_token_obtained_at,
                hunt_enabled=hunt_enabled,
            )
            plan = escalated
            if plan.attach_push_token and not push_token:
                api_id = profile.get("api_id")
                raise RequiredPushTokenMissingError(
                    f"FLOOD escalate 后仍未拿到 Push Token，拒绝以 api_id={api_id} 再次裸发 sendCode",
                    api_id=api_id if isinstance(api_id, int) else None,
                )
            code_settings = cls._build_code_settings_from_plan(push_token, plan)
            await cls._append_send_code_credential_log(
                task_id, manager, profile, push_token, plan, code_settings
            )
            sent_code = await cls._send_code_with_recaptcha(
                client=client,
                phone=phone,
                profile=profile,
                code_settings=code_settings,
                bypass_svc=bypass_svc,
                active_proxy=active_proxy,
                task_id=task_id,
                manager=manager,
            )
        sent_code, sms_poll_attempts = await cls.resolve_sent_code_channel(
            client=client,
            phone=phone,
            sent_code=sent_code,
            task_id=task_id,
            manager=manager,
            bypass_svc=bypass_svc,
            profile=profile,
            emulation_label=getattr(plan, "emulation_label", "balanced"),
        )
        return sent_code, sms_poll_attempts, push_token, push_task_id, push_provider, push_token_obtained_at, plan

    @classmethod
    def _resolve_hunt_limits(cls, config, no_number_retries: Optional[int] = None) -> Dict[str, Any]:
        def _int(name, default):
            try:
                return int(getattr(config, name, default) or default)
            except (TypeError, ValueError):
                return default

        def _float(name, default):
            try:
                return float(getattr(config, name, default) or default)
            except (TypeError, ValueError):
                return default

        retries = no_number_retries
        if retries is None:
            retries = _int("hunt_no_number_retries", DEFAULT_HUNT_NO_NUMBER_RETRIES)
        try:
            retries = int(retries)
        except (TypeError, ValueError):
            retries = DEFAULT_HUNT_NO_NUMBER_RETRIES
        retries = max(0, min(retries, 100))

        # 熔断阈值允许显式为 0（关闭），不能走 `or default` 那套兜底
        fuse_raw = getattr(config, "hunt_app_delivery_fuse", DEFAULT_HUNT_APP_DELIVERY_FUSE)
        try:
            fuse = DEFAULT_HUNT_APP_DELIVERY_FUSE if fuse_raw is None else int(fuse_raw)
        except (TypeError, ValueError):
            fuse = DEFAULT_HUNT_APP_DELIVERY_FUSE
        fuse = max(0, min(fuse, 100))

        return {
            "no_number_retries": retries,
            "no_number_delay": max(0.0, min(_float("hunt_no_number_retry_delay_sec", DEFAULT_HUNT_NO_NUMBER_DELAY_SEC), 60.0)),
            "proxy_max_uses": (
                1 if is_strict_alignment(config)
                else max(1, min(_int("hunt_proxy_max_uses", DEFAULT_HUNT_PROXY_MAX_USES), 50))
            ),
            "device_max_uses": max(1, min(_int("hunt_device_max_uses", DEFAULT_HUNT_DEVICE_MAX_USES), 50)),
            "app_blacklist_ttl_hours": max(
                0.5,
                min(_float("hunt_app_blacklist_ttl_hours", DEFAULT_HUNT_APP_BLACKLIST_TTL_HOURS), 720.0),
            ),
            # 0 = 关闭熔断（把预算跑满）
            "app_delivery_fuse": fuse,
        }

    @classmethod
    def resolve_hunt_lease_budget(
        cls,
        config,
        *,
        count: int = 1,
        max_number_attempts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """把「每任务取号次数 × 批次任务数」压到联合上限内。

        猎号真正花钱的动作是向接码平台租号，单任务 500 次 × 10 路并发就是 5000 次租号，
        足以一次抽干余额。这里统一算出计划租号次数，超限时按批次数把 attempts 裁到上限内，
        并把裁剪结果交给调用方去播报——不能悄悄改用户填的数字。
        """
        try:
            limit = int(getattr(config, "hunt_max_total_leases", DEFAULT_HUNT_MAX_TOTAL_LEASES) or DEFAULT_HUNT_MAX_TOTAL_LEASES)
        except (TypeError, ValueError):
            limit = DEFAULT_HUNT_MAX_TOTAL_LEASES
        limit = max(1, min(limit, MAX_NUMBER_ATTEMPTS_CAP * BATCH_CONCURRENCY_MAX))

        try:
            safe_count = int(count or 1)
        except (TypeError, ValueError):
            safe_count = 1
        safe_count = max(1, safe_count)

        try:
            requested = int(max_number_attempts or 1)
        except (TypeError, ValueError):
            requested = 1
        requested = max(1, min(requested, MAX_NUMBER_ATTEMPTS_CAP))

        attempts = requested
        clamped = False
        rejected = False
        if safe_count > limit:
            # 光是任务数就超过联合上限，没有可裁剪的空间，只能让调用方拒绝
            rejected = True
            attempts = 1
        elif safe_count * requested > limit:
            attempts = max(1, limit // safe_count)
            clamped = attempts != requested

        planned = safe_count * attempts
        if rejected:
            message = (
                f"批次任务数 {safe_count} 已超过猎号联合上限 {limit}（hunt_max_total_leases），"
                "请减少任务数或调高上限"
            )
        elif clamped:
            message = (
                f"每任务取号次数已从 {requested} 裁剪为 {attempts}："
                f"{safe_count} 路 × {requested} 次 = {safe_count * requested} 次租号，"
                f"超过联合上限 {limit}（hunt_max_total_leases）。"
                f"当前计划最多租号 {planned} 次"
            )
        else:
            message = f"计划最多租号 {planned} 次（{safe_count} 路 × {attempts} 次，联合上限 {limit}）"
        return {
            "count": safe_count,
            "requested_attempts": requested,
            "max_number_attempts": attempts,
            "planned_leases": planned,
            "limit": limit,
            "clamped": clamped,
            "rejected": rejected,
            "message": message,
        }

    @classmethod
    def _hunt_cancel_requested(cls, task_id: str, manager: RegistrationTaskManager) -> bool:
        """猎号循环的取消检查钩子。

        POST /api/register/tasks/{id}/cancel 只置 cancel_requested，不打断正在进行中的
        sendCode / OTP 轮询；本钩子在每轮取号前读取标记，让循环在下一轮开始前收住。
        """
        return manager.cancel_requested(task_id)

    @classmethod
    async def _finalize_exhausted_hunt(
        cls,
        *,
        task_id: str,
        manager: RegistrationTaskManager,
        bypass_svc: Optional[AttestationGatewayService],
        push_token: Optional[str],
        push_task_id: Optional[str],
        push_provider: Optional[str],
        push_token_obtained_at: Optional[float],
        phone: Optional[str],
        attempts_used: int,
        max_attempts: int,
        scanned: int,
        blacklisted: int,
        last_failure_reason: Optional[str],
        stop_reason: Optional[str] = None,
    ) -> None:
        """猎号未能进入 OTP 阶段时的统一终态收尾。

        终态码优先用具体的提前终止原因（FLOOD/取消），否则一律给 HUNT_EXHAUSTED，
        并把扫号次数 / 拉黑数 / 最后一次失败原因一起写进任务，便于前端与复盘。
        """
        code = stop_reason or "HUNT_EXHAUSTED"
        if push_token or push_task_id:
            # 这枚 Token 从头到尾没换来一条短信，按扫尽/频控原因走退款审计
            await cls._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                phone, task_id, manager, code,
            )
        detail = (
            f"取号 {attempts_used}/{max_attempts} 次，扫号 {scanned} 个，"
            f"拉黑/拦截 {blacklisted} 个"
            + (f"，最后一次失败原因 {last_failure_reason}" if last_failure_reason else "")
        )
        if code == "HUNT_EXHAUSTED":
            summary = f"[猎号] 已用尽取号次数仍未注册成功（{detail}）"
        else:
            summary = f"[猎号] 提前终止（{code}）：{detail}"
        await manager.append_log(task_id, summary)
        manager.update_task_status(
            task_id,
            "canceled" if code == "HUNT_CANCELED" else "failed",
            error=f"{code}: {detail}",
            hunt_attempt=attempts_used,
            hunt_max=max_attempts,
            hunt_scanned=scanned,
            hunt_blacklisted=blacklisted,
            hunt_last_reason=last_failure_reason,
        )

    @classmethod
    async def _lease_number_with_retries(
        cls,
        sms_svc,
        target_country: str,
        lease_max_price: Optional[float],
        task_id: str,
        manager: RegistrationTaskManager,
        *,
        hunt_enabled: bool,
        no_number_retries: int,
        no_number_delay: float,
        provider_ids: Optional[List[str]] = None,
    ):
        """取号；猎号模式下无库存时软重试，耗尽后抛出 NoNumberAvailableError。"""
        import inspect

        attempts = (no_number_retries + 1) if hunt_enabled else 1
        last_exc: Optional[BaseException] = None
        lease_kwargs: Dict[str, Any] = {
            "country": target_country,
            "service": "tg",
            "max_price": lease_max_price,
        }
        # FiveSim / Vak-SMS 等平台无 providerIds；仅在 get_number 签名支持时传入，避免 TypeError
        try:
            params = inspect.signature(sms_svc.get_number).parameters
            if provider_ids and ("provider_ids" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )):
                lease_kwargs["provider_ids"] = provider_ids
        except (TypeError, ValueError):
            if provider_ids:
                lease_kwargs["provider_ids"] = provider_ids
        for nr in range(1, attempts + 1):
            try:
                return await sms_svc.get_number(**lease_kwargs)
            except NoNumberAvailableError as ex:
                last_exc = ex
                if nr >= attempts:
                    break
                await manager.append_log(
                    task_id,
                    f"[猎号] 平台暂无可用号码（NO_NUMBERS），软重试 {nr}/{no_number_retries}"
                    f"（{no_number_delay:.1f}s 后）…"
                )
                if no_number_delay > 0:
                    await asyncio.sleep(no_number_delay)
        assert last_exc is not None
        raise last_exc

    @classmethod
    async def run_registration(
        cls,
        task_id: str,
        country: Optional[str] = None,
        app_type: Optional[str] = None,
        proxy_override: Optional[Dict[str, Any]] = None,
        set_2fa: Optional[bool] = None,
        proxy_id: Optional[str] = None,
        proxy_mode: str = "custom_pool",
        sms_provider: Optional[str] = None,
        max_price: Optional[float] = None,
        max_number_attempts: Optional[int] = None,
        no_number_retries: Optional[int] = None,
        provider_ids: Optional[List[str]] = None,
    ):
        """执行单次边缘虚拟节点引导全流程。

        猎号（max_number_attempts>1）目标只有两个：
        1) 注册成功 → 停止；
        2) 尽量扫接码平台号码并拉黑不可用号（APP/已注册/封禁等）。
        无库存默认软重试；代理/设备按次数轮换以降低 FLOOD。
        """
        manager = RegistrationTaskManager.get_instance()
        # 排队期间被取消的任务不得被这里的 running 复活
        if cls._hunt_cancel_requested(task_id, manager):
            await manager.append_log(task_id, "[取消] 任务在进入引导流程前已被取消，不再租号")
            manager.update_task_status(
                task_id, "canceled", error="HUNT_CANCELED: 启动前取消，未消耗号码与 Push Token"
            )
            return
        manager.update_task_status(task_id, "running")

        config = ConfigManager.get_instance().config
        # 新任务默认不受历史 API_ID_PUBLISHED_FLOOD 门闩影响（否则会「启动都启动不了」）
        flood_policy = cls._flood_gate_policy(config)
        if not flood_policy.get("block_new_sends"):
            if SendCodeFloodWindow.get().clear_published_flood():
                await manager.append_log(
                    task_id,
                    "[FLOOD窗] 已清除历史 API_ID_PUBLISHED_FLOOD 门闩，本任务照常租号测试",
                )
        target_country = (country or config.target_country).lower()
        active_app = app_type or config.active_app_type

        resolved_sms_provider = cls.resolve_sms_provider(config, sms_provider)
        sms_svc = cls._create_sms_service(config, resolved_sms_provider)
        lease_max_price = cls.resolve_sms_max_price(config, max_price)

        try:
            max_attempts = int(max_number_attempts or 1)
        except (TypeError, ValueError):
            max_attempts = 1
        max_attempts = max(1, min(max_attempts, MAX_NUMBER_ATTEMPTS_CAP))
        hunt_enabled = max_attempts > 1
        hunt_limits = cls._resolve_hunt_limits(config, no_number_retries=no_number_retries)

        try:
            active_proxy = await cls.resolve_active_proxy(
                config=config,
                target_country=target_country,
                task_id=task_id,
                manager=manager,
                proxy_override=proxy_override,
                proxy_id=proxy_id,
                proxy_mode=proxy_mode,
            )
        except ProxyCountryMismatchError as geo_err:
            await manager.append_log(task_id, f"❌ {geo_err}")
            manager.update_task_status(task_id, "failed", error=str(geo_err))
            return

        # 统一 Attestation / Push 凭证高可用网关：按 config.attestation_provider_mode 策略
        # 在 REGHelp 与 AntiSafety 两个独立提供源之间自动选择主备顺序并容灾切换
        bypass_svc = AttestationGatewayService(config, proxy=active_proxy)

        profile = DeviceProfileManager.get_resolved_profile(active_app, target_country)
        aid = profile["aid"]

        act_id = None
        check_id = None
        client = None
        phone = None
        push_token = None
        push_task_id = None
        push_provider = None
        push_token_obtained_at = None
        credentials_resolved = False
        session_path = None
        meta_path = None
        phone_code_hash = None
        sms_poll_attempts = DEFAULT_SMS_POLL_ATTEMPTS
        proxy_send_uses = 0
        device_send_uses = 0
        # 猎号收尾统计与交接标记：
        # phone_disposed 表示号码已在循环内 cancel + 拉黑 + 上报完毕，外层兜底不得重复处理
        hunt_scanned = 0
        hunt_blacklisted = 0
        # 连续 APP 投递计数：任何一轮走到非 APP 结局都清零，只有真的「一直只投 App」才熔断
        hunt_app_streak = 0
        last_failure_reason: Optional[str] = None
        hunt_stop_reason: Optional[str] = None
        entered_otp_stage = False
        phone_disposed = False

        try:
            await manager.append_log(
                task_id,
                f"[接码平台] 当前使用接码通道: {cls._sms_provider_label(sms_svc, resolved_sms_provider)}"
            )
            await manager.append_log(task_id, f"选定端点模板: {profile['name']} (AID: {aid})")
            pack_alias = profile.get("device_pack_alias")
            pack_country = (profile.get("device_pack_country") or "").upper()
            pack_match = profile.get("device_pack_match") or "none"
            pack_auto = bool(profile.get("device_pack_auto"))
            if pack_alias:
                match_label = DeviceProfileManager.describe_pack_match(pack_match, pack_auto)
                await manager.append_log(
                    task_id,
                    f"硬件指纹包: {pack_alias}"
                    + (f" [{pack_country}]" if pack_country else "")
                    + f" · {match_label}"
                )
            elif pack_match == "none":
                await manager.append_log(task_id, "硬件指纹包: 目录为空，回退端点模板默认机型")
            await manager.append_log(task_id, f"绑定硬件特征: {profile['device_model']} ({profile['system_version']}), App: {profile['app_version']}")
            await manager.append_log(task_id, f"网络语言拓扑: {profile['system_lang_code']}, 时区偏置: {profile.get('tz_offset', -14400)}")
            await manager.append_log(task_id, alignment_summary_for_log(profile, config))
            if profile.get("vault_fingerprint_source"):
                await manager.append_log(
                    task_id, f"vault 指纹回放: {profile.get('vault_fingerprint_source')}"
                )
            if profile.get("has_device_secret"):
                await manager.append_log(
                    task_id,
                    f"vault attestation: has_device_secret=是 "
                    f"len={profile.get('device_secret_len') or 0} "
                    f"injected={'是' if profile.get('device_secret_injected') else '否'} "
                    f"（CodeSettings 无此槽位，仅 FirebaseSms 可选注入）"
                )
            try:
                validate_strict_device_profile(profile, config)
            except DeviceAlignmentError as align_err:
                await manager.append_log(task_id, f"❌ {align_err}")
                manager.update_task_status(task_id, "failed", error=str(align_err))
                return

            if hunt_enabled:
                # 代理被 1:1 钉死（批量槽位 / 显式指定）时不存在池内轮换语义，如实播报
                proxy_pinned = bool(proxy_override or proxy_id)
                proxy_note = (
                    "出口已 1:1 钉死"
                    + ("（批量槽位）" if proxy_override else "（用户显式指定）")
                    + "，全程不轮换代理"
                    if proxy_pinned
                    else f"代理每 {hunt_limits['proxy_max_uses']} 次 sendCode 尝试轮换（池内需有其它同国节点）"
                )
                await manager.append_log(
                    task_id,
                    f"[猎号] 目标：注册成功即停，否则扫号拉黑。"
                    f"最多取号 {max_attempts} 次（即本任务最多向接码平台租号 {max_attempts} 次）；"
                    f"无库存软重试 {hunt_limits['no_number_retries']} 次；"
                    f"{proxy_note}；"
                    f"设备每 {hunt_limits['device_max_uses']} 次 sendCode 换指纹+Push；"
                    + (
                        f"连续 {hunt_limits['app_delivery_fuse']} 次 APP 投递即熔断收工"
                        if hunt_limits["app_delivery_fuse"] > 0
                        else "APP 投递熔断已关闭"
                    )
                    + f"；APP 号临时拉黑 {hunt_limits['app_blacklist_ttl_hours']:.0f}h"
                )
                manager.update_task_status(task_id, "running", hunt_max=max_attempts)

            from backend.app.models.schemas import format_sms_max_price

            bid_label = format_sms_max_price(lease_max_price)
            attempt_idx = 0
            while attempt_idx < max_attempts:
                attempt_idx += 1
                act_id = None
                check_id = None
                phone = None
                session_path = None
                meta_path = None
                # 新一轮换了新号：上一轮的「已 cancel+拉黑」交接标记必须复位，
                # 否则外层兜底会以为本轮号码也处理过，漏掉退订与拉黑
                phone_disposed = False
                # 上一轮 client 应已断开；防御性清理
                if client is not None:
                    await cls._disconnect_client_quiet(client)
                    client = None

                if max_attempts > 1:
                    await manager.append_log(
                        task_id,
                        f"[猎号] 第 {attempt_idx}/{max_attempts} 次取号尝试"
                    )
                    manager.update_task_status(
                        task_id, "running", hunt_attempt=attempt_idx, hunt_max=max_attempts
                    )

                # 猎号取消钩子：外部 POST cancel 置 cancel_requested，下一轮取号前收住
                if cls._hunt_cancel_requested(task_id, manager):
                    hunt_stop_reason = "HUNT_CANCELED"
                    await manager.append_log(
                        task_id, f"[猎号] 收到取消请求，在第 {attempt_idx} 轮开始前停止扫号"
                    )
                    break

                flood_stop = await cls._respect_flood_window(task_id, manager, config=config)
                if flood_stop:
                    hunt_stop_reason = flood_stop
                    last_failure_reason = flood_stop
                    skip_msg = (
                        f"{flood_stop}: 同 api_id 冷却中，跳过租号/发码"
                        "（未 cancel 其它已开跑任务）"
                    )
                    if not hunt_enabled:
                        manager.update_task_status(task_id, "failed", error=skip_msg)
                        return
                    break

                # 出口/设备轮换：sendCode 次数达到上限后降低 FLOOD 风险
                if hunt_enabled and proxy_send_uses >= hunt_limits["proxy_max_uses"]:
                    active_proxy, _rotated = await cls._rotate_hunt_proxy(
                        config=config,
                        target_country=target_country,
                        task_id=task_id,
                        manager=manager,
                        current_proxy=active_proxy,
                        proxy_mode=proxy_mode,
                        reason=(
                            f"当前代理已用于 sendCode {proxy_send_uses} 次"
                            f"（上限 {hunt_limits['proxy_max_uses']}，保留设备与 Push）"
                        ),
                        proxy_override=proxy_override,
                        proxy_id=proxy_id,
                    )
                    # 换不掉（池内只有一个节点 / 出口被钉死）时同样清零计数：
                    # 否则每轮都会重复评估并重复播报同一条「未轮换」告警
                    proxy_send_uses = 0

                if hunt_enabled and device_send_uses >= hunt_limits["device_max_uses"]:
                    await manager.append_log(
                        task_id,
                        f"[猎号] 当前设备指纹已用于 sendCode {device_send_uses} 次，达到上限 "
                        f"{hunt_limits['device_max_uses']}，重采样设备并更换 Push"
                    )
                    if push_token or push_task_id:
                        # retire=True：新设备指纹不能再挂旧 Token，退款后同时移出本地复用候选
                        await cls._refund_push_token(
                            bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                            phone, task_id, manager, "HUNT_DEVICE_ROTATE", retire=True,
                        )
                    push_token = None
                    push_task_id = None
                    push_provider = None
                    push_token_obtained_at = None
                    credentials_resolved = False
                    profile = DeviceProfileManager.get_resolved_profile(active_app, target_country)
                    aid = profile["aid"]
                    device_send_uses = 0
                    await manager.append_log(
                        task_id,
                        f"[猎号] 新设备: {profile['device_model']} ({profile['system_version']}), App: {profile['app_version']}"
                    )
                    try:
                        validate_strict_device_profile(profile, config)
                    except DeviceAlignmentError as align_err:
                        await manager.append_log(task_id, f"❌ {align_err}")
                        hunt_stop_reason = "DEVICE_ALIGNMENT_REJECTED"
                        last_failure_reason = "DEVICE_ALIGNMENT_REJECTED"
                        break

                # 收码窗口保护：复用的 REGHelp Token 太老会把本轮 OTP 轮询压到几秒，
                # 真 SMS 号也收不到码。宁可先退掉旧 Token 重新申请，也不能进一个收不到码的轮次。
                # 库存复用（reghelp_reuse）的年龄按签发时间算，不能只看本任务租到它的时刻。
                token_age = cls._push_token_age_seconds(
                    push_provider, push_token_obtained_at, push_task_id, push_token
                )
                if hunt_enabled and push_token is not None and cls._push_token_window_exhausted(
                    push_provider,
                    push_token_obtained_at,
                    DEFAULT_SMS_POLL_ATTEMPTS,
                    token_age_seconds=token_age,
                ):
                    await manager.append_log(
                        task_id,
                        f"[猎号] 在用的 Push Token（{push_provider or '-'}）已签发 "
                        f"{token_age or 0.0:.0f}s，超过 {HUNT_PUSH_TOKEN_MAX_AGE_SECONDS:.0f}s 上限或"
                        f"剩余退款窗口不足 {HUNT_MIN_SMS_POLL_ATTEMPTS} 次 OTP 轮询，"
                        "先退旧 Token 再申请新的，保证本轮收码窗口完整"
                    )
                    await cls._refund_push_token(
                        bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                        phone, task_id, manager, "HUNT_PUSH_ROTATE", retire=True,
                    )
                    push_token = None
                    push_task_id = None
                    push_provider = None
                    push_token_obtained_at = None

                # 1. 租用带外通信句柄（热门/稀缺国家需携带 maxPrice 动态竞价，否则卡在底价空桶）
                if bid_label is not None:
                    await manager.append_log(
                        task_id,
                        f"正在向带外遥测提供者申请拓扑代码 '{target_country.upper()}' 的信道句柄"
                        f"（动态竞价上限 maxPrice={bid_label}，按账户结算币种原样出价）..."
                    )
                else:
                    await manager.append_log(
                        task_id,
                        f"正在向带外遥测提供者申请拓扑代码 '{target_country.upper()}' 的信道句柄"
                        "（未设置最高出价，使用平台底价；热门国家可能 NO_NUMBERS）..."
                    )
                if provider_ids:
                    await manager.append_log(
                        task_id,
                        f"指定供应商 providerIds={','.join(provider_ids)}（精确取号）"
                    )
                act_id, phone = await cls._lease_number_with_retries(
                    sms_svc,
                    target_country,
                    lease_max_price,
                    task_id,
                    manager,
                    hunt_enabled=hunt_enabled,
                    no_number_retries=hunt_limits["no_number_retries"],
                    no_number_delay=hunt_limits["no_number_delay"],
                    provider_ids=provider_ids,
                )
                manager.update_task_status(task_id, "running", phone=phone)
                await manager.append_log(task_id, f"成功获取端点通信句柄: {phone} (Session Handle ID: {act_id})")

                hunt_scanned += 1

                # 1.4 本地封禁号缓存：接码回收号复用时零成本拦截
                if not await cls._apply_banned_cache_gate(
                    phone=phone,
                    act_id=act_id,
                    sms_svc=sms_svc,
                    task_id=task_id,
                    manager=manager,
                    soft=hunt_enabled,
                ):
                    act_id = None  # 闸门已 cancel，置空避免任何后续路径重复退订
                    if hunt_enabled:
                        hunt_blacklisted += 1
                        hunt_app_streak = 0
                        last_failure_reason = LOCAL_BANNED_REASON
                        await manager.append_log(
                            task_id,
                            f"[猎号] 号码 {phone} 命中黑名单，换号继续（尚未消耗 Push Token）"
                        )
                        manager.update_task_status(task_id, "running")
                        continue
                    return

                # 1.5 号码注册状态预检：必须在 Push Token / auth.sendCode 之前完成
                if not await cls._apply_phone_precheck(
                    phone=phone,
                    act_id=act_id,
                    sms_svc=sms_svc,
                    task_id=task_id,
                    manager=manager,
                    proxy=active_proxy,
                    config=config,
                    soft=hunt_enabled,
                    hunt=hunt_enabled,
                ):
                    act_id = None
                    if hunt_enabled:
                        hunt_blacklisted += 1
                        hunt_app_streak = 0
                        last_failure_reason = PRECHECK_ALREADY_REGISTERED
                        await manager.append_log(
                            task_id,
                            f"[猎号] 号码 {phone} 预检已注册，换号继续（尚未消耗 Push Token）"
                        )
                        manager.update_task_status(task_id, "running")
                        continue
                    return

                # 2. 端点信誉预检
                await manager.append_log(task_id, "正在对通信句柄进行历史安全状态审计...")
                check_data = await bypass_svc.check_phone_history(phone, aid)
                if check_data:
                    check_id = check_data.get("id")
                    if "BANNED" in check_data.get("statuses", []):
                        await manager.append_log(task_id, "检测到该通信句柄存在服务端历史异常记录，触发主动退避与信道撤销！")
                        BannedPhonesCache.remember(
                            phone,
                            reason="PHONE_PREAUDIT_BANNED",
                            source=SOURCE_ANTISAFETY,
                            country=target_country,
                        )
                        await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "PHONE_PREAUDIT_BANNED")
                        await bypass_svc.report_result(check_id, aid, "REJECTED")
                        act_id = None
                        check_id = None
                        if hunt_enabled:
                            hunt_blacklisted += 1
                            hunt_app_streak = 0
                            last_failure_reason = "PHONE_PREAUDIT_BANNED"
                            await manager.append_log(
                                task_id,
                                f"[猎号] 预审封禁 {phone}，换号继续（Push Token 保持复用）"
                            )
                            manager.update_task_status(task_id, "running")
                            continue
                        manager.update_task_status(task_id, "failed", error="Endpoint handle pre-audit rejected")
                        return

                # 3. 验证码投递通道策略 + Attestation Push（按 plan 决定是否申请）
                delivery_plan = resolve_code_delivery_plan(
                    config,
                    profile,
                    hunt_app_streak=hunt_app_streak if hunt_enabled else 0,
                )
                await cls._log_code_delivery_plan(task_id, manager, delivery_plan)

                push_token, push_task_id, push_provider, push_token_obtained_at = await cls._fetch_push_token_if_needed(
                    bypass_svc=bypass_svc,
                    profile=profile,
                    aid=aid,
                    task_id=task_id,
                    manager=manager,
                    plan=delivery_plan,
                    push_token=push_token,
                    push_task_id=push_task_id,
                    push_provider=push_provider,
                    push_token_obtained_at=push_token_obtained_at,
                    hunt_enabled=hunt_enabled,
                )

                # 3.1 API 凭证策略裁决（仅在首次拿到/判定 Push 后执行一次）
                if not credentials_resolved:
                    original_api_id = profile["api_id"]
                    profile = DeviceProfileManager.resolve_effective_credentials(
                        profile, config, has_push_token=bool(push_token)
                    )
                    credentials_resolved = True
                    if bool(getattr(config, "official_client_emulation", False)):
                        await manager.append_log(
                            task_id,
                            f"[official] 官方客户端模拟生效：api_id={profile['api_id']} "
                            f"credential_source={profile.get('credential_source')}"
                        )
                    if profile["credential_source"] == "custom":
                        await manager.append_log(task_id, f"API 凭证策略: 强制使用自建开发者凭证 (api_id={profile['api_id']})")
                    elif profile["credential_source"] == "custom_auto_fallback":
                        await manager.append_log(
                            task_id,
                            f"⚠️ 未获取到有效 Push Token，且官方 api_id={original_api_id} "
                            f"属于已知公开泄露 ID，已自动回退至自建开发者凭证 (api_id={profile['api_id']}) 以规避 API_ID_PUBLISHED_FLOOD"
                        )
                    elif profile.get("credential_risk") == "published_id_without_push_token":
                        await manager.append_log(
                            task_id,
                            f"⚠️⚠️ 高风险: 当前使用官方公开泄露 api_id={profile['api_id']} 且无有效 Push Token，"
                            f"auth.sendCode 大概率触发 API_ID_PUBLISHED_FLOOD。建议在「全局参数拓扑」中配置自建开发者 "
                            f"api_id/api_hash (my.telegram.org) 并将 api_credential_mode 设为 auto 或 custom，"
                            f"或修复 Attestation 网关连通性以获取合法 Push Token"
                        )
                    elif profile.get("credential_risk") == "custom_mode_missing_credentials":
                        await manager.append_log(
                            task_id,
                            "⚠️ api_credential_mode 已设为 custom，但未配置 custom_api_id / custom_api_hash，"
                            "本次仍将回退使用官方内置凭证"
                        )
                    if profile.get("credential_source") == "vault_strict_api4":
                        override_note = ""
                        if str(getattr(config, "api_credential_mode", "") or "") == "custom":
                            override_note = "；已覆盖 api_credential_mode=custom（避免自建凭证与 Expert/api_id=4 路径冲突）"
                        await manager.append_log(
                            task_id,
                            f"[严格对齐] api_id 已从 {profile.get('api_id_pinned_from')} 钉死为 4 "
                            f"（hash={cls._api_hash_for_log(profile)}），禁止漂到 6/Payment"
                            f"{override_note}"
                        )
                    if profile.get("api_hash_corrected"):
                        await manager.append_log(
                            task_id,
                            f"⚠️ api_id={profile.get('api_id')} 的 api_hash 与官方固定配对不符，已纠正 "
                            f"（was={cls._api_hash_for_log({'api_hash': profile.get('api_hash_was')})} → "
                            f"{cls._api_hash_for_log(profile)}），避免 4 配 6 的 hash"
                        )
                    reconciled = reconcile_delivery_plan_after_credentials(
                        config,
                        profile,
                        delivery_plan,
                        hunt_app_streak=hunt_app_streak if hunt_enabled else 0,
                    )
                    if reconciled is not delivery_plan:
                        delivery_plan = reconciled
                        await cls._log_code_delivery_plan(task_id, manager, delivery_plan)

                if is_strict_alignment(config) and delivery_plan.attach_push_token:
                    try:
                        validate_strict_device_profile(
                            profile, config, has_push_token=bool(push_token)
                        )
                    except DeviceAlignmentError as align_err:
                        await manager.append_log(task_id, f"❌ {align_err}")
                        hunt_stop_reason = "DEVICE_ALIGNMENT_REJECTED"
                        last_failure_reason = "DEVICE_ALIGNMENT_REJECTED"
                        if not hunt_enabled:
                            manager.update_task_status(task_id, "failed", error=str(align_err))
                            return
                        break

                # 4. 初始化 MTProto 会话
                clean_phone = phone.replace("+", "").strip()
                session_filename = f"node_{target_country}_{clean_phone}"
                session_path = SESSIONS_DIR / f"{session_filename}.session"
                meta_path = SESSIONS_DIR / f"{session_filename}.json"

                proxy_dict = {
                    "proxy_type": active_proxy.get("proxy_type", "socks5"),
                    "addr": active_proxy.get("addr", "127.0.0.1"),
                    "port": int(active_proxy.get("port", 10808)),
                    "username": active_proxy.get("username"),
                    "password": active_proxy.get("password")
                }

                await manager.append_log(task_id, f"建立 MTProto 协议传输通道 (中继节点: {proxy_dict['addr']}:{proxy_dict['port']})...")
                client = TelegramClient(
                    session=str(session_path),
                    api_id=profile["api_id"],
                    api_hash=profile["api_hash"],
                    proxy=proxy_dict if proxy_dict["addr"] else None,
                    device_model=profile["device_model"],
                    system_version=profile["system_version"],
                    app_version=profile["app_version"],
                    lang_code=profile["lang_code"],
                    system_lang_code=profile["system_lang_code"]
                )
                init_snap = apply_init_connection_overrides(client, profile, config)
                if init_snap.get("blocked"):
                    await manager.append_log(
                        task_id,
                        f"InitConnection 指纹未写入: {init_snap.get('blocked')}",
                    )
                else:
                    await manager.append_log(task_id, describe_init_connection(client))

                if not await cls._connect_mtproto(
                    client, task_id, manager, sms_svc, act_id,
                    mark_failed=not hunt_enabled,
                ):
                    last_failure_reason = "CONNECT_TIMEOUT"
                    await cls._refund_push_token(
                        bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                        phone, task_id, manager, "CONNECT_TIMEOUT",
                    )
                    push_token = None
                    push_task_id = None
                    push_provider = None
                    push_token_obtained_at = None
                    await cls._disconnect_client_quiet(client)
                    client = None
                    cls._discard_incomplete_session(session_path, meta_path)
                    session_path = None
                    meta_path = None
                    act_id = None
                    if not hunt_enabled:
                        return
                    active_proxy, rotated = await cls._rotate_hunt_proxy(
                        config=config,
                        target_country=target_country,
                        task_id=task_id,
                        manager=manager,
                        current_proxy=active_proxy,
                        proxy_mode=proxy_mode,
                        reason="CONNECT_TIMEOUT 换出口重试",
                        proxy_override=proxy_override,
                        proxy_id=proxy_id,
                    )
                    bypass_svc = AttestationGatewayService(config, proxy=active_proxy)
                    await manager.append_log(
                        task_id,
                        f"[猎号] CONNECT_TIMEOUT 号码 {phone} 已退订"
                        + ("，出口已轮换" if rotated else "（出口未轮换）")
                        + f"，换号继续 ({attempt_idx}/{max_attempts})"
                    )
                    manager.update_task_status(task_id, "running")
                    continue
                await manager.append_log(task_id, "已完成 MTProto 传输层 Diffie-Hellman 密钥交换与加密连接建立")

                # 5. 执行协议端点握手序列
                await cls.perform_handshake(client, profile, task_id, manager)

                # 6. 发起挑战分发请求 (SendCode)
                try:
                    (
                        sent_code,
                        sms_poll_attempts,
                        push_token,
                        push_task_id,
                        push_provider,
                        push_token_obtained_at,
                        delivery_plan,
                    ) = await cls._send_code_respecting_delivery_plan(
                        client=client,
                        phone=phone,
                        profile=profile,
                        push_token=push_token,
                        push_task_id=push_task_id,
                        push_provider=push_provider,
                        push_token_obtained_at=push_token_obtained_at,
                        delivery_plan=delivery_plan,
                        bypass_svc=bypass_svc,
                        active_proxy=active_proxy,
                        task_id=task_id,
                        manager=manager,
                        aid=aid,
                        hunt_enabled=hunt_enabled,
                    )
                except SentCodeAppDeliveryError as ex:
                    reason = getattr(ex, "reason", None) or "SENT_CODE_TYPE_APP"
                    is_app = reason in APP_STREAK_REASONS
                    ttl_hours = hunt_limits["app_blacklist_ttl_hours"]
                    record = None
                    note = CHANNEL_FAIL_NOTES.get(reason, reason)
                    if phone:
                        record = BannedPhonesCache.remember(
                            phone,
                            reason=reason,
                            source=SOURCE_SENT_CODE,
                            country=target_country,
                            category=CATEGORY_APP_DELIVERY,
                            note=note,
                            ttl_hours=ttl_hours,
                        )
                    await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, reason)
                    if check_id:
                        await bypass_svc.report_result(check_id, aid, reason)
                    # 本轮已完成 cancel / remember / report_result，向外抛之前必须把句柄与
                    # 拉黑标记交接掉，否则外层兜底会对同一号码再退订一次、再拉黑一次
                    act_id = None
                    check_id = None
                    phone_disposed = True
                    await cls._disconnect_client_quiet(client)
                    client = None
                    cls._discard_incomplete_session(session_path, meta_path)
                    session_path = None
                    meta_path = None
                    hunt_blacklisted += 1
                    if is_app:
                        hunt_app_streak += 1
                    last_failure_reason = reason
                    log_label = "APP 投递不可用（未必已注册）" if is_app else note
                    await manager.append_log(
                        task_id,
                        f"⚠️ {log_label}：号码 {phone} 已退订，临时拉黑至 "
                        f"{cls._app_blacklist_expiry_label(record, ttl_hours)}"
                        f"（分类 {CATEGORY_APP_DELIVERY}，TTL {ttl_hours:.0f}h，到期自动放回可试池）"
                    )
                    if hunt_enabled:
                        proxy_send_uses += 1
                        device_send_uses += 1
                        fuse = hunt_limits["app_delivery_fuse"]
                        if fuse and hunt_app_streak >= fuse:
                            # 连续只投 App 是系统性失败（凭证 / Push / 指纹），换号救不了
                            hunt_stop_reason = "HUNT_APP_FUSE"
                            await manager.append_log(
                                task_id,
                                f"❌ [猎号] 连续 {hunt_app_streak} 个号码都只下发站内 App "
                                f"（熔断阈值 {fuse}），判定为系统性 APP 投递失败而非号码问题，"
                                "提前结束猎号以免把剩余取号预算全部烧在同一失败模式上。"
                                "请检查 Push Token 提供源 / api_id 凭证 / 设备指纹配置"
                            )
                            break
                        await manager.append_log(
                            task_id,
                            f"[猎号] {'SentCodeTypeApp' if is_app else reason}（{reason}）换号继续 "
                            f"({attempt_idx}/{max_attempts})；"
                            f"连续 APP {hunt_app_streak}"
                            + (f"/{fuse}" if fuse else "（熔断关闭）")
                            + f"；代理已用 {proxy_send_uses}/{hunt_limits['proxy_max_uses']}，"
                            f"设备已用 {device_send_uses}/{hunt_limits['device_max_uses']}"
                        )
                        manager.update_task_status(task_id, "running")
                        continue
                    raise
                except ApiIdPublishedFloodError:
                    cls._trip_flood_window(
                        reason="API_ID_PUBLISHED_FLOOD",
                        seconds=0,
                        hard=True,
                        api_id=(profile or {}).get("api_id"),
                        config=config,
                    )
                    await cls._refund_and_revoke_channel(
                        sms_svc, act_id, task_id, manager, "API_ID_PUBLISHED_FLOOD"
                    )
                    act_id = None
                    await cls._disconnect_client_quiet(client)
                    client = None
                    cls._discard_incomplete_session(session_path, meta_path)
                    session_path = None
                    meta_path = None
                    last_failure_reason = "API_ID_PUBLISHED_FLOOD"
                    hunt_app_streak = 0
                    if push_token or push_task_id:
                        await cls._refund_push_token(
                            bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                            phone, task_id, manager, "API_ID_PUBLISHED_FLOOD", retire=True,
                        )
                        push_token = None
                        push_task_id = None
                        push_provider = None
                        push_token_obtained_at = None
                    # 默认：本号失败即可；猎号换下一号继续测。省钱硬停才整任务收束。
                    block_new = bool(cls._flood_gate_policy(config).get("block_new_sends"))
                    if hunt_enabled and not block_new:
                        proxy_send_uses += 1
                        device_send_uses += 1
                        await manager.append_log(
                            task_id,
                            f"[Expert] 本号 API_ID_PUBLISHED_FLOOD api_id={profile.get('api_id')} "
                            f"{cls._log_push_token_slot(push_token, delivery_plan)} → 丢本号换号继续 "
                            f"({attempt_idx}/{max_attempts})；不拦后续新测试",
                        )
                        manager.update_task_status(task_id, "running")
                        continue
                    await manager.append_log(
                        task_id,
                        f"[Expert] 本号 API_ID_PUBLISHED_FLOOD api_id={profile.get('api_id')} "
                        f"{cls._log_push_token_slot(push_token, delivery_plan)} → 本任务结束；"
                        "不阻止后续新测试 / 其它并发租号（省钱硬停需 flood_block_new_sends=true）",
                    )
                    raise
                except RequiredPushTokenMissingError as push_ex:
                    await cls._refund_and_revoke_channel(
                        sms_svc, act_id, task_id, manager, "PUSH_TOKEN_MISSING"
                    )
                    act_id = None
                    await cls._disconnect_client_quiet(client)
                    client = None
                    cls._discard_incomplete_session(session_path, meta_path)
                    session_path = None
                    meta_path = None
                    last_failure_reason = "PUSH_TOKEN_MISSING"
                    if not hunt_enabled:
                        raise
                    await manager.append_log(
                        task_id,
                        f"[猎号] {push_ex}，换号继续 ({attempt_idx}/{max_attempts})"
                    )
                    manager.update_task_status(task_id, "running")
                    continue
                except (PhoneNumberFloodError, FloodWaitError) as flood_ex:
                    sec = int(getattr(flood_ex, "seconds", 0) or 0)
                    cls._trip_flood_window(
                        reason="FLOOD_WAIT",
                        seconds=sec,
                        hard=sec >= HUNT_FLOOD_ABORT_SECONDS,
                        api_id=(profile or {}).get("api_id"),
                        config=config,
                    )
                    await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "FLOOD_WAIT")
                    act_id = None
                    await cls._disconnect_client_quiet(client)
                    client = None
                    cls._discard_incomplete_session(session_path, meta_path)
                    session_path = None
                    meta_path = None
                    last_failure_reason = "FLOOD_WAIT"
                    hunt_app_streak = 0
                    if bool(getattr(config, "flood_rotate_push_token", True)) and (push_token or push_task_id):
                        await manager.append_log(
                            task_id,
                            f"[Expert] FLOOD → Push Token 冷却换发 "
                            f"({cls._log_push_token_slot(push_token, delivery_plan)})"
                        )
                        await cls._refund_push_token(
                            bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                            phone, task_id, manager, "FLOOD_WAIT", retire=True,
                        )
                        push_token = None
                        push_task_id = None
                        push_provider = None
                        push_token_obtained_at = None
                    if not hunt_enabled:
                        raise
                    if sec >= HUNT_FLOOD_ABORT_SECONDS:
                        # 小时级频控意味着这条出口/设备组合已经被打死，继续扫只会白烧号
                        hunt_stop_reason = "HUNT_FLOOD_ABORT"
                        await manager.append_log(
                            task_id,
                            f"❌ [猎号] FLOOD_WAIT {sec}s ≥ {HUNT_FLOOD_ABORT_SECONDS}s，"
                            "判定该出口/设备组合已被重度限流，终止猎号任务"
                        )
                        break
                    active_proxy, rotated = await cls._rotate_hunt_proxy(
                        config=config,
                        target_country=target_country,
                        task_id=task_id,
                        manager=manager,
                        current_proxy=active_proxy,
                        proxy_mode=proxy_mode,
                        reason=f"FLOOD_WAIT {sec}s 强制换出口",
                        proxy_override=proxy_override,
                        proxy_id=proxy_id,
                    )
                    if not rotated:
                        # 换不到新出口就继续用同一个 IP 撞频控，只会把号和 Token 一起烧掉
                        hunt_stop_reason = "HUNT_FLOOD_NO_PROXY"
                        await manager.append_log(
                            task_id,
                            f"❌ [猎号] FLOOD_WAIT {sec}s 且无法轮换出口，终止猎号任务"
                            "（请在代理池补充可用于注册的同国节点后重试）"
                        )
                        break
                    proxy_send_uses = 0
                    # 等待完整 FLOOD 窗，禁止 30s 短退避后再发（那会把窗口填满）
                    backoff = max(float(sec), 0.0)
                    if backoff > 0:
                        await manager.append_log(
                            task_id,
                            f"[猎号] FLOOD_WAIT {sec}s，换出口后等待完整窗口 {backoff:.0f}s 再换号 "
                            f"({attempt_idx}/{max_attempts})"
                        )
                        await asyncio.sleep(backoff)
                    manager.update_task_status(task_id, "running")
                    continue
                except PhoneNumberBannedError:
                    if phone:
                        BannedPhonesCache.remember(
                            phone,
                            reason="PHONE_NUMBER_BANNED",
                            source=SOURCE_TELEGRAM_RPC,
                            country=target_country,
                        )
                    await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "PHONE_NUMBER_BANNED")
                    if check_id:
                        await bypass_svc.report_result(check_id, aid, "BANNED")
                    act_id = None
                    check_id = None
                    phone_disposed = True
                    await cls._disconnect_client_quiet(client)
                    client = None
                    cls._discard_incomplete_session(session_path, meta_path)
                    session_path = None
                    meta_path = None
                    hunt_blacklisted += 1
                    hunt_app_streak = 0
                    last_failure_reason = "PHONE_NUMBER_BANNED"
                    if hunt_enabled:
                        proxy_send_uses += 1
                        device_send_uses += 1
                        await manager.append_log(
                            task_id,
                            f"[猎号] PHONE_NUMBER_BANNED 号码 {phone} 已拉黑退订，"
                            f"换号继续 ({attempt_idx}/{max_attempts})"
                        )
                        manager.update_task_status(task_id, "running")
                        continue
                    raise
                except HUNT_SWAPPABLE_SEND_ERRORS as swap_ex:
                    # 平台给了个 Telegram 根本不认的号（PHONE_NUMBER_INVALID 等）：
                    # 这是单个号码的问题，换号就能继续，绝不能因此作废剩余取号预算。
                    reason = HUNT_SWAPPABLE_SEND_ERROR_REASONS.get(
                        type(swap_ex), "PHONE_NUMBER_UNUSABLE"
                    )
                    if phone:
                        # 号码格式/归属本身无效，是永久事实，按 banned 永久收录
                        BannedPhonesCache.remember(
                            phone,
                            reason=reason,
                            source=SOURCE_TELEGRAM_RPC,
                            country=target_country,
                            note="auth.sendCode 判定号码无效",
                        )
                    await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, reason)
                    if check_id:
                        await bypass_svc.report_result(check_id, aid, reason)
                    act_id = None
                    check_id = None
                    phone_disposed = True
                    await cls._disconnect_client_quiet(client)
                    client = None
                    cls._discard_incomplete_session(session_path, meta_path)
                    session_path = None
                    meta_path = None
                    hunt_blacklisted += 1
                    hunt_app_streak = 0
                    last_failure_reason = reason
                    if hunt_enabled:
                        proxy_send_uses += 1
                        device_send_uses += 1
                        await manager.append_log(
                            task_id,
                            f"[猎号] {reason} 号码 {phone} 已拉黑退订，"
                            f"换号继续 ({attempt_idx}/{max_attempts})，Push Token 保持复用"
                        )
                        manager.update_task_status(task_id, "running")
                        continue
                    raise

                hunt_app_streak = 0

                # 猎号收码保底：宁可放弃这枚 Token 的退款，也不能把真 SMS 号的
                # OTP 窗口截到收不到码（上面已在进轮前轮换过老 Token，这里只兜底）
                capped_attempts = cls._sms_poll_attempts_for_push_window(
                    sms_poll_attempts,
                    push_provider,
                    push_token_obtained_at,
                    min_attempts=HUNT_MIN_SMS_POLL_ATTEMPTS if hunt_enabled else 1,
                )
                if capped_attempts < sms_poll_attempts:
                    elapsed = (
                        time.monotonic() - push_token_obtained_at
                        if push_token_obtained_at is not None else 0.0
                    )
                    await manager.append_log(
                        task_id,
                        f"[REGHelp 退款] 短信轮询由 {sms_poll_attempts} 次截断为 {capped_attempts} 次"
                        f"（Token 已签发 {elapsed:.0f}s，需在 {int(PUSH_REFUND_WINDOW_SECONDS)}s 内 setStatus）"
                    )
                sms_poll_attempts = capped_attempts
                phone_code_hash = sent_code.phone_code_hash
                if max_attempts > 1:
                    await manager.append_log(
                        task_id,
                        f"[猎号] 第 {attempt_idx} 次号码进入短信/OTP 通道，停止扫号，继续完成注册"
                    )
                entered_otp_stage = True
                break

            if not entered_otp_stage:
                # 猎号每一轮都被闸门/失败分支拦下（或被 FLOOD/取消提前收住）：
                # 统一在这里收尾，给出可读的扫尽终态，而不是把最后一次的原因当成全局结论
                await cls._finalize_exhausted_hunt(
                    task_id=task_id,
                    manager=manager,
                    bypass_svc=bypass_svc,
                    push_token=push_token,
                    push_task_id=push_task_id,
                    push_provider=push_provider,
                    push_token_obtained_at=push_token_obtained_at,
                    phone=phone,
                    attempts_used=attempt_idx,
                    max_attempts=max_attempts,
                    scanned=hunt_scanned,
                    blacklisted=hunt_blacklisted,
                    last_failure_reason=last_failure_reason,
                    stop_reason=hunt_stop_reason,
                )
                return

            # 7. 异步等待带外挑战证明
            await manager.append_log(task_id, "正在等待带外遥测通道下发瞬时挑战证明 (OTP)...")
            sms_code = await sms_svc.wait_for_code(
                act_id,
                max_attempts=sms_poll_attempts,
                log_callback=lambda msg: manager.append_log(task_id, msg)
            )
            await manager.append_log(task_id, f"带外挑战证明获取成功: {sms_code}")

            # 8. 状态机迁移与鉴权验证：新号 SignUp 与已存在旧号 SignIn 明确分离
            auth_result = None
            needs_signup = False
            account_kind = "unknown"
            existing_2fa_password = None

            try:
                auth_result = await client(functions.auth.SignInRequest(
                    phone_number=phone,
                    phone_code_hash=phone_code_hash,
                    phone_code=sms_code
                ))
                if isinstance(auth_result, types.auth.AuthorizationSignUpRequired):
                    needs_signup = True
            except (PhoneNumberUnoccupiedError, Exception) as e:
                err_str = str(e)
                if "SignUpRequired" in err_str or isinstance(e, (PhoneNumberUnoccupiedError, types.auth.AuthorizationSignUpRequired)):
                    needs_signup = True
                elif isinstance(e, SessionPasswordNeededError):
                    account_kind = "existing_2fa"
                    existing_2fa_password = config.default_2fa_password
                    await manager.append_log(
                        task_id,
                        "检测到已注册旧号且已启用 2FA（SignIn 触发 SessionPasswordNeeded），"
                        "使用已配置的二级口令完成认证，不走 SignUp。"
                    )
                    manager.update_task_status(
                        task_id, "running", account_kind=account_kind, needs_signup=False
                    )
                    # 旧号已有 2FA：本次 Push Token 对新号验证已无意义，尝试触发 REGHelp 退款
                    await cls._refund_push_token(
                        bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                        phone, task_id, manager, "existing_2fa",
                    )
                    auth_result = await client.sign_in(password=config.default_2fa_password)
                else:
                    raise e

            if needs_signup:
                account_kind = "new"
                first_name, last_name = cls._get_random_name(target_country)
                await manager.append_log(
                    task_id,
                    f"状态机判定为新号（SignUpRequired / needs_signup=True），"
                    f"注入合成身份属性: {first_name} {last_name}"
                )
                manager.update_task_status(
                    task_id, "running", account_kind=account_kind, needs_signup=True
                )

                reg_result = await client(functions.auth.SignUpRequest(
                    phone_number=phone,
                    phone_code_hash=phone_code_hash,
                    first_name=first_name,
                    last_name=last_name
                ))

                if hasattr(reg_result, 'terms_of_service') and reg_result.terms_of_service:
                    await client(functions.help.AcceptTermsOfServiceRequest(
                        id=reg_result.terms_of_service.id
                    ))
                auth_result = reg_result
            elif account_kind != "existing_2fa":
                account_kind = "existing_no_2fa"
                await manager.append_log(
                    task_id,
                    "检测到已注册旧号且无 2FA（SignIn 成功，无需 SignUp），"
                    "跳过新号初始化，仅同步已有节点状态。"
                )
                manager.update_task_status(
                    task_id, "running", account_kind=account_kind, needs_signup=False
                )

            user = auth_result.user if hasattr(auth_result, 'user') else auth_result
            user_id = user.id if hasattr(user, 'id') else 0
            await manager.append_log(
                task_id,
                f"虚拟节点状态机初始化成功! 节点 UID: {user_id}, 句柄: {phone}, 账号类型: {account_kind}"
            )

            # 9. 附加二级密码学状态保护 (Secondary State Lock / 2FA)
            # set_2fa 请求字段覆盖 config.auto_set_2fa；旧号若已有口令则传入 current_password
            two_fa_set = False
            should_set_2fa = cls._should_set_2fa(config, set_2fa)
            if should_set_2fa and config.default_2fa_password:
                try:
                    await manager.append_log(
                        task_id,
                        f"启用二级密码学状态保护: {config.default_2fa_password[:3]}***"
                        + ("（已传入 current_password）" if existing_2fa_password else "")
                        + (f"（set_2fa 覆盖={set_2fa}）" if set_2fa is not None else "")
                    )
                    await client.edit_2fa(**cls._edit_2fa_kwargs(
                        config.default_2fa_password,
                        current_password=existing_2fa_password,
                    ))
                    two_fa_set = True
                    await manager.append_log(task_id, "二级密码学状态锁已成功锁定")
                except Exception as e:
                    await manager.append_log(task_id, f"配置二级状态锁跳过或提示: {e}")
            elif set_2fa is False:
                await manager.append_log(task_id, "请求显式关闭 set_2fa，跳过二级密码设定")

            # 10. 首屏遥测与长连接保活同步
            try:
                await client.get_dialogs(limit=5)
                await manager.append_log(task_id, "节点状态机完成全量就绪，首屏状态遥测已同步")
            except Exception:
                pass

            # 11. 序列化持久化密码学快照
            session_meta = {
                "phone": phone,
                "user_id": user_id,
                "country": target_country,
                "secondary_state_key": config.default_2fa_password if two_fa_set else None,
                "two_fa_password": config.default_2fa_password if two_fa_set else None,
                "app_id": profile["api_id"],
                "app_hash": profile["api_hash"],
                "device_model": profile["device_model"],
                "system_version": profile["system_version"],
                "app_version": profile["app_version"],
                "registered_at": datetime.datetime.now().isoformat()
            }
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(session_meta, mf, ensure_ascii=False, indent=2)

            # 12. 终结带外挑战并上报状态
            await sms_svc.finish(act_id)
            if check_id:
                await bypass_svc.report_result(check_id, aid, "REGISTERED")

            try:
                from backend.app.services.push_token_vault import PushTokenVault

                if push_task_id:
                    PushTokenVault.get_instance().mark_success(
                        reghelp_task_id=push_task_id, lease_task_id=task_id
                    )
            except Exception:
                pass

            manager.update_task_status(task_id, "success", phone=phone, user_id=user_id)

        except NoNumberAvailableError as ex:
            err = str(ex) or format_no_number_message(target_country)
            await manager.append_log(task_id, err)
            if lease_max_price is None:
                await manager.append_log(
                    task_id,
                    "提示: 热门/稀缺国家存在动态竞价。可在「参数拓扑」设置 sms_max_price"
                    "（如伊拉克 IQ 美元账户建议 0.55~1.0，卢布账户按网页价填写）"
                    "以匹配网页端高优先级现卡。"
                )
            else:
                from backend.app.models.schemas import format_sms_max_price

                bid_label = format_sms_max_price(lease_max_price) or str(lease_max_price)
                await manager.append_log(
                    task_id,
                    f"提示: 当前最高出价 {bid_label}（按账户结算币种）仍未匹配到现卡，"
                    "可继续上调 sms_max_price 后重试。"
                )
            manager.update_task_status(task_id, "failed", error=err, no_number=True)
        except PhoneNumberBannedError:
            err = f"通信句柄 {phone} 处于服务端拒绝服务状态 (PHONE_NUMBER_BANNED)"
            await manager.append_log(task_id, f"❌ {err}")
            if phone and not phone_disposed:
                BannedPhonesCache.remember(
                    phone,
                    reason="PHONE_NUMBER_BANNED",
                    source=SOURCE_TELEGRAM_RPC,
                    country=target_country,
                )
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "PHONE_NUMBER_BANNED")
            await cls._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                phone, task_id, manager, "PHONE_NUMBER_BANNED",
            )
            if check_id: await bypass_svc.report_result(check_id, aid, "BANNED")
            manager.update_task_status(task_id, "failed", error=err)
        except ApiIdPublishedFloodError:
            cls._trip_flood_window(
                reason="API_ID_PUBLISHED_FLOOD",
                seconds=0,
                hard=True,
                api_id=(profile or {}).get("api_id"),
                config=config,
            )
            attached = bool(push_token)
            err = (
                f"当前 api_id={profile.get('api_id')} 已被 Telegram 判定为公开泄露 ID，"
                + (
                    f"本次已 attach REGHelp Push Token（len={len(push_token)}），"
                    "服务端仍返回 API_ID_PUBLISHED_FLOOD。Token 未被接受为合法平台签署凭证。"
                    if attached else
                    "在缺少合法 Push Token 的情况下触发 API_ID_PUBLISHED_FLOOD (SendCodeRequest)。"
                    "请修复 Attestation 网关或改用自建非泄露 api_id。"
                )
            )
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "API_ID_PUBLISHED_FLOOD")
            await cls._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                phone, task_id, manager, "API_ID_PUBLISHED_FLOOD", retire=True,
            )
            if check_id: await bypass_svc.report_result(check_id, aid, "API_ID_PUBLISHED_FLOOD")
            manager.update_task_status(task_id, "failed", error=err)
        except RequiredPushTokenMissingError as ex:
            err = str(ex)
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "PUSH_TOKEN_MISSING")
            await cls._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                phone, task_id, manager, "PUSH_TOKEN_MISSING", retire=True,
            )
            if check_id: await bypass_svc.report_result(check_id, aid, "PUSH_TOKEN_MISSING")
            manager.update_task_status(task_id, "failed", error=err)
        except DeviceAlignmentError as ex:
            err = str(ex)
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "DEVICE_ALIGNMENT_REJECTED")
            manager.update_task_status(task_id, "failed", error=err)
        except (PhoneNumberFloodError, FloodWaitError) as e:
            sec = getattr(e, 'seconds', 0)
            cls._trip_flood_window(
                reason="FLOOD_WAIT",
                seconds=float(sec or 0),
                hard=int(sec or 0) >= HUNT_FLOOD_ABORT_SECONDS,
                api_id=(profile or {}).get("api_id"),
                config=config,
            )
            err = f"触发协议频控与退避限流，需等待 {sec} 秒 (FLOOD_WAIT)"
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "FLOOD_WAIT")
            await cls._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                phone, task_id, manager, "FLOOD_WAIT",
            )
            if check_id: await bypass_svc.report_result(check_id, aid, "FLOOD_WAIT")
            manager.update_task_status(task_id, "failed", error=err)
        except (PhoneCodeInvalidError, PhoneCodeExpiredError, PhoneCodeEmptyError) as e:
            err = f"带外挑战证明校验失败或已过期: {str(e)}"
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "WRONG_CODE")
            if check_id: await bypass_svc.report_result(check_id, aid, "WRONG_CODE")
            manager.update_task_status(task_id, "failed", error=err)
        except SentCodeAppDeliveryError as ex:
            reason = getattr(ex, "reason", None) or "SENT_CODE_TYPE_APP"
            note = CHANNEL_FAIL_NOTES.get(reason, reason)
            err = f"{note} ({reason}): {str(ex) or repr(ex)}"
            await manager.append_log(task_id, f"❌ {err}")
            if phone and not phone_disposed:
                ttl_hours = hunt_limits["app_blacklist_ttl_hours"]
                record = BannedPhonesCache.remember(
                    phone,
                    reason=reason,
                    source=SOURCE_SENT_CODE,
                    country=target_country,
                    category=CATEGORY_APP_DELIVERY,
                    note=note,
                    ttl_hours=ttl_hours,
                )
                await manager.append_log(
                    task_id,
                    f"⚠️ {note}：号码 {phone} 临时拉黑至 "
                    f"{cls._app_blacklist_expiry_label(record, ttl_hours)}"
                    f"（分类 {CATEGORY_APP_DELIVERY}，TTL {ttl_hours:.0f}h，到期自动放回可试池）"
                )
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, reason)
            await cls._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                phone, task_id, manager, reason,
            )
            if check_id: await bypass_svc.report_result(check_id, aid, reason)
            manager.update_task_status(task_id, "failed", error=err)
        except TimeoutError as ex:
            err = f"等待带外挑战证明超时 (NO_CODE): {str(ex) or repr(ex)}"
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "NO_CODE")
            await cls._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                phone, task_id, manager, "NO_CODE",
            )
            if check_id: await bypass_svc.report_result(check_id, aid, "NO_CODE")
            manager.update_task_status(task_id, "failed", error=err)
        except RecaptchaChallengeError as ex:
            err = f"RECAPTCHA_CHECK 人机挑战未能突破: {str(ex) or repr(ex)}"
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "RECAPTCHA_CHECK")
            await cls._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                phone, task_id, manager, "RECAPTCHA_CHECK",
            )
            if check_id: await bypass_svc.report_result(check_id, aid, "RECAPTCHA_CHECK")
            manager.update_task_status(task_id, "failed", error=err)
        except Exception as ex:
            parsed = parse_recaptcha_check(ex)
            reason = "RECAPTCHA_CHECK" if parsed else "EXCEPTION"
            err = f"状态机引导流程异常: {str(ex) or repr(ex)}"
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, reason)
            await cls._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                phone, task_id, manager, reason,
            )
            if check_id: await bypass_svc.report_result(check_id, aid, "NO_CODE")
            manager.update_task_status(task_id, "failed", error=err)
        finally:
            # 任务终结（成功 / 失败 / 取消 / 异常）必须归还 Push Token 租约，
            # 否则这枚 Token 要等到 LEASE_TTL_SECONDS 兜底过期才能被别的任务复用
            cls._release_push_token_leases(task_id)
            await cls._release_registration_resources(client, sms_svc, bypass_svc)

    @classmethod
    async def run_batch(
        cls,
        batch_id: str,
        task_ids: List[str],
        country: Optional[str] = None,
        app_type: Optional[str] = None,
        proxy_override: Optional[Dict[str, Any]] = None,
        set_2fa: Optional[bool] = None,
        concurrency: int = 3,
        proxy_id: Optional[str] = None,
        proxy_mode: str = "custom_pool",
        sms_provider: Optional[str] = None,
        max_price: Optional[float] = None,
        max_number_attempts: Optional[int] = None,
        no_number_retries: Optional[int] = None,
        provider_ids: Optional[List[str]] = None,
    ) -> None:
        """使用 Semaphore 异步并行调度一批虚拟节点引导任务。"""
        from backend.app.services.proxy_slot_pool import (
            fail_batch_tasks_no_proxy,
            prepare_batch_proxy_pool,
        )

        manager = RegistrationTaskManager.get_instance()
        config = ConfigManager.get_instance().config
        target_country = (country or config.target_country or "").lower()
        limit = max(BATCH_CONCURRENCY_MIN, min(int(concurrency or 1), BATCH_CONCURRENCY_MAX))

        slot_pool = None
        if not proxy_override and not proxy_id:
            slot_pool, pool_limit, pool_logs = await prepare_batch_proxy_pool(
                batch_id=batch_id,
                country=target_country,
                slots=limit,
                config=config,
                proxy_mode=proxy_mode,
            )
            if pool_logs and slot_pool is None and pool_limit == 0:
                await fail_batch_tasks_no_proxy(task_ids, manager, pool_logs)
                with manager._lock:
                    if batch_id in manager.batches:
                        manager.batches[batch_id]["status"] = "failed"
                        manager.batches[batch_id]["updated_at"] = datetime.datetime.now().isoformat()
                return
            if slot_pool is not None:
                limit = max(BATCH_CONCURRENCY_MIN, min(pool_limit, limit))
                for tid in task_ids:
                    for line in pool_logs:
                        await manager.append_log(tid, line)

        sem = asyncio.Semaphore(limit)
        with manager._lock:
            if batch_id in manager.batches:
                manager.batches[batch_id]["status"] = "running"
                manager.batches[batch_id]["updated_at"] = datetime.datetime.now().isoformat()

        async def _run_one(tid: str) -> None:
            # 批次已被取消时不再排队等槽位，也不占用代理槽（否则会白租一轮号）
            if manager.cancel_requested(tid):
                await manager.append_log(tid, "[取消] 批次已取消，跳过本任务的槽位排队")
                manager.update_task_status(
                    tid, "canceled", error="HUNT_CANCELED: 批次取消，未消耗号码与 Push Token"
                )
                return
            async with sem:
                if manager.cancel_requested(tid):
                    await manager.append_log(tid, "[取消] 取得槽位后发现批次已取消，立即释放")
                    manager.update_task_status(
                        tid, "canceled", error="HUNT_CANCELED: 批次取消，未消耗号码与 Push Token"
                    )
                    return
                flood_stop = await cls._respect_flood_window(tid, manager, config=config)
                if flood_stop:
                    manager.update_task_status(
                        tid,
                        "failed",
                        error=(
                            f"{flood_stop}: flood_block_new_sends=开，冷却中跳过本任务租号/发码"
                            "（未 cancel 其它已开跑任务；默认不拦新测试）"
                        ),
                    )
                    return
                await manager.append_log(
                    tid,
                    f"[批量编排] batch_id={batch_id} 取得并发槽位 (concurrency={limit})，开始引导"
                )
                task_proxy = proxy_override
                leased = False
                if slot_pool is not None:
                    try:
                        task_proxy = await slot_pool.acquire(tid)
                        leased = True
                    except Exception as exc:
                        await manager.append_log(tid, f"[代理槽位] 获取失败: {exc}")
                        manager.update_task_status(tid, "failed", message=str(exc))
                        return
                try:
                    await cls.run_registration(
                        task_id=tid,
                        country=country,
                        app_type=app_type,
                        proxy_override=task_proxy,
                        set_2fa=set_2fa,
                        proxy_id=proxy_id,
                        proxy_mode=proxy_mode,
                        sms_provider=sms_provider,
                        max_price=max_price,
                        max_number_attempts=max_number_attempts,
                        no_number_retries=no_number_retries,
                        provider_ids=provider_ids,
                    )
                finally:
                    if slot_pool is not None and leased and task_proxy:
                        await slot_pool.release(task_proxy, tid)

        await asyncio.gather(*[_run_one(tid) for tid in task_ids], return_exceptions=True)
        with manager._lock:
            if batch_id in manager.batches:
                manager.batches[batch_id]["updated_at"] = datetime.datetime.now().isoformat()


NodeProvisioningOrchestrator = RegistrationOrchestrator
