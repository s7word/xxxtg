import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.scripts.warp_ps_hop import (
    _safe_bind_ips,
    parse_socks_host,
    socks5_connect_request,
    wait_for_docker_bridge_binds,
)


def test_socks5_connect_request_uses_domain_atyp():
    req = socks5_connect_request("res.proxy-seller.com", 10003)
    assert req[:4] == b"\x05\x01\x00\x03"
    host = b"res.proxy-seller.com"
    assert req[4] == len(host)
    assert req[5 : 5 + len(host)] == host
    assert req[-2:] == (10003).to_bytes(2, "big")


def test_parse_socks_host_accepts_url_and_hostport():
    assert parse_socks_host("127.0.0.1:41080") == ("127.0.0.1", 41080)
    assert parse_socks_host("socks5h://127.0.0.1:41080") == ("127.0.0.1", 41080)
    assert parse_socks_host("socks5://127.0.0.1") == ("127.0.0.1", 41080)


def test_safe_bind_ips_keeps_private_only():
    assert _safe_bind_ips(["127.0.0.1", "172.22.0.1", "8.8.8.8"]) == [
        "127.0.0.1",
        "172.22.0.1",
    ]


def test_wait_for_docker_bridge_binds_returns_when_bridge_appears():
    seq = iter([["127.0.0.1"], ["127.0.0.1"], ["127.0.0.1", "172.22.0.1"]])

    def fake():
        return next(seq)

    async def run():
        with patch("backend.scripts.warp_ps_hop.iter_private_bridge_binds", fake):
            return await wait_for_docker_bridge_binds(timeout=2.0, interval=0.01)

    assert asyncio.run(run()) == ["127.0.0.1", "172.22.0.1"]
