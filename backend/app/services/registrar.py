import asyncio
import json
import random
import logging
import threading
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
from backend.app.services.device_profile import DeviceProfileManager
from backend.app.services.vaksms import NoNumberAvailableError, VakSmsService, format_no_number_message
from backend.app.services.grizzlysms import GrizzlySmsService, PROVIDER_LABEL as GRIZZLY_PROVIDER_LABEL
from backend.app.services.attestation_gateway import AttestationGatewayService
from backend.app.services.banned_phones import (
    LOCAL_BANNED_REASON,
    SOURCE_ANTISAFETY,
    SOURCE_TELEGRAM_RPC,
    BannedPhonesCache,
)
from backend.app.services.phone_precheck import (
    CLEAN_LOG_TEMPLATE,
    DEGRADE_LOG_TEMPLATE,
    PRECHECK_ALREADY_REGISTERED,
    PhonePrecheckService,
    format_precheck_intercept_log,
)
from backend.app.services.recaptcha_check import (
    RecaptchaChallengeError,
    parse_recaptcha_check,
)

logger = logging.getLogger("NodeProvisioningOrchestrator")

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

CONNECT_TIMEOUT_SECONDS = 25.0
MAX_RETAINED_TASKS = 200
TERMINAL_TASK_STATUSES = frozenset({"success", "failed", "filtered"})
MAX_RESEND_WAIT_SECONDS = 90.0
DEFAULT_SMS_POLL_ATTEMPTS = 30
FAST_FAIL_SMS_POLL_ATTEMPTS = 3
BATCH_COUNT_MIN = 1
BATCH_COUNT_MAX = 10
BATCH_CONCURRENCY_MIN = 1
BATCH_CONCURRENCY_MAX = 10

