"""已有账号凭证库 (Account Vault)

扫描 `lod_user/` 与 `data/sessions/` 下的 JSON / Telethon .session 文件，
解析手机号、注册时间、设备型号、app_id/app_hash 等元数据，
并支持将某个账号已有可用的开发者凭证一键写入全局配置。
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.config import ConfigManager, LOD_USER_DIR, SESSIONS_DIR
from backend.app.models.schemas import (
    ApplyVaultCredentialsResponse,
    VaultAccountItem,
    VaultAccountListResponse,
    VaultUploadResponse,
)
from backend.app.services.device_profile import PUBLISHED_API_ID_BLOCKLIST

logger = logging.getLogger("AccountVault")

REPO_ROOT = Path(__file__).resolve().parents[3]

VAULT_GUIDANCE = (
    "本地 lod_user 里现有的 3 个 JSON 只有元数据、没有带 auth_key 的 .session，"
    "记录中的 app_id=4 是公开泄露官方 ID，不能当专属开发者凭证。"
    "可以这样提供/申请专属 api_id / api_hash："
    "【轨 A · 已登录的 Telegram 客户端，哪怕没有 .session】"
    "在「🔐 凭证库 / 开发者 API」填入该账号手机号，点击「从 my.telegram.org 申请专属 API ID/Hash」。"
    "系统会向 777000 官方号发送 Web 登录码，你在手机 Telegram 里查看后填回本页即可自动登录 /apps 完成申请。"
    "【轨 B · 已有 Telethon .session】"
    "在本页顶部「📤 上传账号文件」选择 .zip / .session / .json，或把文件复制到 lod_user/ 与 data/sessions/（最好与 JSON 同名），"
    "刷新凭证库后点申请，系统会静默读取 777000 消息并完成申请。"
    "【轨 C · 已经在 my.telegram.org 申请过】"
    "直接到「⚙️ 参数拓扑」填写 custom_api_id / custom_api_hash，并把 api_credential_mode 设为 auto 或 custom。"
)


def build_apps_apply_hint(account: Dict[str, Any]) -> str:
    phone = account.get("phone") or account.get("filename") or "该账号"
    has_session = bool(account.get("has_session"))
    is_published = bool(account.get("is_published_api_id"))
    app_id = account.get("app_id")
    parts = []
    if is_published:
        parts.append(
            f"记录中的 api_id={app_id} 属于公开泄露官方 ID，不能作为专属开发者凭证写入全局配置。"
        )
    if has_session:
        parts.append(
            f"{phone} 已检测到同名 .session，可在「🔐 凭证库 / 开发者 API」发起 my.telegram.org 登录，"
            "系统将尝试自动读取官方账号 777000 的 Web 登录码；失败时仍可手动提交。"
        )
    else:
        stem = Path(account.get("filename") or "phone.json").stem
        parts.append(
            f"{phone} 仅有 JSON、缺少同名 .session。自动读取登录码需要 Telethon session："
            f"请将 `{stem}.session` 放到与 JSON 相同的目录，或在控制台发起申请后于 Telegram 客户端"
            "查看 Web 登录码并手动提交。"
        )
    parts.append(
        "也可直接在「参数拓扑」填入已有的自建 custom_api_id / custom_api_hash。"
    )
    return "".join(parts)


def _rel_to_repo(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except Exception:
        return str(path)


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """将各类手机号记录规范为 +E.164 形式。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    return f"+{digits}"


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_register_time(value: Any) -> Tuple[Optional[str], Optional[int]]:
    """解析 Unix 时间戳 / ISO 字符串，返回 (可读时间, unix_int)。"""
    if value is None or value == "":
        return None, None
    if isinstance(value, (int, float)):
        ts = int(value)
        # 毫秒时间戳
        if ts > 10_000_000_000:
            ts = int(ts / 1000)
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC"), ts
        except (OverflowError, OSError, ValueError):
            return str(value), ts
    text = str(value).strip()
    if text.isdigit():
        return parse_register_time(int(text))
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            cleaned = text.replace("Z", "+00:00") if text.endswith("Z") else text
            dt = datetime.fromisoformat(cleaned) if "T" in cleaned or "+" in cleaned else datetime.strptime(text, fmt)
            unix = int(dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp())
            return dt.strftime("%Y-%m-%d %H:%M:%S"), unix
        except ValueError:
            continue
    return text, None


