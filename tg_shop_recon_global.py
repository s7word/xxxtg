#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
================================================================================
TG-Shop Recon Global: 全球网络空间测绘与 Telegram 资产/目标深度发现系统
================================================================================
核心定位：【以目标发现、全网资产拓扑与特征画像为主】
核心能力：
1. 完整接入全球 8 大网络空间测绘引擎 (Shodan, Censys, Netlas, Criminal IP, ZoomEye, FOFA, Hunter, Quake)
2. 免 Key 搜索引擎 Dorking 自动化 (异步非阻塞 DuckDuckGo 搜索矩阵)
3. Favicon MurmurHash3 与 MD5 指纹追踪 (多引擎语法自适配，精准发现同源克隆站群)
4. 高性能异步并发探活与目标画像分析 (Title, Server Banner, CDN/WAF 检测, 页面特征与状态机判定)
5. 结构化输出资产清单 (targets_discovered.csv, targets_summary.json)
"""

import asyncio
import base64
import csv
import hashlib
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

# ==================== 1. 全局配置中心 ====================
class Config:
    # ---------------- 8 大网络空间测绘平台 API 密钥 ----------------
    # 国际平台
    SHODAN_KEY = os.getenv("SHODAN_KEY", "")          # https://account.shodan.io
    CENSYS_ID = os.getenv("CENSYS_ID", "")            # https://search.censys.io/account/api
    CENSYS_SECRET = os.getenv("CENSYS_SECRET", "")
    NETLAS_KEY = os.getenv("NETLAS_KEY", "")          # https://app.netlas.io/profile
    CRIMINALIP_KEY = os.getenv("CRIMINALIP_KEY", "")  # https://www.criminalip.io/mypage/information
    ZOOMEYE_KEY = os.getenv("ZOOMEYE_KEY", "")        # https://www.zoomeye.org/profile

    # 国内平台
    FOFA_EMAIL = os.getenv("FOFA_EMAIL", "")          # https://fofa.info/personalData
    FOFA_KEY = os.getenv("FOFA_KEY", "")
    HUNTER_KEY = os.getenv("HUNTER_KEY", "")          # https://hunter.qianxin.com
    QUAKE_KEY = os.getenv("QUAKE_KEY", "")            # https://quake.360.net/quake/#/personalCenter

    # ---------------- 搜索引擎 Dorking 规则矩阵 ----------------
    DORK_QUERIES = [
        '"Telegram" "tdata" "session+json" ("buy" OR "купить")',
        '"Telegram" "session+json" ("in stock" OR "в наличии")',
        '"Quality accounts at the best prices" "Telegram"',
        '"?request=buy" "session+json" "Telegram"',
        '"Telegram" "tdata" ("FreeKassa" OR "Cryptomus" OR "AAIO" OR "Payeer") site:store OR site:net OR site:site OR site:ru',
        'intitle:"Купить аккаунты Telegram" "session"',
        'intitle:"Telegram accounts shop" "tdata"'
    ]

    # ---------------- 种子网站 (用于计算图标 Hash 和同源关联) ----------------
    SEED_SHOPS = [
        "https://accstore.site/",
        "https://retriv.store/",
        "https://inet-shop.net/"
    ]

    # ---------------- 扫描与探活参数 ----------------
    CONCURRENCY_LIMIT = 30
    HTTP_TIMEOUT = 12.0
    PAGE_SIZE_PER_ENGINE = 50
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("TGGlobalRecon")


# ==================== 2. Favicon 图标指纹计算工具 ====================
class FaviconFingerprint:
    @staticmethod
    def calculate_mmh3(data: bytes) -> int:
        """标准 32 位 MurmurHash3 算法 (Shodan / FOFA / ZoomEye / Quake 通用标准)"""
        length = len(data)
        nblocks = length // 4
        h1 = 0
        c1 = 0xCC9E2D51
        c2 = 0x1B873593

        for i in range(0, nblocks * 4, 4):
            k1 = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16) | (data[i + 3] << 24)
            k1 = (k1 * c1) & 0xFFFFFFFF
            k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
            k1 = (k1 * c2) & 0xFFFFFFFF

            h1 ^= k1
            h1 = ((h1 << 13) | (h1 >> 19)) & 0xFFFFFFFF
            h1 = (h1 * 5 + 0xE6546B64) & 0xFFFFFFFF

        tail = data[nblocks * 4:]
        k1 = 0
        tail_len = len(tail)
        if tail_len >= 3:
            k1 ^= tail[2] << 16
        if tail_len >= 2:
            k1 ^= tail[1] << 8
        if tail_len >= 1:
            k1 ^= tail[0]
            k1 = (k1 * c1) & 0xFFFFFFFF
            k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
            k1 = (k1 * c2) & 0xFFFFFFFF
            h1 ^= k1

        h1 ^= length
        h1 ^= h1 >> 16
        h1 = (h1 * 0x85EBCA6B) & 0xFFFFFFFF
        h1 ^= h1 >> 13
        h1 = (h1 * 0xC2B2AE35) & 0xFFFFFFFF
        h1 ^= h1 >> 16

        if h1 >= 0x80000000:
            h1 = -((0xFFFFFFFF - h1) + 1)
        return h1

    @classmethod
    async def fetch_favicon_hashes(cls, client: httpx.AsyncClient, site_url: str) -> Tuple[Optional[int], Optional[str]]:
        """获取站点的 Favicon 并计算 MurmurHash3 与 MD5 哈希"""
        try:
            resp = await client.get(
                site_url,
                headers={"User-Agent": Config.USER_AGENT},
                follow_redirects=True,
                timeout=8.0
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            
            icon_url = None
            for link in soup.find_all("link"):
                rel = link.get("rel", [])
                if isinstance(rel, list):
                    rel_str = " ".join(rel).lower()
                else:
                    rel_str = str(rel).lower()
                if "icon" in rel_str or "shortcut" in rel_str:
                    href = link.get("href")
                    if href:
                        icon_url = urljoin(str(resp.url), href)
                        break
            
            if not icon_url:
                icon_url = urljoin(str(resp.url), "/favicon.ico")

            icon_resp = await client.get(
                icon_url,
                headers={"User-Agent": Config.USER_AGENT},
                follow_redirects=True,
                timeout=8.0
            )
            
            if icon_resp.status_code == 200 and len(icon_resp.content) > 10:
                # 排除 HTML 报错页作为 icon
                content_type = icon_resp.headers.get("Content-Type", "")
                if "html" not in content_type:
                    # Shodan / FOFA 标准：base64 编码（包含换行）后的 mmh3
                    b64_data = base64.encodebytes(icon_resp.content)
                    mmh3_hash = cls.calculate_mmh3(b64_data)
                    md5_hash = hashlib.md5(icon_resp.content).hexdigest()
                    logger.info(f"[Favicon] {site_url} -> MMH3: {mmh3_hash}, MD5: {md5_hash}")
                    return mmh3_hash, md5_hash
        except Exception as e:
            logger.debug(f"[Favicon] 获取 {site_url} 图标失败: {str(e)}")
        return None, None


# ==================== 3. 全球 8 大网络空间测绘引擎集成 ====================
class GlobalCyberEngines:
    @staticmethod
    def _standardize_target(url: str, source: str, ip: str = "", port: int = 0, country: str = "", raw_title: str = "", server: str = "") -> Dict[str, Any]:
        """统一目标资产的数据结构"""
        parsed = urlparse(url)
        host = parsed.netloc or url
        return {
            "url": url,
            "host": host,
            "ip": ip,
            "port": port if port else (443 if parsed.scheme == "https" else 80),
            "source": source,
            "country": country or "Global",
            "raw_title": raw_title.strip() if raw_title else "",
            "server": server.strip() if server else ""
        }

    # ---------- 1. Shodan (国际) ----------
    @classmethod
    async def query_shodan(cls, client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        if not Config.SHODAN_KEY:
            return []
        url = "https://api.shodan.io/shodan/host/search"
        params = {"key": Config.SHODAN_KEY, "query": query}
        try:
            resp = await client.get(url, params=params, timeout=Config.HTTP_TIMEOUT)
            data = resp.json()
            results = []
            for match in data.get("matches", []):
                ip = match.get("ip_str", "")
                port = match.get("port", 80)
                hostnames = match.get("hostnames", [])
                host = hostnames[0] if hostnames else ip
                scheme = "https" if port in [443, 8443] else "http"
                target_url = f"{scheme}://{host}:{port}" if port not in [80, 443] else f"{scheme}://{host}"
                results.append(cls._standardize_target(
                    url=target_url,
                    source="Shodan",
                    ip=ip,
                    port=port,
                    country=match.get("location", {}).get("country_name", ""),
                    server=match.get("http", {}).get("server", "")
                ))
            logger.info(f"[Shodan] 匹配到 {len(results)} 个资产 (Query: {query[:35]})")
            return results
        except Exception as e:
            logger.warning(f"[Shodan] 查询异常: {str(e)}")
            return []

    # ---------- 2. Censys Search 2.0 (国际) ----------
    @classmethod
    async def query_censys(cls, client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        if not Config.CENSYS_ID or not Config.CENSYS_SECRET:
            return []
        url = "https://search.censys.io/api/v2/hosts/search"
        auth = (Config.CENSYS_ID, Config.CENSYS_SECRET)
        params = {"q": query, "per_page": min(Config.PAGE_SIZE_PER_ENGINE, 50)}
        try:
            resp = await client.get(url, auth=auth, params=params, timeout=Config.HTTP_TIMEOUT)
            data = resp.json()
            results = []
            for hit in data.get("result", {}).get("hits", []):
                ip = hit.get("ip", "")
                services = hit.get("services", [])
                country = hit.get("location", {}).get("country", "")
                for svc in services:
                    port = svc.get("port", 80)
                    svc_name = svc.get("service_name", "").upper()
                    if "HTTP" in svc_name or port in [80, 443, 8080, 8443]:
                        scheme = "https" if "HTTPS" in svc_name or port in [443, 8443] else "http"
                        target_url = f"{scheme}://{ip}:{port}" if port not in [80, 443] else f"{scheme}://{ip}"
                        results.append(cls._standardize_target(
                            url=target_url,
                            source="Censys",
                            ip=ip,
                            port=port,
                            country=country
                        ))
            logger.info(f"[Censys] 匹配到 {len(results)} 个资产 (Query: {query[:35]})")
            return results
        except Exception as e:
            logger.warning(f"[Censys] 查询异常: {str(e)}")
            return []

    # ---------- 3. Netlas.io (国际) ----------
    @classmethod
    async def query_netlas(cls, client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        if not Config.NETLAS_KEY:
            return []
        url = "https://app.netlas.io/api/responses/"
        headers = {"X-API-Key": Config.NETLAS_KEY}
        params = {"q": query, "start": 0, "indices": "response"}
        try:
            resp = await client.get(url, headers=headers, params=params, timeout=Config.HTTP_TIMEOUT)
            data = resp.json()
            results = []
            for item in data.get("items", []):
                data_obj = item.get("data", {})
                uri = data_obj.get("uri")
                ip = data_obj.get("ip", "")
                port = data_obj.get("port", 80)
                country = data_obj.get("geo", {}).get("country", "")
                if uri:
                    results.append(cls._standardize_target(
                        url=uri,
                        source="Netlas",
                        ip=ip,
                        port=port,
                        country=country
                    ))
            logger.info(f"[Netlas] 匹配到 {len(results)} 个资产 (Query: {query[:35]})")
            return results
        except Exception as e:
            logger.warning(f"[Netlas] 查询异常: {str(e)}")
            return []

    # ---------- 4. Criminal IP (国际) ----------
    @classmethod
    async def query_criminalip(cls, client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        if not Config.CRIMINALIP_KEY:
            return []
        url = "https://api.criminalip.io/v1/banner/search"
        headers = {"x-api-key": Config.CRIMINALIP_KEY}
        params = {"query": query, "offset": 0}
        try:
            resp = await client.get(url, headers=headers, params=params, timeout=Config.HTTP_TIMEOUT)
            data = resp.json()
            results = []
            for item in data.get("data", {}).get("result", []):
                ip = item.get("ip_address", "")
                port = item.get("port", 80)
                hostname = item.get("hostname", ip)
                country = item.get("country", "")
                scheme = "https" if port in [443, 8443] else "http"
                target_url = f"{scheme}://{hostname}:{port}" if port not in [80, 443] else f"{scheme}://{hostname}"
                results.append(cls._standardize_target(
                    url=target_url,
                    source="CriminalIP",
                    ip=ip,
                    port=port,
                    country=country
                ))
            logger.info(f"[CriminalIP] 匹配到 {len(results)} 个资产 (Query: {query[:35]})")
            return results
        except Exception as e:
            logger.warning(f"[CriminalIP] 查询异常: {str(e)}")
            return []

    # ---------- 5. ZoomEye 钟馗之眼 (国际/国内) ----------
    @classmethod
    async def query_zoomeye(cls, client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        if not Config.ZOOMEYE_KEY:
            return []
        url = "https://api.zoomeye.org/host/search"
        headers = {"API-KEY": Config.ZOOMEYE_KEY}
        params = {"query": query, "page": 1}
        try:
            resp = await client.get(url, headers=headers, params=params, timeout=Config.HTTP_TIMEOUT)
            data = resp.json()
            results = []
            for match in data.get("matches", []):
                ip = match.get("ip", "")
                portinfo = match.get("portinfo", {})
                port = portinfo.get("port", 80)
                hostname = portinfo.get("hostname", ip)
                service = portinfo.get("service", "").lower()
                scheme = "https" if "https" in service or port in [443, 8443] else "http"
                target_url = f"{scheme}://{hostname}:{port}" if port not in [80, 443] else f"{scheme}://{hostname}"
                country = match.get("geoinfo", {}).get("country", {}).get("name", "")
                title = portinfo.get("title", "")
                results.append(cls._standardize_target(
                    url=target_url,
                    source="ZoomEye",
                    ip=ip,
                    port=port,
                    country=country,
                    raw_title=str(title)
                ))
            logger.info(f"[ZoomEye] 匹配到 {len(results)} 个资产 (Query: {query[:35]})")
            return results
        except Exception as e:
            logger.warning(f"[ZoomEye] 查询异常: {str(e)}")
            return []

    # ---------- 6. FOFA (国内/全球) ----------
    @classmethod
    async def query_fofa(cls, client: httpx.AsyncClient, query_rule: str, size: int = 50) -> List[Dict[str, Any]]:
        if not Config.FOFA_KEY:
            return []
        qbase64 = base64.b64encode(query_rule.encode("utf-8")).decode("utf-8")
        url = "https://fofa.info/api/v1/search/all"
        params = {
            "email": Config.FOFA_EMAIL,
            "key": Config.FOFA_KEY,
            "qbase64": qbase64,
            "size": size,
            "fields": "host,title,ip,port,country_name,server"
        }
        try:
            resp = await client.get(url, params=params, timeout=Config.HTTP_TIMEOUT)
            data = resp.json()
            if data.get("error"):
                logger.error(f"[FOFA] 查询报错: {data.get('errmsg')}")
                return []
            results = []
            for item in data.get("results", []):
                host, title, ip, port, country, server = item
                if not host.startswith("http://") and not host.startswith("https://"):
                    target_url = f"https://{host}" if str(port) == "443" else f"http://{host}"
                else:
                    target_url = host
                results.append(cls._standardize_target(
                    url=target_url,
                    source="FOFA",
                    ip=ip,
                    port=int(port) if str(port).isdigit() else 80,
                    country=country,
                    raw_title=title,
                    server=server
                ))
            logger.info(f"[FOFA] 匹配到 {len(results)} 个资产 (Rule: {query_rule[:35]})")
            return results
        except Exception as e:
            logger.warning(f"[FOFA] 查询异常: {str(e)}")
            return []

    # ---------- 7. 奇安信 Hunter 鹰图 (国内/全球) ----------
    @classmethod
    async def query_hunter(cls, client: httpx.AsyncClient, query_rule: str, size: int = 50) -> List[Dict[str, Any]]:
        if not Config.HUNTER_KEY:
            return []
        qbase64 = base64.urlsafe_b64encode(query_rule.encode("utf-8")).decode("utf-8")
        url = "https://hunter.qianxin.com/openApi/search"
        params = {
            "api-key": Config.HUNTER_KEY,
            "search": qbase64,
            "page": 1,
            "page_size": min(size, 100),
            "is_web": 1
        }
        try:
            resp = await client.get(url, params=params, timeout=Config.HTTP_TIMEOUT)
            data = resp.json()
            if data.get("code") != 200:
                logger.error(f"[Hunter] 错误码 {data.get('code')}: {data.get('message')}")
                return []
            arr = data.get("data", {}).get("arr", [])
            results = []
            for item in arr:
                target_url = item.get("url")
                if target_url:
                    results.append(cls._standardize_target(
                        url=target_url,
                        source="Hunter",
                        ip=item.get("ip", ""),
                        port=item.get("port", 80),
                        country=item.get("country", ""),
                        raw_title=item.get("web_title", "")
                    ))
            logger.info(f"[Hunter] 匹配到 {len(results)} 个资产 (Rule: {query_rule[:35]})")
            return results
        except Exception as e:
            logger.warning(f"[Hunter] 查询异常: {str(e)}")
            return []

    # ---------- 8. 360 Quake 夸克 (国内/全球) ----------
    @classmethod
    async def query_quake(cls, client: httpx.AsyncClient, query_rule: str, size: int = 50) -> List[Dict[str, Any]]:
        if not Config.QUAKE_KEY:
            return []
        url = "https://quake.360.net/api/v3/search/quake_service"
        headers = {
            "X-QuakeToken": Config.QUAKE_KEY,
            "Content-Type": "application/json"
        }
        body = {
            "query": query_rule,
            "start": 0,
            "size": min(size, 100),
            "ignore_cache": False
        }
        try:
            resp = await client.post(url, headers=headers, json=body, timeout=Config.HTTP_TIMEOUT)
            data = resp.json()
            if data.get("code") != 0:
                logger.error(f"[Quake] 错误: {data.get('message')}")
                return []
            results = []
            for item in data.get("data", []):
                ip = item.get("ip", "")
                port = item.get("port", 80)
                service = item.get("service", {})
                http_info = service.get("http", {})
                host = http_info.get("host") or ip
                title = http_info.get("title", "")
                server = http_info.get("server", "")
                scheme = "https" if port in [443, 8443] else "http"
                target_url = f"{scheme}://{host}:{port}" if port not in [80, 443] else f"{scheme}://{host}"
                country = item.get("location", {}).get("country_cn", "")
                results.append(cls._standardize_target(
                    url=target_url,
                    source="Quake",
                    ip=ip,
                    port=port,
                    country=country,
                    raw_title=title,
                    server=server
                ))
            logger.info(f"[Quake] 匹配到 {len(results)} 个资产 (Rule: {query_rule[:35]})")
            return results
        except Exception as e:
            logger.warning(f"[Quake] 查询异常: {str(e)}")
            return []


# ==================== 4. 搜索引擎 Dorking 自动化 (异步非阻塞) ====================
class SearchDorkingEngine:
    @staticmethod
    def _run_single_dork(query: str, max_results: int = 20) -> List[Dict[str, Any]]:
        hits_list = []
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
                for h in results:
                    u = h.get("href")
                    if u:
                        hits_list.append({
                            "url": u,
                            "source": "DuckDuckGo Dork",
                            "raw_title": h.get("title", ""),
                            "country": "Global"
                        })
        except Exception as ex:
            logger.debug(f"[Dorking] '{query[:30]}' 异常: {str(ex)}")
        return hits_list

    @classmethod
    async def run_dorks_async(cls, queries: List[str], max_per_query: int = 20) -> List[Dict[str, Any]]:
        """在异步线程池中执行 Dorking 搜索，不阻塞事件循环"""
        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(None, cls._run_single_dork, q, max_per_query)
            for q in queries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_dork_hits = []
        for res in results:
            if isinstance(res, list):
                all_dork_hits.extend(res)
        logger.info(f"[Dorking] 搜索引擎矩阵聚合发现 {len(all_dork_hits)} 个候选链接")
        return all_dork_hits


# ==================== 5. 目标资产深度探活与画像分析器 ====================
class TargetProfiler:
    """
    对发现的资产进行快速探活、状态机分类与指纹识别：
    状态机分类：
      - ACTIVE_SHOP: 活跃发卡/商城 (包含 tdata, session, buy, stock 等核心特征)
      - LOGIN_WALL: 登录墙 / 私域防护 (需要账号密码/授权方可查看货架)
      - CLOUDFLARE_PROTECTED: 套了 CF 5秒盾 / 人机验证
      - CATALOG_OR_BLOG: 聚合目录/评测/博客页面
      - INACTIVE / ERROR: 无法连接、超时或返回 4xx/5xx
    """

    @staticmethod
    async def profile_target(client: httpx.AsyncClient, target: Dict[str, Any], semaphore: asyncio.Semaphore) -> Dict[str, Any]:
        url = target["url"]
        result = {
            "url": url,
            "domain": urlparse(url).netloc,
            "source": target.get("source", "Unknown"),
            "country": target.get("country", "Unknown"),
            "ip": target.get("ip", ""),
            "port": target.get("port", 0),
            "status_code": 0,
            "response_time_ms": 0,
            "page_title": target.get("raw_title", ""),
            "server_header": target.get("server", ""),
            "category": "UNVERIFIED",
            "is_alive": False,
            "has_telegram_keywords": False,
            "security_shield": "None",
            "matched_signals": []
        }

        async with semaphore:
            start_time = asyncio.get_event_loop().time()
            try:
                resp = await client.get(
                    url,
                    headers={"User-Agent": Config.USER_AGENT},
                    follow_redirects=True,
                    timeout=Config.HTTP_TIMEOUT
                )
                elapsed_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
                result["response_time_ms"] = elapsed_ms
                result["status_code"] = resp.status_code
                result["url"] = str(resp.url)
                result["domain"] = urlparse(str(resp.url)).netloc
                result["server_header"] = resp.headers.get("Server", result["server_header"])

                if resp.status_code in [200, 301, 302, 403]:
                    result["is_alive"] = True

                # 检测 Cloudflare / WAF 盾
                if "cloudflare" in result["server_header"].lower() or "cf-ray" in resp.headers:
                    result["security_shield"] = "Cloudflare"
                if resp.status_code == 403 and ("just a moment" in resp.text.lower() or "cf-mitigated" in resp.headers):
                    result["category"] = "CLOUDFLARE_PROTECTED"
                    result["matched_signals"].append("CF-Challenge-403")
                    return result

                html = resp.text.lower()
                soup = BeautifulSoup(resp.text, "html.parser")
                title = soup.title.string.strip() if soup.title and soup.title.string else result["page_title"]
                result["page_title"] = title.replace("\n", " ")[:80]

                # 提取元数据
                signals = []
                # 核心关键词特征匹配
                tg_match = "telegram" in html or "телеграм" in html
                tdata_match = "tdata" in html or "session+json" in html or ".session" in html
                ecommerce_match = any(k in html for k in ["buy", "купить", "in stock", "в наличии", "pcs.", "шт.", "price", "цена"])
                login_match = any(k in html for k in ["login", "sign in", "войти", "авторизация"])

                if tg_match:
                    signals.append("Telegram-Mention")
                    result["has_telegram_keywords"] = True
                if tdata_match:
                    signals.append("Format-TData/Session")
                if ecommerce_match:
                    signals.append("E-Commerce-Words")

                # 判定分类
                if tg_match and (tdata_match or (ecommerce_match and len(signals) >= 2)):
                    result["category"] = "ACTIVE_SHOP"
                elif login_match and len(soup.find_all("input", type="password")) > 0:
                    result["category"] = "LOGIN_WALL"
                elif tg_match or tdata_match:
                    result["category"] = "CATALOG_OR_INFO"
                else:
                    result["category"] = "GENERIC_WEB"

                result["matched_signals"] = signals

            except httpx.ConnectTimeout:
                result["category"] = "TIMEOUT"
            except httpx.ConnectError:
                result["category"] = "CONN_REFUSED"
            except Exception as e:
                result["category"] = f"ERROR: {type(e).__name__}"

        return result


# ==================== 6. 核心调度与编排引擎 ====================
class ReconOrchestrator:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(Config.CONCURRENCY_LIMIT)
        self.discovered_targets: List[Dict[str, Any]] = []
        self.seen_hosts: Set[str] = set()
        self.profiled_assets: List[Dict[str, Any]] = []

    def add_target(self, target: Dict[str, Any]):
        """根据 Host 进行去重聚合"""
        url = target.get("url")
        if not url:
            return
        parsed = urlparse(url)
        host_key = parsed.netloc.lower() or url.lower()
        if host_key and host_key not in self.seen_hosts:
            self.seen_hosts.add(host_key)
            self.discovered_targets.append(target)

    async def run(self):
        print("\n" + "=" * 90)
        print("🌐 TG-Shop Recon Global: 全球网络空间测绘与资产发现系统启动")
        print("=" * 90)

        # 检查配置生效情况
        configured_engines = []
        if Config.SHODAN_KEY: configured_engines.append("Shodan")
        if Config.CENSYS_ID and Config.CENSYS_SECRET: configured_engines.append("Censys")
        if Config.NETLAS_KEY: configured_engines.append("Netlas")
        if Config.CRIMINALIP_KEY: configured_engines.append("CriminalIP")
        if Config.ZOOMEYE_KEY: configured_engines.append("ZoomEye")
        if Config.FOFA_KEY: configured_engines.append("FOFA")
        if Config.HUNTER_KEY: configured_engines.append("Hunter")
        if Config.QUAKE_KEY: configured_engines.append("Quake")

        logger.info(f"[*] 已配置并激活的测绘引擎 ({len(configured_engines)}/8): {', '.join(configured_engines) if configured_engines else '未配置API Key (仅使用种子与Dorking)'}")

        async with httpx.AsyncClient(verify=False, follow_redirects=True) as http_client:
            # 1. 加载预设种子站
            for seed in Config.SEED_SHOPS:
                self.add_target({
                    "url": seed,
                    "source": "Preset Seed",
                    "country": "Global"
                })

            # 2. 提取种子站 Favicon Hash
            logger.info(">>> [阶段 1] 正在提取种子站点的 Favicon 图标指纹 (MMH3 & MD5)...")
            seed_hashes: List[Tuple[int, str]] = []
            for seed in Config.SEED_SHOPS:
                mmh3_h, md5_h = await FaviconFingerprint.fetch_favicon_hashes(http_client, seed)
                if mmh3_h:
                    seed_hashes.append((mmh3_h, md5_h or ""))

            # 3. 构建并并发打满 8 大测绘引擎
            logger.info(">>> [阶段 2] 全球 8 大网络空间测绘引擎并发下发资产扫描...")
            engine_tasks = []

            # (1) FOFA 任务
            fofa_rules = [
                'body="session+json" && body="tdata"',
                'body="Telegram" && (body="session+json" || body="tdata") && (body="В наличии" || body="in stock" || body="buy")',
                'title="Купить аккаунты Telegram"'
            ]
            for r in fofa_rules:
                engine_tasks.append(GlobalCyberEngines.query_fofa(http_client, r, size=Config.PAGE_SIZE_PER_ENGINE))
            for mmh3_h, _ in seed_hashes:
                engine_tasks.append(GlobalCyberEngines.query_fofa(http_client, f'icon_hash="{mmh3_h}"', size=30))

            # (2) Hunter 任务
            hunter_rules = [
                'body="session+json" && body="tdata"',
                'web.title="Telegram" && body="session"'
            ]
            for r in hunter_rules:
                engine_tasks.append(GlobalCyberEngines.query_hunter(http_client, r, size=Config.PAGE_SIZE_PER_ENGINE))

            # (3) Quake 任务
            quake_rules = [
                'body: "session+json" AND body: "tdata"',
                'title: "Telegram" AND body: "session"'
            ]
            for r in quake_rules:
                engine_tasks.append(GlobalCyberEngines.query_quake(http_client, r, size=Config.PAGE_SIZE_PER_ENGINE))

            # (4) Shodan 任务
            for mmh3_h, _ in seed_hashes:
                engine_tasks.append(GlobalCyberEngines.query_shodan(http_client, f"http.favicon.hash:{mmh3_h}"))
            engine_tasks.append(GlobalCyberEngines.query_shodan(http_client, '"session+json" "tdata"'))

            # (5) ZoomEye 任务
            for mmh3_h, _ in seed_hashes:
                engine_tasks.append(GlobalCyberEngines.query_zoomeye(http_client, f'iconhash:"{mmh3_h}"'))
            engine_tasks.append(GlobalCyberEngines.query_zoomeye(http_client, 'title:"Telegram" +body:"session"'))

            # (6) Censys 任务
            engine_tasks.append(GlobalCyberEngines.query_censys(http_client, 'services.http.response.body: "session+json" and services.http.response.body: "tdata"'))

            # (7) Netlas 任务
            engine_tasks.append(GlobalCyberEngines.query_netlas(http_client, 'http.body:"session+json" AND http.body:"tdata"'))

            # (8) Criminal IP 任务
            engine_tasks.append(GlobalCyberEngines.query_criminalip(http_client, 'session+json tdata'))

            # 执行所有测绘任务
            engine_results = await asyncio.gather(*engine_tasks, return_exceptions=True)
            for res in engine_results:
                if isinstance(res, list):
                    for item in res:
                        self.add_target(item)

            # 4. 执行搜索引擎 Dorking 矩阵 (异步执行)
            logger.info(">>> [阶段 3] 执行多语法搜索引擎 Dorking 规则矩阵...")
            dork_hits = await SearchDorkingEngine.run_dorks_async(Config.DORK_QUERIES, max_per_query=15)
            for item in dork_hits:
                self.add_target(item)

            total_discovered = len(self.discovered_targets)
            logger.info(f"[+] 多源聚合完成！全局去重后获得 {total_discovered} 个独立目标资产，进入探活与画像分析阶段...")

            # 5. 并发异步探活与资产画像
            logger.info(">>> [阶段 4] 启动高并发异步探活与特征画像...")
            profile_tasks = [
                TargetProfiler.profile_target(http_client, target, self.semaphore)
                for target in self.discovered_targets
            ]
            self.profiled_assets = await asyncio.gather(*profile_tasks)

        # 6. 生成分析报表与结构化导出
        self.export_and_report()

    def export_and_report(self):
        logger.info(">>> [阶段 5] 正在生成全球资产拓扑与发现报表...")

        csv_path = "targets_discovered.csv"
        json_path = "targets_summary.json"

        # 过滤与统计
        active_shops = [a for a in self.profiled_assets if a["category"] == "ACTIVE_SHOP"]
        login_walls = [a for a in self.profiled_assets if a["category"] == "LOGIN_WALL"]
        cf_shields = [a for a in self.profiled_assets if a["category"] == "CLOUDFLARE_PROTECTED"]
        alive_total = [a for a in self.profiled_assets if a["is_alive"]]

        # 写入 CSV 资产清单
        if self.profiled_assets:
            fieldnames = [
                "domain", "url", "category", "is_alive", "status_code",
                "page_title", "country", "source", "ip", "port",
                "response_time_ms", "server_header", "security_shield", "matched_signals"
            ]
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for a in self.profiled_assets:
                    row = {k: a.get(k, "") for k in fieldnames}
                    if isinstance(row["matched_signals"], list):
                        row["matched_signals"] = "|".join(row["matched_signals"])
                    writer.writerow(row)
            logger.info(f"[✓] 完整资产清单已导出 -> {csv_path} (共 {len(self.profiled_assets)} 条记录)")

        # 写入 JSON 结构化报告
        summary_payload = {
            "summary": {
                "total_targets_discovered": len(self.profiled_assets),
                "total_alive_targets": len(alive_total),
                "active_shops_count": len(active_shops),
                "login_walls_count": len(login_walls),
                "cloudflare_protected_count": len(cf_shields),
            },
            "active_shops": active_shops,
            "all_assets": self.profiled_assets
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary_payload, f, ensure_ascii=False, indent=2)
        logger.info(f"[✓] 结构化汇总报表已生成 -> {json_path}")

        # 控制台输出直观报表
        print("\n" + "=" * 105)
        print("🎯【全球 Telegram 资产与目标商城发现汇总报告】")
        print("=" * 105)
        print(f"📊 资产总览: 发现独立目标 {len(self.profiled_assets)} 个 | 存活目标 {len(alive_total)} 个 | 活跃货架商城 {len(active_shops)} 个 | 登录墙/私域 {len(login_walls)} 个")
        print("-" * 105)
        print(f"{'序号':<4} | {'资产分类':<18} | {'响应/状态':<10} | {'国家/地区':<12} | {'来源引擎':<14} | {'域名 / URL'}")
        print("-" * 105)

        # 优先展示活跃商城和存活站点
        sorted_for_display = sorted(
            self.profiled_assets,
            key=lambda x: (
                0 if x["category"] == "ACTIVE_SHOP" else (
                    1 if x["category"] == "LOGIN_WALL" else (
                        2 if x["is_alive"] else 3
                    )
                )
            )
        )

        for idx, a in enumerate(sorted_for_display[:25], start=1):
            cat_display = a["category"][:16]
            stat_display = f"{a['status_code']} ({a['response_time_ms']}ms)" if a['is_alive'] else "DOWN"
            geo_display = a["country"][:10]
            src_display = a["source"][:12]
            url_display = a["url"][:42]
            print(f"{idx:<4} | {cat_display:<18} | {stat_display:<10} | {geo_display:<12} | {src_display:<14} | {url_display}")

        if len(sorted_for_display) > 25:
            print(f"... 还有 {len(sorted_for_display) - 25} 个目标资产已保存至 {csv_path}")
        print("=" * 105 + "\n")


if __name__ == "__main__":
    asyncio.run(ReconOrchestrator().run())
