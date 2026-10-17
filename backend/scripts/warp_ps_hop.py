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


def _safe_bind_ips(candidates: Sequence[str]) -> List[str]:
    safe: List[str] = []
    for ip in candidates:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            LOG.error("skipping invalid bind address %s", ip)
            continue
        if addr.is_loopback or addr.is_private:
            if ip not in safe:
                safe.append(ip)
        else:
            LOG.error("refusing to bind public address %s", ip)
    return safe


async def wait_for_docker_bridge_binds(timeout: float = 30.0, interval: float = 0.5) -> List[str]:
    """Boot race: hop 常比 compose 网桥先起来，等 172.22.0.1 出现再绑。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout))
    last = iter_private_bridge_binds()
    while loop.time() < deadline:
        last = iter_private_bridge_binds()
        if any(ip != "127.0.0.1" for ip in last):
            return last
        await asyncio.sleep(interval)
    return last


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


async def bind_missing(
    *,
    bound: set,
    servers: List[asyncio.AbstractServer],
    serve_tasks: set,
    ips: Sequence[str],
    ports: Sequence[int],
    kwargs: dict,
) -> List[Tuple[str, int]]:
    added: List[Tuple[str, int]] = []
    for bind in _safe_bind_ips(ips):
        for port in ports:
            key = (bind, int(port))
            if key in bound:
                continue
            try:
                srv = await serve_port(bind, int(port), **kwargs)
            except OSError as exc:
                LOG.warning("listen failed %s:%s: %s", bind, port, exc)
                continue
            servers.append(srv)
            bound.add(key)
            task = asyncio.create_task(srv.serve_forever())
            serve_tasks.add(task)
            task.add_done_callback(serve_tasks.discard)
            added.append(key)
    return added


async def amain(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    binds = list(args.bind)
    auto = bool(args.auto_bind or not binds)
    if auto:
        for ip in iter_private_bridge_binds():
            if ip not in binds:
                binds.append(ip)
        if all(ip == "127.0.0.1" for ip in binds):
            LOG.info("docker bridge not up yet; waiting up to 30s for 172.22.0.1")
            for ip in await wait_for_docker_bridge_binds(timeout=30.0):
                if ip not in binds:
                    binds.append(ip)
    safe = _safe_bind_ips(binds)
    if not safe:
        LOG.error("no safe bind addresses")
        return 2
    warp_host, warp_port = parse_socks_host(args.warp)
    start = int(args.start_port)
    count = max(1, int(args.count))
    ports = list(range(start, start + count))
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
    serve_tasks: set = set()
    bound: set = set()
    hop_kwargs = {
        "upstream": args.upstream,
        "warp_host": warp_host,
        "warp_port": warp_port,
        "timeout": float(args.timeout),
    }
    added = await bind_missing(
        bound=bound,
        servers=servers,
        serve_tasks=serve_tasks,
        ips=safe,
        ports=ports,
        kwargs=hop_kwargs,
    )
    if not added:
        LOG.error("listen failed on every bind/port")
        return 1

    async def rebind_loop() -> None:
        while True:
            await asyncio.sleep(15.0)
            ips = iter_private_bridge_binds() if auto else list(args.bind)
            extra = await bind_missing(
                bound=bound,
                servers=servers,
                serve_tasks=serve_tasks,
                ips=ips,
                ports=ports,
                kwargs=hop_kwargs,
            )
            if extra:
                hosts = sorted({ip for ip, _ in extra})
                LOG.info("rebind added %s ports on %s", len(extra), ",".join(hosts))

    rebind_task = asyncio.create_task(rebind_loop())
    try:
        await rebind_task
    except asyncio.CancelledError:
        pass
    finally:
        rebind_task.cancel()
        for task in list(serve_tasks):
            task.cancel()
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
