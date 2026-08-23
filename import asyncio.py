import asyncio
import random
import string
import logging
import hashlib
from typing import Optional, Dict, Any, Tuple, List
import httpx
from telethon import TelegramClient
from telethon.tl import functions, types
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneNumberBannedError,
    FloodWaitError
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TG-Regger")

# ==================== 1. 设备指纹 Profile 矩阵 (与 App AID 严格绑定) ====================
class DeviceProfile:
    """
    真实设备与客户端指纹配置
    注意：不同的 App 类型必须与对应的 api_id、app_version 及 AntiSafety AID 完全一致
    """
    PROFILES = {
        # 1. 官方 Telegram Android (主版)
        "telegram_android": {
            "name": "Telegram Android 10.x",
            "aid": "308aba4e-5680-466b-81a5-477ac6befa95",
            "api_id": 6,
            "api_hash": "eb06d4abfb49dc3eeb1aeb98ae0f581e",
            "app_name": "tg",
            "app_device": "Android",
            "device_model": "Samsung Galaxy S23",
            "system_version": "SDK 33",
            "app_version": "10.9.2 (25345)",
            "app_version_pure": "10.9.2",
            "app_build": "25345",
            "lang_pack": "android",
            "lang_code": "es",
            "system_lang_code": "es-CL"
        },
        # 2. 官方 Telegram X (TDLib 架构版)
        "telegram_x": {
            "name": "Telegram X",
            "aid": "47f7d612-fe1a-4167-a450-db8a52048e9c",
            "api_id": 21724,
            "api_hash": "3e0cb5ab2d48077663362339f7c30f45",
            "app_name": "tg_x",
            "app_device": "Android",
            "device_model": "Google Pixel 7 Pro",
            "system_version": "SDK 33",
            "app_version": "0.26.5.1692",
            "app_version_pure": "0.26.5",
            "app_build": "1692",
            "lang_pack": "android_x",
            "lang_code": "es",
            "system_lang_code": "es-CL"
        },
        # 3. 官方 Telegram 9 (经典历史稳定版)
        "telegram_9": {
            "name": "Telegram Android 9.x",
            "aid": "59e59906-5177-4f6f-8f7e-ced3fe370997",
            "api_id": 6,
            "api_hash": "eb06d4abfb49dc3eeb1aeb98ae0f581e",
            "app_name": "tg",
            "app_device": "Android",
            "device_model": "Xiaomi 13",
            "system_version": "SDK 32",
            "app_version": "9.6.7 (33219)",
            "app_version_pure": "9.6.7",
            "app_build": "33219",
            "lang_pack": "android",
            "lang_code": "es",
            "system_lang_code": "es-CL"
        }
    }

    @classmethod
    def get_profile(cls, app_key: str = "telegram_android") -> Dict[str, Any]:
        return cls.PROFILES.get(app_key, cls.PROFILES["telegram_android"])


# ==================== 2. 全局配置中心 ====================
class Config:
    # 注册客户端模板选择 ("telegram_android", "telegram_x", "telegram_9")
    ACTIVE_APP_TYPE = "telegram_android"

    # AntiSafety Bypass 服务配置
    ANTISAFETY_API_KEY = "as2b21dc7b71b5ce8166a42c22b54566"

    # Vak-SMS 接码平台配置
    VAK_SMS_API_KEY = "16aa4499a3954317aaf002a55e354eed"
    TARGET_COUNTRY = "cl"   # 智利 (Chile, +56 区号)

    # Proxy-Seller 代理服务配置
    PROXY_SELLER_KEY = "q8WKCvB4QNUF"

    # 默认 2FA 强密码 (注册成功后自动绑定)
    DEFAULT_2FA_PASSWORD = "Password@2026!Sec"

    # 静态备用代理 (若 Proxy-Seller API 限制 IP 白名单，可在此填入提取到的智利代理)
    FALLBACK_PROXY = {
        "proxy_type": "socks5",
        "addr": "127.0.0.1",
        "port": 10808,
        "username": None,
        "password": None
    }


# ==================== 3. Proxy-Seller 代理管理服务 ====================
class ProxySellerService:
    """Proxy-Seller (https://proxy-seller.com) API 客户端"""
    BASE_URL = "https://proxy-seller.com/personal/api/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    async def get_proxy_list(self, country: str = "cl") -> List[Dict[str, Any]]:
        """获取已购代理列表并筛选指定国家的代理"""
        url = f"{self.BASE_URL}/{self.api_key}/proxy/list"
        try:
            resp = await self.client.get(url)
            data = resp.json()
            if data.get("status") == "error":
                errors = data.get("errors", [])
                err_msg = errors[0].get("message") if errors else "Unknown error"
                logger.warning(f"[Proxy-Seller] API 提示: {err_msg}")
                return []
            
            proxies = []
            raw_items = data.get("data", {}).get("items", [])
            for item in raw_items:
                if country.lower() in item.get("country", "").lower():
                    proxies.append({
                        "proxy_type": item.get("protocol", "socks5").lower(),
                        "addr": item.get("ip"),
                        "port": int(item.get("port_socks5") or item.get("port")),
                        "username": item.get("login"),
                        "password": item.get("password")
                    })
            logger.info(f"[Proxy-Seller] 成功获取到 {len(proxies)} 个 {country.upper()} 代理")
            return proxies
        except Exception as e:
            logger.warning(f"[Proxy-Seller] 请求代理列表异常: {e}")
            return []


