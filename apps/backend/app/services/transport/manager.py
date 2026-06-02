import asyncio
import concurrent.futures
import contextlib
import os
import socket
import threading
from typing import Optional

from app.services.curl_socks import measure_download_via_socks5, probe_latency_via_socks5

from .http_proxy import HttpProxyServer
from .packet_templates import ClientHelloMaker
from .socks5 import Socks5Server
from .tcp_injector import TcpInjector, FakeInjectiveConnection

_injector_thread: Optional[threading.Thread] = None
_injector: Optional[TcpInjector] = None
_proxy_thread: Optional[threading.Thread] = None
_connections: dict = {}
_running = False
_socks_server: Optional[Socks5Server] = None
_http_server: Optional[HttpProxyServer] = None
_socks_loop: Optional[asyncio.AbstractEventLoop] = None
_active_socks_port: Optional[int] = None
_active_http_port: Optional[int] = None
_target_connect_ip: Optional[str] = None
_target_connect_port: Optional[int] = None
_target_fake_sni: bytes = b""


def start_injector(
        interface_ipv4: str,
        connect_ip: str,
        connect_port: int,
        fake_sni: bytes,
        socks_port: Optional[int],
        http_port: Optional[int],
        listen_host: str = "127.0.0.1",
        start_local_proxies: bool = True,
):
    """Launch the WinDivert injector and local SOCKS/HTTP proxies."""
    global _injector_thread, _injector, _proxy_thread, _connections, _running, _socks_server, _http_server, _socks_loop, _active_socks_port, _active_http_port, _target_connect_ip, _target_connect_port, _target_fake_sni

    if _running:
        stop_injector()

    _connections.clear()
    connect_port = int(connect_port)
    if not str(connect_ip).strip():
        raise ValueError("connect_ip is required")
    if not fake_sni:
        raise ValueError("fake_sni is required")

    w_filter = (
        f"tcp and ((ip.SrcAddr == {interface_ipv4} and ip.DstAddr == {connect_ip} and tcp.DstPort == {connect_port}) or "
        f"(ip.SrcAddr == {connect_ip} and ip.DstAddr == {interface_ipv4} and tcp.SrcPort == {connect_port}))"
    )

    injector = TcpInjector(
        w_filter,
        _connections,
        fake_sni=fake_sni,
        auto_monitor=not start_local_proxies,
        match_port=connect_port,
    )
    _injector = injector
    _running = True
    _active_socks_port = socks_port
    _active_http_port = http_port
    _target_connect_ip = connect_ip
    _target_connect_port = connect_port
    _target_fake_sni = fake_sni

    # Start WinDivert capture thread
    def _run():
        with contextlib.suppress(Exception):
            injector.run()

    _injector_thread = threading.Thread(target=_run, daemon=True)
    _injector_thread.start()

    if not start_local_proxies:
        _proxy_thread = None
        _socks_server = None
        _http_server = None
        _socks_loop = None
        return True

    if socks_port is None or http_port is None:
        raise ValueError("socks_port and http_port are required when start_local_proxies=True")

    # Start local proxies in their own asyncio loop.
    _socks_loop = asyncio.new_event_loop()
    _socks_server = Socks5Server(listen_host, socks_port, interface_ipv4, fake_sni)
    _http_server = HttpProxyServer(listen_host, http_port, interface_ipv4, fake_sni)

    def _run_proxy():
        loop = _socks_loop
        socks_server = _socks_server
        http_server = _http_server
        if loop is None or socks_server is None or http_server is None:
            return
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(asyncio.gather(socks_server.start(), http_server.start()))
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    _proxy_thread = threading.Thread(target=_run_proxy, daemon=True)
    _proxy_thread.start()
    return True


