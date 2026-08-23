import logging
from typing import Optional, Dict, Any
from urllib.parse import quote
import httpx

logger = logging.getLogger("NetUtils")


def _bracket_proxy_host(addr: Any) -> str:
    """IPv6 字面量必须写成 [host]，否则端口会被冒号截断。"""
    host = str(addr or "").strip()
    if not host:
        return host
    if host.startswith("[") and "]" in host:
        return host
    if ":" in host:
        return f"[{host}]"
    return host


def format_httpx_proxy_url(proxy: Optional[Dict[str, Any]]) -> Optional[str]:
    """拼接 httpx 可用的 socks5:// / http:// 代理 URL。

    username / password 使用 quote(safe='') 彻底转义 @ : / # 等保留字符，
    避免认证信息被 URL 解析器截断；IPv6 地址自动加 [host]。
    """
    if not proxy or not proxy.get("addr") or not proxy.get("port"):
        return None
    p_type = str(proxy.get("proxy_type") or "socks5").lower()
    if p_type in {"socks", "socks5", "socks5h"}:
        p_type = "socks5"
    elif p_type in {"http", "https"}:
        p_type = "http"
    host = _bracket_proxy_host(proxy.get("addr"))
    port = proxy.get("port")
    username = proxy.get("username")
    password = proxy.get("password")
    auth = ""
    if username and password:
        user = quote(str(username), safe="")
        pwd = quote(str(password), safe="")
        auth = f"{user}:{pwd}@"
    return f"{p_type}://{auth}{host}:{port}"


def create_httpx_client(
    proxy: Optional[Dict[str, Any]] = None,
    connect_timeout: float = 6.0,
    total_timeout: float = 20.0
) -> httpx.AsyncClient:
    """构建统一配置的异步 HTTP 客户端 (可选绑定出口中继通道)

    单独收紧连接建立超时：DNS 解析/端点不可达时应尽快失败并切换到下一候选地址，
    而不是占用整条注册流水线的时间预算。
    """
    proxy_url = format_httpx_proxy_url(proxy)
    if proxy_url:
        p_type = str(proxy.get("proxy_type") or "socks5").lower()
        logger.info(f"HTTP 客户端绑定中继出口通道: {p_type}://{proxy['addr']}:{proxy['port']}")

    timeout = httpx.Timeout(total_timeout, connect=connect_timeout)
    client_kwargs = {"verify": False, "timeout": timeout}
    if proxy_url:
        try:
            return httpx.AsyncClient(proxy=proxy_url, **client_kwargs)
        except TypeError:
            return httpx.AsyncClient(proxies=proxy_url, **client_kwargs)
    return httpx.AsyncClient(**client_kwargs)