# ==================== 4. AntiSafety Bypass & 预检服务 ====================
class AntiSafetyService:
    API_BASE = "https://api.antisafety.net"
    REPORTING_BASE = "https://reporting.antisafety.net"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0, verify=False)

    async def check_phone_history(self, phone_number: str, aid: str) -> Optional[Dict[str, Any]]:
        clean_number = "".join([c for c in phone_number if c.isdigit()])
        num_hash = hashlib.md5(clean_number.encode('utf-8')).hexdigest()
        try:
            resp = await self.client.get(f"{self.REPORTING_BASE}/check", params={
                "api_key": self.api_key,
                "aid": aid,
                "hash": num_hash,
                "number": clean_number
            })
            data = resp.json()
            if data.get("status") == "ok":
                logger.info(f"[AntiSafety] 号码预检通过 (ID: {data.get('id')}, 历史状态: {data.get('statuses')})")
                return data
            logger.warning(f"[AntiSafety] 号码预检响应: {data}")
        except Exception as e:
            logger.warning(f"[AntiSafety] 号码预检异常: {e}")
        return None

    async def get_push_token(self, profile: Dict[str, Any]) -> str:
        params = {
            "apiKey": self.api_key,
            "aid": profile.get("aid"),
            "appName": profile.get("app_name", "tg"),
            "appDevice": profile.get("app_device", "Android"),
            "appVersion": profile.get("app_version_pure", "10.9.2"),
            "appBuild": profile.get("app_build", "25345")
        }
        resp = await self.client.get(f"{self.API_BASE}/push/getToken", params=params)
        data = resp.json()
        task_id = data.get("id")

        if not task_id:
            raise RuntimeError(f"AntiSafety Push 任务下发失败: {data}")

        for _ in range(30):
            await asyncio.sleep(2)
            check_resp = await self.client.get(f"{self.API_BASE}/push/getStatus", params={
                "apiKey": self.api_key,
                "aid": profile.get("aid"),
                "id": task_id
            })
            res = check_resp.json()
            if res.get("status") == "done":
                return res["token"]
            if res.get("status") == "error":
                raise RuntimeError(f"AntiSafety Push 生成失败: {res.get('message')}")

        raise TimeoutError("AntiSafety 获取 Push Token 超时")

    async def report_result(self, check_id: str, aid: str, status: str):
        if not check_id:
            return
        try:
            await self.client.get(f"{self.REPORTING_BASE}/report", params={
                "api_key": self.api_key,
                "aid": aid,
                "id": check_id,
                "status": status
            })
            logger.info(f"[AntiSafety] 状态上报完成: {status}")
        except Exception as e:
            logger.debug(f"[AntiSafety] 状态上报异常: {e}")


# ==================== 5. Vak-SMS 接码服务 ====================
class VakSmsService:
    BASE_URL = "https://vak-sms.com/api"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)

    async def get_balance(self) -> float:
        resp = await self.client.get(f"{self.BASE_URL}/getBalance/", params={"apiKey": self.api_key})
        data = resp.json()
        if "balance" in data:
            return float(data["balance"])
        raise RuntimeError(f"Vak-SMS 获取余额失败: {data}")

    async def get_stock_count(self, country: str = "cl", service: str = "tg") -> int:
        resp = await self.client.get(f"{self.BASE_URL}/getCountNumber/", params={
            "apiKey": self.api_key,
            "service": service,
            "country": country
        })
        data = resp.json()
        return int(data.get(service, 0))

    async def get_number(self, country: str = "cl", service: str = "tg", operator: Optional[str] = None) -> Tuple[str, str]:
        params = {"apiKey": self.api_key, "service": service, "country": country}
        if operator:
            params["operator"] = operator
        resp = await self.client.get(f"{self.BASE_URL}/getNumber/", params=params)
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Vak-SMS 申请号码失败: {data.get('error')}")
        if "tel" in data and "idNum" in data:
            phone = str(data["tel"])
            if not phone.startswith("+"):
                phone = "+" + phone
            return str(data["idNum"]), phone
        raise RuntimeError(f"Vak-SMS 返回格式未知: {data}")

    async def wait_for_code(self, act_id: str, max_attempts: int = 30) -> str:
        params = {"apiKey": self.api_key, "idNum": act_id}
        for _ in range(max_attempts):
            await asyncio.sleep(4)
            resp = await self.client.get(f"{self.BASE_URL}/getSmsCode/", params=params)
            data = resp.json()
            code = data.get("smsCode")
            if code is not None:
                return str(code)
        raise TimeoutError("等待 Vak-SMS 验证码超时")

    async def finish(self, act_id: str):
        await self.client.get(f"{self.BASE_URL}/setStatus/", params={
            "apiKey": self.api_key,
            "status": "end",
            "idNum": act_id
        })

    async def cancel(self, act_id: str):
        await self.client.get(f"{self.BASE_URL}/setStatus/", params={
            "apiKey": self.api_key,
            "status": "bad",
            "idNum": act_id
        })


