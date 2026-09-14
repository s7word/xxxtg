#!/usr/bin/env python3
"""Forward Proxy-Seller residential ports through Cloudflare WARP SOCKS.

Host traffic stays on the normal default route. Only TCP accepted on the
bound addresses (loopback + Docker bridges) is chained:

    client -> hop:{10000-10999} -> 127.0.0.1:41080 (WARP proxy) -> res.proxy-seller.com:same-port

Do not bind 0.0.0.0: that would publish an open forwarder on the public NIC.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import socket
import sys
from typing import List, Sequence, Tuple

LOG = logging.getLogger("warp-ps-hop")
DEFAULT_UPSTREAM = "res.proxy-seller.com"
DEFAULT_WARP = ("127.0.0.1", 41080)
DEFAULT_START = 10000
DEFAULT_COUNT = 1000


def socks5_connect_request(host: str, port: int) -> bytes:
    host_b = host.encode("idna")
    if len(host_b) > 255:
        raise ValueError(f"hostname too long: {host}")
    return b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + int(port).to_bytes(2, "big")


def parse_socks_host(value: str) -> Tuple[str, int]:
    text = (value or "").strip()
    if text.startswith("socks5h://"):
        text = text[len("socks5h://") :]
    elif text.startswith("socks5://"):
        text = text[len("socks5://") :]
    if ":" not in text:
        return text or "127.0.0.1", DEFAULT_WARP[1]
    host, port_s = text.rsplit(":", 1)
    return host or "127.0.0.1", int(port_s)


def _iface_ipv4s() -> List[str]:
    import subprocess

    try:
        raw = subprocess.check_output(["ip", "-4", "-o", "addr", "show"], text=True)
    except (OSError, subprocess.SubprocessError):
        return []
    found: List[str] = []
    for line in raw.splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        cidr = parts[parts.index("inet") + 1]
        found.append(cidr.split("/", 1)[0])
    return found


def iter_private_bridge_binds() -> List[str]:
    """Listen on loopback plus the xxxtg backend docker-bridge gateway only."""
    found = {"127.0.0.1"}
    iface_ips = set(_iface_ipv4s())
    try:
        import json
        import subprocess

        raw = subprocess.check_output(
            ["docker", "network", "inspect", "xxxtg_edgenode-mesh"],
            text=True,
        )
        data = json.loads(raw)
        gw = data[0]["IPAM"]["Config"][0]["Gateway"]
        if gw in iface_ips:
            found.add(gw)
    except Exception:
        pass
    # Fallback used by the current host compose network.
    if "172.22.0.1" in iface_ips:
        found.add("172.22.0.1")
    return sorted(found, key=lambda x: (x != "127.0.0.1", x))


async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    buf = await reader.readexactly(n)
    return buf


async def socks5_open(
    warp_host: str,
    warp_port: int,
    dest_host: str,
    dest_port: int,
    timeout: float,
) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(warp_host, warp_port),
        timeout=timeout,
    )
    try:
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        hello = await asyncio.wait_for(_read_exact(reader, 2), timeout=timeout)
        if hello != b"\x05\x00":
            raise OSError(f"WARP SOCKS greeting rejected: {hello!r}")
        writer.write(socks5_connect_request(dest_host, dest_port))
        await writer.drain()
        hdr = await asyncio.wait_for(_read_exact(reader, 4), timeout=timeout)
        if len(hdr) < 4 or hdr[0] != 5 or hdr[1] != 0:
            raise OSError(f"WARP SOCKS CONNECT failed: {hdr!r}")
        atyp = hdr[3]
        if atyp == 1:
            await asyncio.wait_for(_read_exact(reader, 4 + 2), timeout=timeout)
        elif atyp == 4:
            await asyncio.wait_for(_read_exact(reader, 16 + 2), timeout=timeout)
        elif atyp == 3:
            ln = await asyncio.wait_for(_read_exact(reader, 1), timeout=timeout)
            await asyncio.wait_for(_read_exact(reader, ln[0] + 2), timeout=timeout)
        else:
            raise OSError(f"unexpected SOCKS atyp={atyp}")
    except Exception:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        raise
    return reader, writer


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await src.read(65536)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            dst.close()
        except Exception:
            pass


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    dest_port: int,
    upstream: str,
    warp_host: str,
    warp_port: int,
    timeout: float,
) -> None:
    peer = writer.get_extra_info("peername")
    try:
        up_reader, up_writer = await socks5_open(
            warp_host, warp_port, upstream, dest_port, timeout
        )
    except Exception as exc:
        LOG.warning("hop open failed port=%s peer=%s err=%s", dest_port, peer, exc)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return
    try:
        await asyncio.gather(
            _pipe(reader, up_writer),
            _pipe(up_reader, writer),
        )
    finally:
        for w in (writer, up_writer):
            try:
                w.close()
            except Exception:
                pass


async def serve_port(
    bind: str,
    dest_port: int,
    **kwargs,
) -> asyncio.AbstractServer:
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, dest_port=dest_port, **kwargs),
        host=bind,
        port=dest_port,
        reuse_address=True,
    )
    return server


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bind", action="append", default=[], help="Listen address (repeatable)")
    p.add_argument("--auto-bind", action="store_true", help="Bind loopback + private docker bridges")
    p.add_argument("--start-port", type=int, default=DEFAULT_START)
    p.add_argument("--count", type=int, default=DEFAULT_COUNT)
    p.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    p.add_argument("--warp", default=f"{DEFAULT_WARP[0]}:{DEFAULT_WARP[1]}")
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


async def amain(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    binds = list(args.bind)
    if args.auto_bind or not binds:
        for ip in iter_private_bridge_binds():
            if ip not in binds:
                binds.append(ip)
    # Never accidentally include a public address if auto-detected wrong.
    safe: List[str] = []
    for ip in binds:
        addr = ipaddress.ip_address(ip)
        if addr.is_loopback or addr.is_private:
            safe.append(ip)
        else:
            LOG.error("refusing to bind public address %s", ip)
    if not safe:
        LOG.error("no safe bind addresses")
        return 2
    warp_host, warp_port = parse_socks_host(args.warp)
    start = int(args.start_port)
    count = max(1, int(args.count))
    ports = range(start, start + count)
    LOG.info(
        "starting hop binds=%s ports=%s-%s via %s:%s -> %s",
        ",".join(safe),
        start,
        start + count - 1,
        warp_host,
        warp_port,
        args.upstream,
    )
    servers: List[asyncio.AbstractServer] = []
    try:
        for bind in safe:
            for port in ports:
                servers.append(
                    await serve_port(
                        bind,
                        port,
                        upstream=args.upstream,
                        warp_host=warp_host,
                        warp_port=warp_port,
                        timeout=float(args.timeout),
                    )
                )
    except OSError as exc:
        LOG.error("listen failed: %s", exc)
        for srv in servers:
            srv.close()
        return 1
    try:
        await asyncio.gather(*(srv.serve_forever() for srv in servers))
    except asyncio.CancelledError:
        pass
    finally:
        for srv in servers:
            srv.close()
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(amain(sys.argv[1:])))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