def make_account_id(source: str, identity: str) -> str:
    digest = hashlib.sha256(f"{source}:{identity}".encode("utf-8")).hexdigest()
    return digest[:16]


def _looks_like_account_json(data: Dict[str, Any]) -> bool:
    keys = {str(k).lower() for k in data.keys()}
    interesting = {
        "phone", "phone_number", "app_id", "api_id", "app_hash", "api_hash",
        "session_file", "device", "device_model", "user_id", "id",
    }
    return bool(keys & interesting)


def _extract_json_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    phone_raw = (
        data.get("phone")
        or data.get("phone_number")
        or data.get("msisdn")
    )
    app_id = _to_int(data.get("app_id") if data.get("app_id") is not None else data.get("api_id"))
    app_hash = _to_str(data.get("app_hash") or data.get("api_hash"))
    device = _to_str(data.get("device") or data.get("device_model"))
    system_version = _to_str(data.get("sdk") or data.get("system_version"))
    app_version = _to_str(data.get("app_version"))
    user_id = _to_int(data.get("user_id") if data.get("user_id") is not None else data.get("id"))
    register_raw = (
        data.get("register_time")
        or data.get("registered_at")
        or data.get("created_at")
    )
    readable, unix = parse_register_time(register_raw)
    two_fa = data.get("twoFA") or data.get("two_fa_password") or data.get("secondary_state_key")
    return {
        "phone_raw": _to_str(phone_raw),
        "phone": normalize_phone(phone_raw),
        "app_id": app_id,
        "app_hash": app_hash,
        "device_model": device,
        "system_version": system_version,
        "app_version": app_version,
        "user_id": user_id,
        "register_time": readable,
        "register_time_unix": unix,
        "lang_pack": _to_str(data.get("lang_pack")),
        "system_lang_code": _to_str(data.get("system_lang_pack") or data.get("system_lang_code")),
        "has_2fa": bool(two_fa),
        "session_file_hint": _to_str(data.get("session_file")),
    }


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and _looks_like_account_json(data):
            return data
    except Exception as exc:
        logger.warning("跳过无法解析的 JSON: %s (%s)", path, exc)
    return None


def _is_telethon_session(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 64:
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cur.fetchall()}
            return "sessions" in tables
        finally:
            conn.close()
    except Exception:
        return False


def _find_sibling_session(json_path: Path, hint: Optional[str]) -> Optional[Path]:
    candidates: List[Path] = []
    if hint:
        hint_path = Path(hint)
        if not hint_path.suffix:
            hint_path = hint_path.with_suffix(".session")
        candidates.append(json_path.parent / hint_path.name)
        candidates.append(SESSIONS_DIR / hint_path.name)
        if hint_path.is_absolute():
            candidates.append(hint_path)
    candidates.append(json_path.with_suffix(".session"))
    for cand in candidates:
        if cand.exists() and cand.is_file():
            return cand
    return None


def _find_sibling_json(session_path: Path) -> Optional[Path]:
    sibling = session_path.with_suffix(".json")
    if sibling.exists():
        return sibling
    return None