# ==================== 6. 官方客户端握手链路与全流程注册调度 ====================
class TelegramRegistrar:
    def __init__(self, sms_svc: VakSmsService, bypass_svc: AntiSafetyService, proxy_config: Dict[str, Any]):
        self.sms = sms_svc
        self.bypass = bypass_svc
        self.proxy = proxy_config

    @staticmethod
    def _random_chile_name() -> Tuple[str, str]:
        """生成智利/西语常用真实姓名"""
        first_names = ["Mateo", "Agustín", "Santiago", "Tomás", "Lucas", "Benjamín", "Matías", "Sofía", "Isabella", "Emilia"]
        last_names = ["González", "Muñoz", "Rojas", "Díaz", "Pérez", "Soto", "Contreras", "Silva", "Martínez", "Sepúlveda"]
        return random.choice(first_names), random.choice(last_names)

    async def perform_official_handshake(self, client: TelegramClient, profile: Dict[str, Any]):
        """补齐 Telegram 官方客户端初始化序列"""
        logger.info(">>> [握手链] 正在执行官方客户端初始化序列...")
        nearest_dc = await client(functions.help.GetNearestDcRequest())
        logger.info(f"[*] 握手步骤 1: Nearest DC: {nearest_dc.nearest_dc}, Current DC: {nearest_dc.this_dc}")
        await asyncio.sleep(random.uniform(0.3, 0.7))

        server_config = await client(functions.help.GetConfigRequest())
        logger.info(f"[*] 握手步骤 2: 服务器全局配置拉取成功 (DC 列表数: {len(server_config.dc_options)})")
        await asyncio.sleep(random.uniform(0.3, 0.6))

        app_config = await client(functions.help.GetAppConfigRequest(hash=0))
        logger.info(f"[*] 握手步骤 3: 官方动态 AppConfig 参数已同步")
        await asyncio.sleep(random.uniform(0.4, 0.8))

        await client(functions.langpack.GetLanguagesRequest(lang_pack=profile.get("lang_pack", "android")))
        logger.info(f"[*] 握手步骤 4: 客户端语言包 ({profile.get('lang_pack')}) 已载入")

        human_jitter = random.uniform(3.5, 6.0)
        logger.info(f"[*] 握手序列完成，模拟用户界面输入中 (耗时 {human_jitter:.1f} 秒)...")
        await asyncio.sleep(human_jitter)

    async def register_single_account(self, session_name: str, app_type: str = "telegram_android", country: str = "cl") -> bool:
        act_id = None
        check_id = None
        client = None
        profile = DeviceProfile.get_profile(app_type)
        aid = profile["aid"]

        try:
            logger.info(f"步骤 1: 加载应用模板: {profile['name']} (AID: {aid})")

            # 1. 租用号码 (智利 +56)
            logger.info(f"步骤 2: 正在向 Vak-SMS 租用号码 (国家: {country.upper()})...")
            act_id, phone = await self.sms.get_number(country=country)
            logger.info(f"获得号码: {phone} (ID: {act_id})")

            # 2. AntiSafety 号码预检
            check_data = await self.bypass.check_phone_history(phone, aid)
            if check_data:
                check_id = check_data.get("id")
                if "BANNED" in check_data.get("statuses", []):
                    logger.warning(f"号码 {phone} 在 AntiSafety 数据库中存在封号记录，主动退号！")
                    await self.sms.cancel(act_id)
                    await self.bypass.report_result(check_id, aid, "REJECTED")
                    return False

            # 3. 申请 Push Token
            logger.info("步骤 3: 向 AntiSafety 申请 Push Token...")
            push_token = await self.bypass.get_push_token(profile)
            logger.info("成功获取合法 Push Token")

            # 4. 初始化 MTProto (绑定智利代理与西语语言包)
            client = TelegramClient(
                session=session_name,
                api_id=profile["api_id"],
                api_hash=profile["api_hash"],
                proxy=self.proxy,
                device_model=profile["device_model"],
                system_version=profile["system_version"],
                app_version=profile["app_version"],
                lang_code=profile["lang_code"],
                system_lang_code=profile["system_lang_code"]
            )

            await client.connect()
            logger.info("已连接至 Telegram MTProto 网关")

            # 5. 官方握手序列
            await self.perform_official_handshake(client, profile)

            # 6. 发送验证码请求
            code_settings = types.CodeSettings(
                allow_flashcall=False,
                current_number=False,
                allow_app_hash=True,
                allow_missed_call=False,
                token=push_token.encode('utf-8') if push_token else None
            )

            logger.info("步骤 4: 调用 auth.sendCode 请求验证码...")
            sent_code = await client(functions.auth.SendCodeRequest(
                phone_number=phone,
                api_id=profile["api_id"],
                api_hash=profile["api_hash"],
                settings=code_settings
            ))
            phone_code_hash = sent_code.phone_code_hash
            logger.info(f"SendCode 成功，验证码下发方式: {type(sent_code.type).__name__}")

            # 7. 等待短信
            logger.info("步骤 5: 等待短信验证码下发...")
            sms_code = await self.sms.wait_for_code(act_id)
            logger.info(f"收到验证码: {sms_code}")

            # 8. 登录 / 注册
            try:
                auth_result = await client(functions.auth.SignInRequest(
                    phone_number=phone,
                    phone_code_hash=phone_code_hash,
                    phone_code=sms_code
                ))
            except Exception as e:
                if "SignUpRequired" in str(e) or isinstance(e, types.auth.AuthorizationSignUpRequired):
                    first_name, last_name = self._random_chile_name()
                    logger.info(f"步骤 6: 号码未注册，提交新用户资料: {first_name} {last_name}")

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
                else:
                    raise e

            # 9. 注册成功，完成状态记录
            user = auth_result.user if hasattr(auth_result, 'user') else auth_result
            logger.info(f"🎉 智利账号注册成功! UserID: {user.id}, 手机号: {phone}")

            await self.sms.finish(act_id)
            if check_id:
                await self.bypass.report_result(check_id, aid, "REGISTERED")
            return True

        except PhoneNumberBannedError:
            logger.error(f"号码 {phone} 已被 Telegram 封禁 (PHONE_NUMBER_BANNED)")
            if act_id: await self.sms.cancel(act_id)
            if check_id: await self.bypass.report_result(check_id, aid, "BANNED")
        except FloodWaitError as e:
            logger.error(f"请求过于频繁，触发风控限制，需等待 {e.seconds} 秒")
            if act_id: await self.sms.cancel(act_id)
            if check_id: await self.bypass.report_result(check_id, aid, "FLOOD_WAIT")
        except Exception as ex:
            logger.error(f"注册流水线发生异常: {str(ex)}")
            if act_id: await self.sms.cancel(act_id)
            if check_id: await self.bypass.report_result(check_id, aid, "NO_CODE")
        finally:
            if client and client.is_connected():
                await client.disconnect()
        return False


