import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.scripts.warp_ps_hop import parse_socks_host, socks5_connect_request


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