def _iter_files(root: Path, pattern: str) -> List[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(p for p in root.rglob(pattern) if p.is_file())


def _file_mtime_as_register(path: Path) -> Tuple[Optional[str], Optional[int]]:
    try:
        ts = int(path.stat().st_mtime)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC"), ts
    except Exception:
        return None, None


ALLOWED_UPLOAD_EXTS = {".zip", ".session", ".json"}
ALLOWED_ZIP_MEMBER_EXTS = {".session", ".json"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_ZIP_MEMBERS = 200
MAX_ZIP_MEMBER_BYTES = 20 * 1024 * 1024
IMPORTS_SUBDIR = "imports"


def sanitize_upload_filename(filename: Optional[str]) -> str:
    """只保留文件名本身，剔除路径并折叠危险字符。"""
    name = str(filename or "upload.bin").replace("\\", "/").replace("\x00", "")
    name = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (cleaned or "upload.bin")[:180]


def zip_dest_stem(filename: str) -> str:
    stem = Path(sanitize_upload_filename(filename)).stem
    stem = re.sub(r"_+[0-9a-f]{3,8}$", "", stem, flags=re.I)
    return stem or "uploaded_sessions"


def _is_safe_relpath(member: str) -> bool:
    raw = str(member or "").replace("\\", "/").strip()
    if not raw or raw.endswith("/"):
        return False
    if raw.startswith("/") or raw.startswith("../") or "/../" in f"/{raw}/":
        return False
    parts = [p for p in Path(raw).parts if p not in {"", "."}]
    if not parts or any(p == ".." for p in parts):
        return False
    return True


def extract_zip_safely(content: bytes, dest_dir: Path) -> Tuple[List[str], List[str]]:
    """安全解压账号 zip：拒绝 zip-slip、限制体积与成员类型。"""
    imported: List[str] = []
    skipped: List[str] = []
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_root = dest_dir.resolve()
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"不是有效的 ZIP 压缩包: {exc}") from exc

    infos = [info for info in archive.infolist() if not info.is_dir()]
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ValueError(f"ZIP 成员过多（{len(infos)} > {MAX_ZIP_MEMBERS}）")

    with archive:
        for info in infos:
            name = info.filename
            if not _is_safe_relpath(name):
                skipped.append(f"{name} (路径不安全)")
                continue
            ext = Path(name).suffix.lower()
            if ext not in ALLOWED_ZIP_MEMBER_EXTS:
                skipped.append(f"{name} (不支持的类型)")
                continue
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                skipped.append(f"{name} (单文件过大)")
                continue
            rel = Path(Path(name).name)
            target = (dest_root / rel).resolve()
            if target != dest_root and dest_root not in target.parents:
                skipped.append(f"{name} (zip-slip)")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            imported.append(str(target))
    return imported, skipped


class AccountVaultService:
    """扫描、解析与应用已有账号凭证。"""

    @classmethod
    def scan_roots(cls) -> List[Tuple[str, Path]]:
        roots = [
            ("lod_user", LOD_USER_DIR),
            ("sessions", SESSIONS_DIR),
        ]
        extra = os.getenv("VAULT_EXTRA_DIRS", "")
        for idx, item in enumerate(extra.split(os.pathsep)):
            item = item.strip()
            if item:
                roots.append((f"extra_{idx}", Path(item).resolve()))
        return roots

    @classmethod
    def scan_accounts(cls) -> List[VaultAccountItem]:
        merged: Dict[str, VaultAccountItem] = {}

        for source, root in cls.scan_roots():
            json_files = _iter_files(root, "*.json")
            session_files = _iter_files(root, "*.session")

            consumed_sessions: set = set()

            for json_path in json_files:
                data = _read_json_file(json_path)
                if not data:
                    continue
                fields = _extract_json_fields(data)
                session_path = _find_sibling_session(json_path, fields.get("session_file_hint"))
                if session_path:
                    consumed_sessions.add(session_path.resolve())

                account_id = make_account_id(source, str(json_path.resolve()))
                app_id = fields.get("app_id")
                app_hash = fields.get("app_hash")
                is_published = bool(app_id in PUBLISHED_API_ID_BLOCKLIST) if app_id else False
                has_usable = bool(app_id and app_hash and not is_published)

                if not fields.get("register_time"):
                    readable, unix = _file_mtime_as_register(json_path)
                    fields["register_time"] = readable
                    fields["register_time_unix"] = unix

                has_session = bool(session_path and session_path.exists())
                hint_payload = {
                    "phone": fields.get("phone"),
                    "filename": json_path.name,
                    "has_session": has_session,
                    "is_published_api_id": is_published,
                    "app_id": app_id,
                }
                item = VaultAccountItem(
                    account_id=account_id,
                    source=source,
                    phone=fields.get("phone"),
                    phone_raw=fields.get("phone_raw"),
                    user_id=fields.get("user_id"),
                    register_time=fields.get("register_time"),
                    register_time_unix=fields.get("register_time_unix"),
                    device_model=fields.get("device_model"),
                    system_version=fields.get("system_version"),
                    app_version=fields.get("app_version"),
                    lang_pack=fields.get("lang_pack"),
                    system_lang_code=fields.get("system_lang_code"),
                    app_id=app_id,
                    app_hash=app_hash,
                    is_published_api_id=is_published,
                    has_usable_custom_credentials=has_usable,
                    has_session=has_session,
                    has_json=True,
                    has_2fa=bool(fields.get("has_2fa")),
                    can_request_new_api_credentials=bool(fields.get("phone")),
                    session_missing_for_auto_code=not has_session,
                    apps_apply_hint=build_apps_apply_hint(hint_payload),
                    json_path=_rel_to_repo(json_path),
                    session_path=_rel_to_repo(session_path) if session_path else None,
                    filename=json_path.name,
                )
                merged[account_id] = item

            for session_path in session_files:
                if session_path.resolve() in consumed_sessions:
                    continue
                if not _is_telethon_session(session_path):
                    # 仍然收录：某些环境可能是空占位或尚未完成握手的快照
                    logger.info("收录非标准/空 session 快照: %s", session_path)
                sibling_json = _find_sibling_json(session_path)
                fields: Dict[str, Any] = {}
                if sibling_json:
                    data = _read_json_file(sibling_json)
                    if data:
                        fields = _extract_json_fields(data)

                account_id = make_account_id(source, str(session_path.resolve()))
                if account_id in merged:
                    continue

                app_id = fields.get("app_id")
                app_hash = fields.get("app_hash")
                is_published = bool(app_id in PUBLISHED_API_ID_BLOCKLIST) if app_id else False
                readable = fields.get("register_time")
                unix = fields.get("register_time_unix")
                if not readable:
                    readable, unix = _file_mtime_as_register(session_path)

                hint_payload = {
                    "phone": fields.get("phone") or session_path.stem,
                    "filename": session_path.name,
                    "has_session": True,
                    "is_published_api_id": is_published,
                    "app_id": app_id,
                }
                merged[account_id] = VaultAccountItem(
                    account_id=account_id,
                    source=source,
                    phone=fields.get("phone"),
                    phone_raw=fields.get("phone_raw") or session_path.stem,
                    user_id=fields.get("user_id"),
                    register_time=readable,
                    register_time_unix=unix,
                    device_model=fields.get("device_model"),
                    system_version=fields.get("system_version"),
                    app_version=fields.get("app_version"),
                    lang_pack=fields.get("lang_pack"),
                    system_lang_code=fields.get("system_lang_code"),
                    app_id=app_id,
                    app_hash=app_hash,
                    is_published_api_id=is_published,
                    has_usable_custom_credentials=bool(app_id and app_hash and not is_published),
                    has_session=True,
                    has_json=bool(sibling_json),
                    has_2fa=bool(fields.get("has_2fa")),
                    can_request_new_api_credentials=bool(fields.get("phone") or session_path.stem),
                    session_missing_for_auto_code=False,
                    apps_apply_hint=build_apps_apply_hint(hint_payload),
                    json_path=_rel_to_repo(sibling_json) if sibling_json else None,
                    session_path=_rel_to_repo(session_path),
                    filename=session_path.name,
                )

        accounts = list(merged.values())
        accounts.sort(
            key=lambda a: (a.register_time_unix or 0, a.phone or "", a.filename or ""),
            reverse=True,
        )
        return accounts

    @classmethod
    def _accounts_touching_files(cls, accounts: List[VaultAccountItem], written: List[Path]) -> List[VaultAccountItem]:
        names = {p.name for p in written}
        resolved = {p.resolve() for p in written}
        matched: List[VaultAccountItem] = []
        for acc in accounts:
            hits = []
            for rel in (acc.json_path, acc.session_path):
                if not rel:
                    continue
                path = (REPO_ROOT / rel).resolve()
                hits.append(path)
            if names.intersection({acc.filename or "", Path(acc.json_path or "").name, Path(acc.session_path or "").name}):
                matched.append(acc)
                continue
            if any(path in resolved for path in hits):
                matched.append(acc)
        return matched

    @classmethod
    def import_uploaded_bytes(
        cls,
        filename: str,
        content: bytes,
        dest_root: Optional[Path] = None,
    ) -> VaultUploadResponse:
        """保存上传的 zip / session / json，然后立刻 scan_accounts。"""
        safe_name = sanitize_upload_filename(filename)
        ext = Path(safe_name).suffix.lower()
        dest_root = Path(dest_root).resolve() if dest_root else LOD_USER_DIR.resolve()

        if ext not in ALLOWED_UPLOAD_EXTS:
            return VaultUploadResponse(
                success=False,
                message="仅支持 .zip / .session / .json 账号文件",
                filename=safe_name,
                kind="unknown",
            )
        if not content:
            return VaultUploadResponse(
                success=False,
                message="上传文件为空",
                filename=safe_name,
                kind=ext.lstrip(".") or "unknown",
            )
        if len(content) > MAX_UPLOAD_BYTES:
            return VaultUploadResponse(
                success=False,
                message=f"上传文件过大（{len(content)} > {MAX_UPLOAD_BYTES} 字节）",
                filename=safe_name,
                kind=ext.lstrip(".") or "unknown",
            )

        dest_root.mkdir(parents=True, exist_ok=True)
        imported_files: List[str] = []
        skipped: List[str] = []
        dest_dir = dest_root

        try:
            if ext == ".zip":
                dest_dir = dest_root / zip_dest_stem(safe_name)
                imported_abs, skipped = extract_zip_safely(content, dest_dir)
                imported_files = imported_abs
                if not imported_files:
                    return VaultUploadResponse(
                        success=False,
                        message="ZIP 中没有可导入的 .json / .session 账号文件",
                        filename=safe_name,
                        kind="zip",
                        dest_dir=str(dest_dir),
                        skipped_files=skipped,
                    )
            else:
                dest_dir = dest_root / IMPORTS_SUBDIR
                dest_dir.mkdir(parents=True, exist_ok=True)
                target = dest_dir / safe_name
                target.write_bytes(content)
                imported_files = [str(target)]
        except ValueError as exc:
            return VaultUploadResponse(
                success=False,
                message=str(exc),
                filename=safe_name,
                kind=ext.lstrip(".") or "unknown",
                dest_dir=str(dest_dir),
            )
        except OSError as exc:
            return VaultUploadResponse(
                success=False,
                message=f"写入导入目录失败: {exc}",
                filename=safe_name,
                kind=ext.lstrip(".") or "unknown",
                dest_dir=str(dest_dir),
            )

        extra_old = os.environ.get("VAULT_EXTRA_DIRS")
        extra_needed = dest_root.resolve() not in {root.resolve() for _, root in cls.scan_roots()}
        if extra_needed:
            os.environ["VAULT_EXTRA_DIRS"] = (
                str(dest_root) if not extra_old else f"{extra_old}{os.pathsep}{dest_root}"
            )
        try:
            listing = cls.list_accounts()
        finally:
            if extra_needed:
                if extra_old is None:
                    os.environ.pop("VAULT_EXTRA_DIRS", None)
                else:
                    os.environ["VAULT_EXTRA_DIRS"] = extra_old

        written_paths = [Path(p) for p in imported_files]
        imported_accounts = cls._accounts_touching_files(listing.accounts, written_paths)
        paired = sum(1 for acc in imported_accounts if acc.has_json and acc.has_session)
        kind = ext.lstrip(".")
        phones = [acc.phone or acc.filename or "?" for acc in imported_accounts]
        message = (
            f"已导入 {len(imported_files)} 个文件"
            + (f"，识别到 {len(imported_accounts)} 个账号" if imported_accounts else "")
            + (f"（{', '.join(phones)}）" if phones else "")
            + f"，凭证库现共 {listing.total} 个账号"
        )
        return VaultUploadResponse(
            success=True,
            message=message,
            filename=safe_name,
            kind=kind,
            dest_dir=str(dest_dir),
            imported_files=[_rel_to_repo(Path(p)) or p for p in imported_files],
            skipped_files=skipped,
            imported_accounts=imported_accounts,
            imported_count=len(imported_accounts),
            total=listing.total,
            paired_count=paired,
        )

    @classmethod
    def list_accounts(cls) -> VaultAccountListResponse:
        config = ConfigManager.get_instance().config
        accounts = cls.scan_accounts()
        return VaultAccountListResponse(
            total=len(accounts),
            lod_user_dir=str(LOD_USER_DIR),
            sessions_dir=str(SESSIONS_DIR),
            accounts=accounts,
            applied_api_id=config.custom_api_id,
            applied_api_hash=config.custom_api_hash,
            api_credential_mode=config.api_credential_mode,
            published_api_id_count=sum(1 for acc in accounts if acc.is_published_api_id),
            missing_session_count=sum(1 for acc in accounts if acc.session_missing_for_auto_code),
            guidance=VAULT_GUIDANCE,
        )

    @classmethod
    def get_account(cls, account_id: str) -> Optional[VaultAccountItem]:
        for item in cls.scan_accounts():
            if item.account_id == account_id:
                return item
        return None

    @classmethod
    def resolve_session_file(cls, account: VaultAccountItem) -> Optional[Path]:
        if not account.session_path:
            return None
        path = (REPO_ROOT / account.session_path).resolve()
        if path.exists():
            return path
        # 回退：按文件名在两个根目录再找一次
        name = Path(account.session_path).name
        for _, root in cls.scan_roots():
            hits = list(root.rglob(name))
            if hits:
                return hits[0]
        return None

    @classmethod
    def apply_account_credentials(
        cls,
        account_id: str,
        set_mode_custom: bool = True,
    ) -> ApplyVaultCredentialsResponse:
        account = cls.get_account(account_id)
        if not account:
            return ApplyVaultCredentialsResponse(
                success=False,
                message=f"未找到账号 {account_id}，请先刷新凭证库",
                account_id=account_id,
            )
        if not account.app_id or not account.app_hash:
            return ApplyVaultCredentialsResponse(
                success=False,
                message="该账号记录中缺少 app_id / app_hash，无法应用到全局配置",
                account_id=account_id,
            )

        manager = ConfigManager.get_instance()
        config = manager.config.model_copy(deep=True)
        config.custom_api_id = int(account.app_id)
        config.custom_api_hash = account.app_hash
        if set_mode_custom:
            config.api_credential_mode = "custom"
        manager.save_config(config)

        warning = None
        if account.is_published_api_id:
            warning = (
                f"该账号记录的 api_id={account.app_id} 属于已知公开泄露官方 ID，"
                "写入 custom_api_id 后仍会触发 API_ID_PUBLISHED_FLOOD。"
                "请改用「🔐 凭证库 / 开发者 API」为该账号申请专属凭证："
                "有同名 .session 时可自动读码，否则在 Telegram 客户端查看 Web 登录码后手动提交；"
                "或直接在参数拓扑填入已有的自建 custom_api_id / custom_api_hash。"
            )

        return ApplyVaultCredentialsResponse(
            success=True,
            message=(
                f"已将账号 {account.phone or account.filename} 的 "
                f"api_id={account.app_id} 写入全局 custom_api_id / custom_api_hash"
            ),
            account_id=account_id,
            custom_api_id=config.custom_api_id,
            custom_api_hash=config.custom_api_hash,
            api_credential_mode=config.api_credential_mode,
            is_published_api_id=account.is_published_api_id,
            warning=warning,
        )

    @classmethod
    def apply_raw_credentials(
        cls,
        api_id: int,
        api_hash: str,
        set_mode_custom: bool = True,
    ) -> ApplyVaultCredentialsResponse:
        manager = ConfigManager.get_instance()
        config = manager.config.model_copy(deep=True)
        config.custom_api_id = int(api_id)
        config.custom_api_hash = api_hash
        if set_mode_custom:
            config.api_credential_mode = "custom"
        manager.save_config(config)
        is_published = int(api_id) in PUBLISHED_API_ID_BLOCKLIST
        return ApplyVaultCredentialsResponse(
            success=True,
            message=f"已将 api_id={api_id} 写入全局 custom_api_id / custom_api_hash",
            custom_api_id=config.custom_api_id,
            custom_api_hash=config.custom_api_hash,
            api_credential_mode=config.api_credential_mode,
            is_published_api_id=is_published,
            warning=(
                "写入的 api_id 属于已知公开泄露官方 ID"
                if is_published
                else None
            ),
        )