def stop_injector():
    """Stop WinDivert injector and SOCKS5 proxy."""
    global _running, _injector_thread, _injector, _proxy_thread, _socks_server, _http_server, _socks_loop, _active_socks_port, _active_http_port, _target_connect_ip, _target_connect_port, _target_fake_sni
    _running = False

    if _injector is not None:
        _injector.stop()

    if _socks_loop and not _socks_loop.is_closed():
        async def _stop():
            tasks = []
            if _socks_server:
                tasks.append(_socks_server.stop())
            if _http_server:
                tasks.append(_http_server.stop())
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            future = asyncio.run_coroutine_threadsafe(_stop(), _socks_loop)
            future.result(timeout=2)
        except (RuntimeError, concurrent.futures.TimeoutError):
            pass
        finally:
            with contextlib.suppress(RuntimeError):
                _socks_loop.call_soon_threadsafe(_socks_loop.stop)

    if _proxy_thread and _proxy_thread.is_alive():
        _proxy_thread.join(timeout=2)

    if _injector_thread and _injector_thread.is_alive():
        _injector_thread.join(timeout=2)

    _injector_thread = None
    _injector = None
    _proxy_thread = None
    _socks_server = None
    _http_server = None
    _socks_loop = None
    _connections.clear()
    _active_socks_port = None
    _active_http_port = None
    _target_connect_ip = None
    _target_connect_port = None
    _target_fake_sni = b""


def get_active_socks_port() -> Optional[int]:
    """Return the SOCKS port currently used by the injector, if any."""
    return _active_socks_port


def get_active_http_port() -> Optional[int]:
    """Return the HTTP port currently used by the injector, if any."""
    return _active_http_port


def get_injector_target() -> tuple[Optional[str], Optional[int]]:
    """Return injector target tuple used for tcp-inject mode."""
    return _target_connect_ip, _target_connect_port


def get_injector_diagnostics() -> dict:
    """Return lightweight runtime diagnostics for tcp-inject troubleshooting."""
    connect_ip, connect_port = get_injector_target()
    injector_alive = bool(_injector_thread and _injector_thread.is_alive())
    proxy_alive = bool(_proxy_thread and _proxy_thread.is_alive())
    return {
        "running": bool(_running),
        "injectorThreadAlive": injector_alive,
        "proxyThreadAlive": proxy_alive,
        "proxyMode": "local-proxy" if _socks_server is not None or _http_server is not None else "hook-only",
        "activeSocksPort": _active_socks_port,
        "activeHttpPort": _active_http_port,
        "targetConnectIp": connect_ip,
        "targetConnectPort": connect_port,
        "fakeSniConfigured": bool(_target_fake_sni),
        "activeMonitoredConnections": len(_connections),
        "injectorStats": _injector.get_stats() if _injector is not None else {},
    }


