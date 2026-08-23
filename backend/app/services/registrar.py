import asyncio
import json
import random
import logging
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
from backend.app.services.vaksms import VakSmsService
from backend.app.services.attestation_gateway import AttestationGatewayService
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
    "default": {
        "first": ["James", "Alex", "David", "Elena", "Marcus", "Lucas", "Sophie", "Michael", "Daniel"],
        "last": ["Smith", "Brown", "Wilson", "Taylor", "Anderson", "White", "Miller", "Davis"]
    }
}
GEO_NAME_POOLS = SYNTHETIC_IDENTITY_POOLS


class RegistrationTaskManager:
    """边缘节点引导任务与状态机审计追踪管理器 (Node Provisioning Task Manager)"""
    _instance = None
    tasks: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def get_instance(cls) -> "RegistrationTaskManager":
        if cls._instance is None:
            cls._instance = RegistrationTaskManager()
        return cls._instance

    def create_task(self) -> str:
        task_id = str(uuid.uuid4())[:8]
        now = datetime.datetime.now().isoformat()
        self.tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "phone": None,
            "user_id": None,
            "error": None,
            "logs": [],
            "created_at": now,
            "updated_at": now
        }
        return task_id

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.tasks.get(task_id)

    def list_tasks(self) -> List[Dict[str, Any]]:
        return sorted(self.tasks.values(), key=lambda x: x["created_at"], reverse=True)

    async def append_log(self, task_id: str, message: str):
        if task_id in self.tasks:
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            log_entry = f"[{timestamp}] {message}"
            self.tasks[task_id]["logs"].append(log_entry)
            self.tasks[task_id]["updated_at"] = datetime.datetime.now().isoformat()
            logger.info(f"[{task_id}] {message}")

    def update_task_status(self, task_id: str, status: str, **kwargs):
        if task_id in self.tasks:
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

    @classmethod
    async def _refund_and_revoke_channel(
        cls,
        sms_svc: VakSmsService,
        act_id: Optional[str],
        task_id: str,
        manager: RegistrationTaskManager,
        reason: str,
    ) -> None:
        """失败路径统一走 Vak-SMS setStatus=bad，触发取消与自动退款。"""
        if not act_id:
            return
        result = await sms_svc.cancel(act_id)
        if result.get("skipped"):
            return
        if result.get("success"):
            await manager.append_log(
                task_id,
                f"[自动退订/撤销信道句柄完成] act_id={act_id} (Vak-SMS status=bad, 原因: {reason})"
            )
            return
        detail = result.get("error") or result.get("data") or "unknown"
        await manager.append_log(
            task_id,
            f"⚠️ 自动退订/撤销信道句柄未成功 (act_id={act_id}, 原因: {reason}): {detail}"
        )

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
        proxy_override: Optional[Dict[str, Any]] = None
    ):
        """执行单次边缘虚拟节点引导全流程"""
        manager = RegistrationTaskManager.get_instance()
        manager.update_task_status(task_id, "running")

        config = ConfigManager.get_instance().config
        target_country = (country or config.target_country).lower()
        active_app = app_type or config.active_app_type

        sms_svc = VakSmsService(config.vak_sms_api_key)

        # 动态出口中继网关调度
        active_proxy = proxy_override
        if not active_proxy and config.use_proxy_seller_auto and config.proxy_seller_key:
            try:
                await manager.append_log(task_id, f"尝试通过多径中继网关自动租借 {target_country.upper()} 出口跳点...")
                from backend.app.services.proxyseller import ProxySellerService
                ps_svc = ProxySellerService(config.proxy_seller_key)
                fetched_proxies = await ps_svc.get_proxy_list(country=target_country)
                if fetched_proxies:
                    active_proxy = fetched_proxies[0]
                    await manager.append_log(task_id, f"已动态分配中继跳点: {active_proxy['addr']}:{active_proxy['port']}")
                await ps_svc.close()
            except Exception as e:
                await manager.append_log(task_id, f"中继网关动态分配未成功 ({e})，回退至静态后备中继")

        if not active_proxy:
            active_proxy = config.fallback_proxy.model_dump()

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
            await manager.append_log(task_id, f"选定端点模板: {profile['name']} (AID: {aid})")
            await manager.append_log(task_id, f"绑定硬件特征: {profile['device_model']} ({profile['system_version']}), App: {profile['app_version']}")
            await manager.append_log(task_id, f"网络语言拓扑: {profile['system_lang_code']}, 时区偏置: {profile.get('tz_offset', -14400)}")

            # 1. 租用带外通信句柄
            await manager.append_log(task_id, f"正在向带外遥测提供者申请拓扑代码 '{target_country.upper()}' 的信道句柄...")
            act_id, phone = await sms_svc.get_number(country=target_country, service="tg")
            manager.update_task_status(task_id, "running", phone=phone)
            await manager.append_log(task_id, f"成功获取端点通信句柄: {phone} (Session Handle ID: {act_id})")

            # 2. 端点信誉预检
            await manager.append_log(task_id, "正在对通信句柄进行历史安全状态审计...")
            check_data = await bypass_svc.check_phone_history(phone, aid)
            if check_data:
                check_id = check_data.get("id")
                if "BANNED" in check_data.get("statuses", []):
                    await manager.append_log(task_id, "检测到该通信句柄存在服务端历史异常记录，触发主动退避与信道撤销！")
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

            await client.connect()
            await manager.append_log(task_id, "已完成 MTProto 传输层 Diffie-Hellman 密钥交换与加密连接建立")

            # 5. 执行协议端点握手序列
            await cls.perform_handshake(client, profile, task_id, manager)

            # 6. 发起挑战分发请求 (SendCode)
            code_settings = types.CodeSettings(
                allow_flashcall=False,
                current_number=False,
                allow_app_hash=True,
                allow_missed_call=False,
                token=push_token.encode('utf-8') if push_token else None
            )

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
            phone_code_hash = sent_code.phone_code_hash
            await manager.append_log(task_id, f"挑战已由服务端下发! 分发通道类型: {type(sent_code.type).__name__}")

            # 7. 异步等待带外挑战证明
            await manager.append_log(task_id, "正在等待带外遥测通道下发瞬时挑战证明 (OTP)...")
            sms_code = await sms_svc.wait_for_code(
                act_id,
                log_callback=lambda msg: manager.append_log(task_id, msg)
            )
            await manager.append_log(task_id, f"带外挑战证明获取成功: {sms_code}")

            # 8. 状态机迁移与鉴权验证
            auth_result = None
            needs_signup = False

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
                    await manager.append_log(task_id, "检测到节点已存在二级密码学保护状态，执行二级凭证认证...")
                    auth_result = await client.sign_in(password=config.default_2fa_password)
                else:
                    raise e

            if needs_signup:
                first_name, last_name = cls._get_random_name(target_country)
                await manager.append_log(task_id, f"状态机迁移为新节点初始化，注入合成身份属性: {first_name} {last_name}")

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

            user = auth_result.user if hasattr(auth_result, 'user') else auth_result
            user_id = user.id if hasattr(user, 'id') else 0
            await manager.append_log(task_id, f"虚拟节点状态机初始化成功! 节点 UID: {user_id}, 句柄: {phone}")

            # 9. 附加二级密码学状态保护 (Secondary State Lock / 2FA)
            two_fa_set = False
            if config.auto_set_2fa and config.default_2fa_password:
                try:
                    await manager.append_log(task_id, f"启用二级密码学状态保护: {config.default_2fa_password[:3]}***")
                    await client.edit_2fa(new_password=config.default_2fa_password)
                    two_fa_set = True
                    await manager.append_log(task_id, "二级密码学状态锁已成功锁定")
                except Exception as e:
                    await manager.append_log(task_id, f"配置二级状态锁跳过或提示: {e}")

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

        except PhoneNumberBannedError:
            err = f"通信句柄 {phone} 处于服务端拒绝服务状态 (PHONE_NUMBER_BANNED)"
            await manager.append_log(task_id, f"❌ {err}")
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
            if client and client.is_connected():
                await client.disconnect()
            await sms_svc.close()
            await bypass_svc.close()


NodeProvisioningOrchestrator = RegistrationOrchestrator
