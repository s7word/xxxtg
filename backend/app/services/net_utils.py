import logging
from typing import Optional, Dict, Any
import httpx

logger = logging.getLogger("NetUtils")


def create_httpx_client(
    proxy: Optional[Dict[str, Any]] = None,
    connect_timeout: float = 6.0,
    total_timeout: float = 20.0
) -> httpx.AsyncClient:
    """构建统一配置的异步 HTTP 客户端 (可选绑定出口中继通道)

    单独收紧连接建立超时：DNS 解析/端点不可达时应尽快失败并切换到下一候选地址，
    而不是占用整条注册流水线的时间预算。
    """
    proxy_url = None
    if proxy and proxy.get("addr") and proxy.get("port"):
        p_type = proxy.get("proxy_type", "socks5").lower()
        if p_type == "socks":
            p_type = "socks5"
        auth = f"{proxy['username']}:{proxy['password']}@" if proxy.get("username") and proxy.get("password") else ""
        proxy_url = f"{p_type}://{auth}{proxy['addr']}:{proxy['port']}"
        logger.info(f"HTTP 客户端绑定中继出口通道: {p_type}://{proxy['addr']}:{proxy['port']}")

    timeout = httpx.Timeout(total_timeout, connect=connect_timeout)
    client_kwargs = {"verify": False, "timeout": timeout}
    if proxy_url:
        try:
            return httpx.AsyncClient(proxy=proxy_url, **client_kwargs)
        except TypeError:
            return httpx.AsyncClient(proxies=proxy_url, **client_kwargs)
    return httpx.AsyncClient(**client_kwargs)