SMS_PROVIDER_ALIASES = {
    "grizzly": "grizzlysms",
    "grizzlysms": "grizzlysms",
    "grizzly_sms": "grizzlysms",
    "grizzly-sms": "grizzlysms",
    "vak": "vaksms",
    "vaksms": "vaksms",
    "vak_sms": "vaksms",
    "vak-sms": "vaksms",
}
SMS_PROVIDER_LABELS = {
    "grizzlysms": GRIZZLY_PROVIDER_LABEL,
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


class SentCodeAppDeliveryError(Exception):
    """服务端将验证码下发到已登录官方客户端，带外短信网关无法接收。"""

    def __init__(self, message: str, reason: str = "SENT_CODE_TYPE_APP"):
        super().__init__(message)
        self.reason = reason


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
            elif status == "running":
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

    def list_tasks(self, batch_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            snapshot = list(self.tasks.values())
        if batch_id:
            snapshot = [item for item in snapshot if item.get("batch_id") == batch_id]
        return sorted(snapshot, key=lambda x: x["created_at"], reverse=True)

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
            return "grizzlysms"
        compact = token.replace("-", "").replace("_", "")
        return SMS_PROVIDER_ALIASES.get(token) or SMS_PROVIDER_ALIASES.get(compact) or "grizzlysms"

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
        return GrizzlySmsService(getattr(config, "grizzly_sms_api_key", "") or "")

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
        if label:
            return str(label)
        name = getattr(sms_svc, "PROVIDER_NAME", None) or provider
        return SMS_PROVIDER_LABELS.get(cls.normalize_sms_provider(name), SMS_PROVIDER_LABELS["grizzlysms"])

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
        detail = result.get("error") or result.get("data") or "unknown"
        await manager.append_log(
            task_id,
            f"⚠️ 自动退订/撤销信道句柄未成功 (act_id={act_id}, 原因: {reason}): {detail}"
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
    ) -> bool:
        """租号后最先检查本地已确认封禁库。命中则立即退订，不消耗 Push Token。

        返回 True 表示可以继续；False 表示已拦截并退订，调用方必须立即 return。
        """
        service = cache or BannedPhonesCache
        record = service.lookup(phone)
        if not record:
            return True
        await manager.append_log(
            task_id,
            f"[本地封禁库拦截] 通信句柄 {phone} 已在 banned_phones_cache 中 "
            f"(原因={record.reason}, 来源={record.source}, 命中={record.hits}次)，"
            "跳过白号预检 / Push Token / auth.sendCode，直接撤销退订换号",
        )
        await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, LOCAL_BANNED_REASON)
        manager.update_task_status(
            task_id,
            "filtered",
            error=f"{LOCAL_BANNED_REASON}: 号码 {phone} 已被本机确认为 {record.reason}",
            phone=phone,
            banned_cache_hit=True,
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
    ) -> bool:
        """租号后、申请 Push Token 之前做白号预检。

        返回 True 表示可以继续注册流水线；False 表示已拦截并退订，调用方必须立即 return。
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
            await cls._refund_and_revoke_channel(
                sms_svc, act_id, task_id, manager, PRECHECK_ALREADY_REGISTERED
            )
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
            if result.reason in {"PRECHECK_NO_PROBE_SESSION", "PRECHECK_DISABLED", ""}:
                degrade_msg = DEGRADE_LOG_TEMPLATE
            else:
                degrade_msg = f"⚠️ 预检未得到明确结论 ({result.reason})，优雅降级走现有流程"
            await manager.append_log(task_id, degrade_msg)
            manager.update_task_status(task_id, "running", precheck_intercepted=False)
            return True
        await manager.append_log(task_id, CLEAN_LOG_TEMPLATE.format(phone=phone))
        manager.update_task_status(task_id, "running", precheck_intercepted=False)
        return True

    @classmethod
    async def _resolve_custom_proxy(
        cls,
        config,
        target_country: str,
        task_id: str,
        manager: RegistrationTaskManager,
    ) -> Optional[Dict[str, Any]]:
        """优先从用户自建代理池按目标国家匹配节点。"""
        from backend.app.services.proxy_manager import custom_pool_summary, select_proxy_for_registration
        from backend.app.services.proxyseller import format_proxy_endpoint

        if not getattr(config, "custom_proxies", None):
            return None
        summary = custom_pool_summary(target_country)
        if not summary.get("total"):
            return None
        chosen = select_proxy_for_registration(target_country)
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
            is_static_residential,
        )

        await manager.append_log(
            task_id,
            f"[多径中继网关] 正在检索 {target_country.upper()} 区域代理（自建池 + API + 内置静态住宅池）..."
        )
        ps_svc = ProxySellerService(config.proxy_seller_key)
        try:
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
                "（API / 静态住宅 / 自建池均无匹配节点）。"
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
    ) -> bool:
        """带超时的 Telethon connect；超时则标记失败并自动退款，避免任务挂起。"""
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
    def _build_code_settings(push_token: Optional[str] = None) -> types.CodeSettings:
        """构造 auth.sendCode 的 CodeSettings。

        Telethon CodeSettings._bytes 要求 token 与 app_sandbox 同真或同假：
        有 Push Token 时必须显式传入 app_sandbox（非沙盒为 False）；
        无 Token 时两者都必须保持 False-y（None）。
        token 传 str 即可，Telethon 会走 serialize_bytes。
        """
        return types.CodeSettings(
            allow_flashcall=False,
            current_number=False,
            allow_app_hash=True,
            allow_missed_call=False,
            token=push_token if push_token else None,
            app_sandbox=False if push_token else None,
        )

    @staticmethod
    def _tl_type_name(obj: Any) -> str:
        if obj is None:
            return ""
        return type(obj).__name__

    @classmethod
    def _is_app_delivery(cls, sent_code: Any) -> bool:
        return cls._tl_type_name(getattr(sent_code, "type", None)) in APP_DELIVERY_TYPE_NAMES

    @classmethod
    def _is_sms_delivery(cls, sent_code: Any) -> bool:
        name = cls._tl_type_name(getattr(sent_code, "type", None))
        if name in SMS_DELIVERY_TYPE_NAMES:
            return True
        return bool(name) and "Sms" in name and "App" not in name

    @classmethod
    def _next_type_is_sms(cls, sent_code: Any) -> bool:
        name = cls._tl_type_name(getattr(sent_code, "next_type", None))
        if not name:
            return False
        return name in SMS_NEXT_TYPE_NAMES or "Sms" in name

    @classmethod
    def _describe_sent_code(cls, sent_code: Any) -> str:
        type_name = cls._tl_type_name(getattr(sent_code, "type", None)) or "Unknown"
        next_name = cls._tl_type_name(getattr(sent_code, "next_type", None)) or "None"
        timeout = getattr(sent_code, "timeout", None)
        timeout_text = str(timeout) if timeout is not None else "None"
        return f"type={type_name} next_type={next_name} timeout={timeout_text}"

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
    async def resolve_sent_code_channel(
        cls,
        client,
        phone: str,
        sent_code: Any,
        task_id: str,
        manager: RegistrationTaskManager,
        wait_timeout: Optional[float] = None,
    ) -> Tuple[Any, int]:
        """解析 sendCode 分发通道；SentCodeTypeApp 时尝试 ResendCode 降级到短信。

        返回 (effective_sent_code, sms_poll_attempts)。
        若无法降级到可被带外短信网关接收的通道，抛出 SentCodeAppDeliveryError 以快速退订。
        """
        delivery_name = cls._tl_type_name(getattr(sent_code, "type", None)) or "Unknown"
        await manager.append_log(
            task_id,
            f"挑战已由服务端下发! 分发通道类型: {delivery_name} ({cls._describe_sent_code(sent_code)})"
        )

        if not cls._is_app_delivery(sent_code):
            if cls._is_sms_delivery(sent_code):
                await manager.append_log(task_id, "分发通道为运营商短信，带外遥测网关可正常接收")
            return sent_code, DEFAULT_SMS_POLL_ATTEMPTS

        await manager.append_log(
            task_id,
            "⚠️ 服务端将验证码下发到了已有设备客户端 (SentCodeTypeApp)，带外短信通道大概率无法接收"
        )

        next_name = cls._tl_type_name(getattr(sent_code, "next_type", None))
        has_timeout = getattr(sent_code, "timeout", None) is not None
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
    ):
        """执行单次边缘虚拟节点引导全流程"""
        manager = RegistrationTaskManager.get_instance()
        manager.update_task_status(task_id, "running")

        config = ConfigManager.get_instance().config
        target_country = (country or config.target_country).lower()
        active_app = app_type or config.active_app_type

        resolved_sms_provider = cls.resolve_sms_provider(config, sms_provider)
        sms_svc = cls._create_sms_service(config, resolved_sms_provider)
        lease_max_price = cls.resolve_sms_max_price(config, max_price)

        from backend.app.models.schemas import normalize_proxy_mode
        from backend.app.services.proxy_manager import find_custom_proxy
        from backend.app.services.proxyseller import format_proxy_endpoint

        mode = normalize_proxy_mode(proxy_mode)
        if proxy_id and mode == "custom_pool":
            mode = "explicit"

        # 使用者决定配对关系：explicit 100% 遵从指定节点，不施加隐式国家约束
        active_proxy = proxy_override
        if not active_proxy and (proxy_id or mode == "explicit"):
            if proxy_id:
                found = find_custom_proxy(proxy_id=proxy_id)
                if found:
                    active_proxy = found
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
            and config.use_proxy_seller_auto
        ):
            active_proxy = await cls._resolve_proxy_seller_auto(
                config=config,
                target_country=target_country,
                task_id=task_id,
                manager=manager,
            )

        if not active_proxy:
            active_proxy = config.fallback_proxy.model_dump()
            await manager.append_log(
                task_id,
                f"[多径中继网关] 使用静态后备中继 {active_proxy.get('proxy_type', 'socks5')}://"
                f"{active_proxy.get('addr')}:{active_proxy.get('port')}"
                + (f"（策略={mode}）" if mode == "fallback" else "")
            )

        # 统一 Attestation / Push 凭证高可用网关：按 config.attestation_provider_mode 策略
        # 在 REGHelp 与 AntiSafety 两个独立提供源之间自动选择主备顺序并容灾切换
        bypass_svc = AttestationGatewayService(config, proxy=active_proxy)

        profile = DeviceProfileManager.get_resolved_profile(active_app, target_country)
        aid = profile["aid"]

        act_id = None
        check_id = None
        client = None
        phone = None

        try:
            await manager.append_log(
                task_id,
                f"[接码平台] 当前使用接码通道: {cls._sms_provider_label(sms_svc, resolved_sms_provider)}"
            )
            await manager.append_log(task_id, f"选定端点模板: {profile['name']} (AID: {aid})")
            pack_alias = profile.get("device_pack_alias")
            pack_country = (profile.get("device_pack_country") or "").upper()
            pack_match = profile.get("device_pack_match") or "none"
            if pack_alias:
                match_label = "国家精确匹配" if pack_match == "country" else "跨库回退采样"
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

            # 1. 租用带外通信句柄（热门/稀缺国家需携带 maxPrice 动态竞价，否则卡在底价空桶）
            if lease_max_price is not None:
                await manager.append_log(
                    task_id,
                    f"正在向带外遥测提供者申请拓扑代码 '{target_country.upper()}' 的信道句柄"
                    f"（动态竞价上限 maxPrice={lease_max_price} RUB）..."
                )
            else:
                await manager.append_log(
                    task_id,
                    f"正在向带外遥测提供者申请拓扑代码 '{target_country.upper()}' 的信道句柄"
                    "（未设置最高出价，使用平台底价；热门国家可能 NO_NUMBERS）..."
                )
            act_id, phone = await sms_svc.get_number(
                country=target_country,
                service="tg",
                max_price=lease_max_price,
            )
            manager.update_task_status(task_id, "running", phone=phone)
            await manager.append_log(task_id, f"成功获取端点通信句柄: {phone} (Session Handle ID: {act_id})")

            # 1.4 本地封禁号缓存：接码回收号复用时零成本拦截
            if not await cls._apply_banned_cache_gate(
                phone=phone,
                act_id=act_id,
                sms_svc=sms_svc,
                task_id=task_id,
                manager=manager,
            ):
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
            ):
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
                    manager.update_task_status(task_id, "failed", error="Endpoint handle pre-audit rejected")
                    return

            # 3. 申请 Attestation Push 握手凭证 (可选增强，若不可达平滑降级至标准协议信道)
            # 通过统一网关在 REGHelp / AntiSafety 两个高可用提供源之间按优先级自动选择与容灾切换
            push_token = None
            push_provider = None
            try:
                await manager.append_log(task_id, "向 Attestation 高可用网关请求平台推送握手凭证 (Signed Push Token)...")
                push_token, push_provider = await bypass_svc.get_push_token(
                    profile,
                    aid=aid,
                    log_callback=lambda msg: manager.append_log(task_id, msg)
                )
                if push_token:
                    await manager.append_log(task_id, f"成功获取平台合规签署的 Attestation Push Token (提供源: {push_provider})")
                else:
                    await manager.append_log(task_id, "⚠️ Attestation Push Token 未返回，回退至标准信道...")
            except Exception as e:
                await manager.append_log(task_id, f"⚠️ Attestation Push 凭证请求跳过/降级 ({e})，自动切换至标准信道模式")

            # 3.1 API 凭证策略裁决 (应对 API_ID_PUBLISHED_FLOOD)
            # 官方内置 api_id (如 6 / 21724) 早年已被公开泄露，Telegram 服务端对其 auth.sendCode
            # 请求执行近乎无差别拦截：若本次未拿到合法 Push Token，几乎必然返回 API_ID_PUBLISHED_FLOOD。
            # 此处按 config.api_credential_mode 策略，在必要时自动切换为用户自建的开发者 api_id/api_hash。
            original_api_id = profile["api_id"]
            profile = DeviceProfileManager.resolve_effective_credentials(profile, config, has_push_token=bool(push_token))
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

            if not await cls._connect_mtproto(client, task_id, manager, sms_svc, act_id):
                return
            await manager.append_log(task_id, "已完成 MTProto 传输层 Diffie-Hellman 密钥交换与加密连接建立")

            # 5. 执行协议端点握手序列
            await cls.perform_handshake(client, profile, task_id, manager)

            # 6. 发起挑战分发请求 (SendCode)
            code_settings = cls._build_code_settings(push_token)

            await manager.append_log(task_id, "调用 auth.sendCode 触发服务端瞬时握手挑战分发...")
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
            )
            phone_code_hash = sent_code.phone_code_hash

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

            manager.update_task_status(task_id, "success", phone=phone, user_id=user_id)

        except NoNumberAvailableError as ex:
            err = str(ex) or format_no_number_message(target_country)
            await manager.append_log(task_id, err)
            if lease_max_price is None:
                await manager.append_log(
                    task_id,
                    "提示: 热门/稀缺国家存在动态竞价。可在「参数拓扑」设置 sms_max_price"
                    "（如伊拉克 IQ 建议 50~80 RUB）以匹配网页端高优先级现卡。"
                )
            else:
                await manager.append_log(
                    task_id,
                    f"提示: 当前最高出价 {lease_max_price} RUB 仍未匹配到现卡，可继续上调 sms_max_price 后重试。"
                )
            manager.update_task_status(task_id, "failed", error=err, no_number=True)
        except PhoneNumberBannedError:
            err = f"通信句柄 {phone} 处于服务端拒绝服务状态 (PHONE_NUMBER_BANNED)"
            await manager.append_log(task_id, f"❌ {err}")
            if phone:
                BannedPhonesCache.remember(
                    phone,
                    reason="PHONE_NUMBER_BANNED",
                    source=SOURCE_TELEGRAM_RPC,
                    country=target_country,
                )
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "PHONE_NUMBER_BANNED")
            if check_id: await bypass_svc.report_result(check_id, aid, "BANNED")
            manager.update_task_status(task_id, "failed", error=err)
        except ApiIdPublishedFloodError:
            err = (
                f"当前 api_id={profile.get('api_id')} 已被 Telegram 判定为公开泄露 ID，"
                "在缺少合法 Push Token 的情况下触发 API_ID_PUBLISHED_FLOOD (SendCodeRequest)。"
                "请在「🔐 凭证库 / 开发者 API」用已有账号申请专属 api_id/api_hash，"
                "或在「全局参数拓扑」填入自建 custom_api_id / custom_api_hash "
                "并将 api_credential_mode 设为 auto 或 custom，"
                "或修复 Attestation 网关连通性以获取合法 Push Token 后重试"
            )
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "API_ID_PUBLISHED_FLOOD")
            if check_id: await bypass_svc.report_result(check_id, aid, "API_ID_PUBLISHED_FLOOD")
            manager.update_task_status(task_id, "failed", error=err)
        except (PhoneNumberFloodError, FloodWaitError) as e:
            sec = getattr(e, 'seconds', 0)
            err = f"触发协议频控与退避限流，需等待 {sec} 秒 (FLOOD_WAIT)"
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "FLOOD_WAIT")
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
            err = f"站内信验证码无法被带外通道接收 ({reason}): {str(ex) or repr(ex)}"
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, reason)
            if check_id: await bypass_svc.report_result(check_id, aid, reason)
            manager.update_task_status(task_id, "failed", error=err)
        except TimeoutError as ex:
            err = f"等待带外挑战证明超时 (NO_CODE): {str(ex) or repr(ex)}"
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "NO_CODE")
            if check_id: await bypass_svc.report_result(check_id, aid, "NO_CODE")
            manager.update_task_status(task_id, "failed", error=err)
        except RecaptchaChallengeError as ex:
            err = f"RECAPTCHA_CHECK 人机挑战未能突破: {str(ex) or repr(ex)}"
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, "RECAPTCHA_CHECK")
            if check_id: await bypass_svc.report_result(check_id, aid, "RECAPTCHA_CHECK")
            manager.update_task_status(task_id, "failed", error=err)
        except Exception as ex:
            parsed = parse_recaptcha_check(ex)
            reason = "RECAPTCHA_CHECK" if parsed else "EXCEPTION"
            err = f"状态机引导流程异常: {str(ex) or repr(ex)}"
            await manager.append_log(task_id, f"❌ {err}")
            await cls._refund_and_revoke_channel(sms_svc, act_id, task_id, manager, reason)
            if check_id: await bypass_svc.report_result(check_id, aid, "NO_CODE")
            manager.update_task_status(task_id, "failed", error=err)
        finally:
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
    ) -> None:
        """使用 Semaphore 异步并行调度一批虚拟节点引导任务。"""
        manager = RegistrationTaskManager.get_instance()
        limit = max(BATCH_CONCURRENCY_MIN, min(int(concurrency or 1), BATCH_CONCURRENCY_MAX))
        sem = asyncio.Semaphore(limit)
        with manager._lock:
            if batch_id in manager.batches:
                manager.batches[batch_id]["status"] = "running"
                manager.batches[batch_id]["updated_at"] = datetime.datetime.now().isoformat()

        async def _run_one(tid: str) -> None:
            async with sem:
                await manager.append_log(
                    tid,
                    f"[批量编排] batch_id={batch_id} 取得并发槽位 (concurrency={limit})，开始引导"
                )
                await cls.run_registration(
                    task_id=tid,
                    country=country,
                    app_type=app_type,
                    proxy_override=proxy_override,
                    set_2fa=set_2fa,
                    proxy_id=proxy_id,
                    proxy_mode=proxy_mode,
                    sms_provider=sms_provider,
                    max_price=max_price,
                )

        await asyncio.gather(*[_run_one(tid) for tid in task_ids], return_exceptions=True)
        with manager._lock:
            if batch_id in manager.batches:
                manager.batches[batch_id]["updated_at"] = datetime.datetime.now().isoformat()


NodeProvisioningOrchestrator = RegistrationOrchestrator