async def handle_bridge_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Accept one local bridge connection and relay it through the injected upstream socket."""
    loop = asyncio.get_running_loop()
    interface_ipv4 = _resolve_local_device_ip() or "0.0.0.0"
    peer_sock = writer.get_extra_info("socket")
    success, message, outgoing_sock = await establish_connection(
        loop,
        interface_ipv4,
        _target_connect_ip,
        _target_connect_port,
        _target_fake_sni,
        peer_sock,
    )
    if not success or outgoing_sock is None:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        raise RuntimeError(message)

    remote_reader, remote_writer = await asyncio.open_connection(sock=outgoing_sock)
    await asyncio.gather(
        _relay_with_count(reader, remote_writer, is_upload=True),
        _relay_with_count(remote_reader, writer, is_upload=False),
        return_exceptions=True,
    )


async def _relay_with_count(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, is_upload: bool) -> None:
    from .traffic import _traffic

    try:
        while True:
            data = await reader.read(65535)
            if not data:
                break
            writer.write(data)
            await writer.drain()
            if is_upload:
                _traffic.add_upload(len(data))
            else:
                _traffic.add_download(len(data))
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


def _resolve_local_device_ip() -> str | None:
    candidates: list[str] = []
    with contextlib.suppress(Exception):
        hostname = socket.gethostname()
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = str(sockaddr[0])
            if ip and not ip.startswith("127."):
                candidates.append(ip)
    with contextlib.suppress(Exception):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = str(sock.getsockname()[0])
            if ip and not ip.startswith("127."):
                candidates.append(ip)
    return candidates[0] if candidates else None


def test_delay_via_socks5(timeout_s: float = 3.0) -> float:
    """
    Measure real HTTP latency through the active SOCKS5 proxy.
    """
    port = get_active_socks_port()
    if not port:
        raise RuntimeError("No active SOCKS5 proxy")
    try:
        return float(probe_latency_via_socks5("127.0.0.1", port, timeout_s=timeout_s))
    except Exception as e:
        raise TimeoutError(f"SOCKS5 delay test failed: {e}")


def test_speed_via_socks5(timeout_s: float = 5.0) -> float:
    """
    Download a real payload via SOCKS5 proxy and return throughput (bytes/sec).
    """
    port = get_active_socks_port()
    if not port:
        raise RuntimeError("No active SOCKS5 proxy")
    try:
        return float(measure_download_via_socks5("127.0.0.1", port, timeout_s=timeout_s))
    except Exception as e:
        raise TimeoutError(f"SOCKS5 speed test failed: {e}")


def _socks5_connect(proxy_host: str, proxy_port: int, target_host: str, target_port: int,
                    timeout_s: float) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout_s)
    sock.settimeout(timeout_s)
    try:
        sock.sendall(b"\x05\x01\x00")
        response = sock.recv(2)
        if response != b"\x05\x00":
            raise RuntimeError("SOCKS5 handshake failed")

        host_bytes = target_host.encode("idna")
        request = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + target_port.to_bytes(2, "big")
        sock.sendall(request)
        head = sock.recv(4)
        if len(head) != 4 or head[1] != 0:
            raise RuntimeError("SOCKS5 connect failed")

        atyp = head[3]
        if atyp == 0x01:
            remaining = 4 + 2
        elif atyp == 0x03:
            length = sock.recv(1)
            if not length:
                raise RuntimeError("SOCKS5 malformed response")
            remaining = int(length[0]) + 2
        elif atyp == 0x04:
            remaining = 16 + 2
        else:
            raise RuntimeError("SOCKS5 unknown address type")
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise RuntimeError("SOCKS5 truncated response")
            remaining -= len(chunk)
        return sock
    except Exception:
        sock.close()
        raise


async def establish_connection(
        loop: asyncio.AbstractEventLoop,
        interface_ipv4: str,
        connect_ip: Optional[str],
        connect_port: Optional[int],
        fake_sni: bytes,
        peer_sock: socket.socket
) -> tuple[bool, str, Optional[socket.socket]]:
    """
    Perform the TCP handshake and fake TLS injection.
    Returns (success, message, outgoing_socket) or (False, error, None).
    """
    target_ip = connect_ip
    target_port = connect_port
    if not target_ip or not target_port:
        target_ip, target_port = get_injector_target()
    if not target_ip or not target_port:
        return False, "injector target is not configured", None

    outgoing = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    outgoing.setblocking(False)
    outgoing.bind((interface_ipv4, 0))
    src_port = outgoing.getsockname()[1]

    fake_data = ClientHelloMaker.get_client_hello_with(
        os.urandom(32), os.urandom(32), fake_sni, os.urandom(32)
    )
    conn = FakeInjectiveConnection(
        outgoing, interface_ipv4, target_ip, src_port, int(target_port),
        fake_data, "wrong_seq", peer_sock
    )
    _connections[conn.id] = conn

    try:
        await loop.sock_connect(outgoing, (target_ip, int(target_port)))
    except Exception as e:
        _connections.pop(conn.id, None)
        outgoing.close()
        return False, f"connect failed: {e}", None

    # Wait for the fake packet to be acknowledged (2 sec timeout)
    event_set = await loop.run_in_executor(None, conn.t2a_event.wait, 2.0)
    if not event_set:
        _connections.pop(conn.id, None)
        outgoing.close()
        return False, "timeout waiting for fake ACK", None

    if conn.t2a_msg != "fake_data_ack_recv":
        _connections.pop(conn.id, None)
        outgoing.close()
        return False, f"unexpected state: {conn.t2a_msg}", None

    # Injection successful – stop monitoring this connection
    conn.monitor = False
    _connections.pop(conn.id, None)
    return True, "injection ready", outgoing