# ==================== 7. 启动与实测入口 ====================
async def main():
    sms = VakSmsService(Config.VAK_SMS_API_KEY)
    bypass = AntiSafetyService(Config.ANTISAFETY_API_KEY)
    proxy_seller = ProxySellerService(Config.PROXY_SELLER_KEY)

    # 1. 验证 Vak-SMS 智利库存
    bal = await sms.get_balance()
    cl_stock = await sms.get_stock_count(country=Config.TARGET_COUNTRY, service="tg")
    logger.info(f"✅ [Vak-SMS] 连通正常 | 余额: {bal} | 智利 (CL, +56) Telegram 可用库存: {cl_stock} 个")

    # 2. 验证 AntiSafety 联通性
    test_profile = DeviceProfile.get_profile(Config.ACTIVE_APP_TYPE)
    check_test = await bypass.check_phone_history("56912345678", test_profile["aid"])
    if check_test:
        logger.info(f"✅ [AntiSafety] 连通正常 | 当前应用模板: {test_profile['name']} (AID: {test_profile['aid']})")

    # 3. 提取 Proxy-Seller 代理 (若 API 白名单受限则使用备用代理)
    proxies = await proxy_seller.get_proxy_list(country=Config.TARGET_COUNTRY)
    active_proxy = proxies[0] if proxies else Config.FALLBACK_PROXY
    logger.info(f"[*] 当前使用代理配置: {active_proxy['addr']}:{active_proxy['port']} ({active_proxy['proxy_type'].upper()})")

    # 4. 准备注册调度器
    registrar = TelegramRegistrar(sms, bypass, active_proxy)
    logger.info(">>> 实验环境准备就绪，可以执行单号注册测试...")

if __name__ == "__main__":
    asyncio.run(main())
