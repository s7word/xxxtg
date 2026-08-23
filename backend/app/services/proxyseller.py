import logging
from typing import Optional, Dict, Any, List
import httpx

logger = logging.getLogger("MultipathRelayGatewayService")

class ProxySellerService:
    """多径传输出口中继网关服务 (Multipath Egress Relay Gateway Provider)"""
    BASE_URL = "https://proxy-seller.com/personal/api/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    async def close(self):
        try:
            await self.client.aclose()
        except Exception:
            pass

    async def get_proxy_list(self, country: Optional[str] = None) -> List[Dict[str, Any]]:
        """从分布式中继池动态检索指定拓扑区域的可用出口节点"""
        url = f"{self.BASE_URL}/{self.api_key}/proxy/list"
        try:
            resp = await self.client.get(url)
            data = resp.json()
            if data.get("status") == "error":
                errors = data.get("errors", [])
                err_msg = errors[0].get("message") if errors else "API Error"
                raise RuntimeError(err_msg)
            
            proxies = []
            raw_items = data.get("data", {}).get("items", [])
            for item in raw_items:
                c_code = str(item.get("country", "")).lower()
                if country and country.lower() not in c_code:
                    continue
                proxies.append({
                    "id": item.get("id"),
                    "proxy_type": item.get("protocol", "socks5").lower(),
                    "addr": item.get("ip"),
                    "port": int(item.get("port_socks5") or item.get("port", 1080)),
                    "username": item.get("login"),
                    "password": item.get("password"),
                    "country": item.get("country"),
                    "active_until": item.get("active_until")
                })
            return proxies
        except Exception as e:
            logger.warning(f"检索出口中继节点列表异常: {e}")
            raise e

    discover_relay_nodes = get_proxy_list

    @staticmethod
    async def test_proxy_connectivity(proxy_dict: Dict[str, Any]) -> Dict[str, Any]:
        """对指定出口中继路径进行主动连通性与公网拓扑寻址探测"""
        proxy_type = proxy_dict.get('proxy_type', 'socks5').lower()
        if proxy_type == "socks":
            proxy_type = "socks5"
        addr = proxy_dict.get('addr')
        port = proxy_dict.get('port')
        username = proxy_dict.get('username')
        password = proxy_dict.get('password')

        if not addr or not port:
            return {
                "success": False,
                "error": "未配置有效的中继跳点主机地址与端口"
            }

        proxy_url = f"{proxy_type}://"
        if username and password:
            proxy_url += f"{username}:{password}@"
        proxy_url += f"{addr}:{port}"

        client_kwargs = {"verify": False, "timeout": 12.0}
        try:
            try:
                client = httpx.AsyncClient(proxy=proxy_url, **client_kwargs)
            except TypeError:
                client = httpx.AsyncClient(proxies=proxy_url, **client_kwargs)

            async with client:
                ip_resp = await client.get("https://ipapi.co/json/")
                ip_data = ip_resp.json()
                return {
                    "success": True,
                    "ip": ip_data.get("ip"),
                    "country": ip_data.get("country_name"),
                    "country_code": ip_data.get("country_code"),
                    "city": ip_data.get("city"),
                    "org": ip_data.get("org")
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    probe_relay_path_connectivity = test_proxy_connectivity

MultipathRelayGateway = ProxySellerService
