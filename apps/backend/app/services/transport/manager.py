from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import threading
import time
from typing import Optional

from .connection_registry import ConnectionRegistry
from .http_proxy import HttpProxyServer
from .monitor_connection import MonitorConnection
from .packet_templates import ClientHelloMaker
from .profile_store import ProfileStore
from .socks5 import Socks5Server
from .tcp_injector import TcpInjector
from .traffic import _traffic

# Global state (simplified)
_injector: Optional[TcpInjector] = None
_injector_thread: Optional[threading.Thread] = None
_registry: Optional[ConnectionRegistry] = None
_profile_store: Optional[ProfileStore] = None
_cleanup_task: Optional[asyncio.Task] = None
_socks_server: Optional[Socks5Server] = None
_http_server: Optional[Socks5Server] = None  # actually HttpProxyServer, but kept type for consistency
_proxy_thread: Optional[threading.Thread] = None
_socks_loop: Optional[asyncio.AbstractEventLoop] = None
_listen_host: str = "127.0.0.1"
_active_socks_port: Optional[int] = None
_active_http_port: Optional[int] = None
_running = False

# Timeout settings (updated from core_settings)
_connect_timeout_ms = 3000
_inject_timeout_ms = 2000
_relay_idle_timeout_ms = 120000
_stale_connection_ms = 30000
_cleanup_interval_ms = 5000

_logger = logging.getLogger("tcp_injector.manager")


def _update_timeouts_from_settings() -> None:
    """Called from runtime.py to pass core settings."""
    global _connect_timeout_ms, _inject_timeout_ms, _relay_idle_timeout_ms, _stale_connection_ms, _cleanup_interval_ms
    # These will be set by runtime._spawn_tcp_inject_locked
    pass


def start_injector(
        interface_ipv4: str,
        connect_ip: str,
        connect_port: int,
        fake_sni: bytes,
        socks_port: Optional[int],
        http_port: Optional[int],
        listen_host: str = "127.0.0.1",
        start_local_proxies: bool = True,
        connect_timeout_ms: int = 3000,
        inject_timeout_ms: int = 2000,
        relay_idle_timeout_ms: int = 120000,
        stale_connection_ms: int = 30000,
        cleanup_interval_ms: int = 5000,
) -> bool:
    """
    Start the broad‑filter TCP injector and optionally local SOCKS/HTTP proxies.
    Unlike the old version, this does NOT use the narrow filter; it creates a single injector
    that will handle any destination IP. The actual target for each connection is determined
    by ProfileStore at connection time.
    """
    global _injector, _injector_thread, _registry, _profile_store, _cleanup_task
    global _socks_server, _http_server, _proxy_thread, _socks_loop, _running
    global _listen_host, _active_socks_port, _active_http_port
    global _connect_timeout_ms, _inject_timeout_ms, _relay_idle_timeout_ms, _stale_connection_ms, _cleanup_interval_ms

    if _running:
        stop_injector()

    # Store timeouts
    _connect_timeout_ms = connect_timeout_ms
    _inject_timeout_ms = inject_timeout_ms
    _relay_idle_timeout_ms = relay_idle_timeout_ms
    _stale_connection_ms = stale_connection_ms
    _cleanup_interval_ms = cleanup_interval_ms
    _listen_host = listen_host
    _running = True

    # Create registry and profile store
    _registry = ConnectionRegistry()
    _profile_store = ProfileStore()  # will read from database

    # Start injector thread
    _injector = TcpInjector(_registry, _logger)
    _injector_thread = threading.Thread(target=_injector.run, name="windivert-injector", daemon=True)
    _injector_thread.start()

    # Start background cleanup task (async)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    _cleanup_task = loop.create_task(_cleanup_loop())

    if not start_local_proxies:
        _active_socks_port = None
        _active_http_port = None
        return True

    if socks_port is None or http_port is None:
        raise ValueError("socks_port and http_port are required when start_local_proxies=True")

    _active_socks_port = socks_port
    _active_http_port = http_port

    # Start local proxies in their own asyncio loop
    _socks_loop = asyncio.new_event_loop()
    _socks_server = Socks5Server(listen_host, socks_port, interface_ipv4, fake_sni)
    _http_server = HttpProxyServer(listen_host, http_port, interface_ipv4, fake_sni)

    def _run_proxy():
        loop = _socks_loop
        socks = _socks_server
        http = _http_server
        if loop is None or socks is None or http is None:
            return
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(asyncio.gather(socks.start(), http.start()))
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    _proxy_thread = threading.Thread(target=_run_proxy, daemon=True)
    _proxy_thread.start()
    return True


def stop_injector() -> None:
    global _injector, _injector_thread, _registry, _profile_store, _cleanup_task
    global _socks_server, _http_server, _proxy_thread, _socks_loop, _running
    global _active_socks_port, _active_http_port

    _running = False

    if _injector is not None:
        _injector.stop()
        _injector = None

    if _cleanup_task is not None:
        _cleanup_task.cancel()
        _cleanup_task = None

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
        except (RuntimeError, asyncio.TimeoutError):
            pass
        finally:
            with contextlib.suppress(RuntimeError):
                _socks_loop.call_soon_threadsafe(_socks_loop.stop)

    if _proxy_thread and _proxy_thread.is_alive():
        _proxy_thread.join(timeout=2)
    if _injector_thread and _injector_thread.is_alive():
        _injector_thread.join(timeout=2)

    _injector_thread = None
    _proxy_thread = None
    _socks_server = None
    _http_server = None
    _socks_loop = None
    _registry = None
    _profile_store = None
    _active_socks_port = None
    _active_http_port = None


async def _cleanup_loop() -> None:
    while _running:
        await asyncio.sleep(_cleanup_interval_ms / 1000.0)
        if _registry is not None:
            removed = _registry.prune(_stale_connection_ms / 1000.0)
            if removed:
                _logger.info("registry_pruned", extra={"event_name": "registry.pruned", "fields": {"removed": removed}})


def get_active_socks_port() -> Optional[int]:
    return _active_socks_port


def get_active_http_port() -> Optional[int]:
    return _active_http_port


def get_injector_target() -> tuple[Optional[str], Optional[int]]:
    """Deprecated for multi‑profile; kept for compatibility."""
    return None, None


def get_injector_diagnostics() -> dict:
    global _injector, _injector_thread, _proxy_thread, _running, _registry
    injector_alive = _injector_thread and _injector_thread.is_alive()
    proxy_alive = _proxy_thread and _proxy_thread.is_alive()
    registry_stats = _registry.get_stats() if _registry else {}
    injector_stats = _injector.get_stats() if _injector else {}
    return {
        "running": _running,
        "injectorThreadAlive": injector_alive,
        "proxyThreadAlive": proxy_alive,
        "proxyMode": "local-proxy" if _socks_server is not None or _http_server is not None else "hook-only",
        "activeSocksPort": _active_socks_port,
        "activeHttpPort": _active_http_port,
        "activeMonitoredConnections": registry_stats.get("active_connections", 0),
        "injectorStats": injector_stats,
        "connectTimeoutMs": _connect_timeout_ms,
        "injectTimeoutMs": _inject_timeout_ms,
        "relayIdleTimeoutMs": _relay_idle_timeout_ms,
        "staleConnectionMs": _stale_connection_ms,
        "cleanupIntervalMs": _cleanup_interval_ms,
    }


async def handle_bridge_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Accept one local bridge connection and relay it through the injected upstream socket."""
    loop = asyncio.get_running_loop()
    interface_ipv4 = _resolve_local_device_ip() or "0.0.0.0"
    peer_sock = writer.get_extra_info("socket")
    success, message, outgoing_sock = await establish_connection(
        loop,
        interface_ipv4,
        peer_sock,
    )
    if not success or outgoing_sock is None:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        raise RuntimeError(message)

    remote_reader, remote_writer = await asyncio.open_connection(sock=outgoing_sock)
    await asyncio.gather(
        _relay_with_count(reader, remote_writer, is_upload=True, idle_timeout=_relay_idle_timeout_ms),
        _relay_with_count(remote_reader, writer, is_upload=False, idle_timeout=_relay_idle_timeout_ms),
        return_exceptions=True,
    )


async def _relay_with_count(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        is_upload: bool,
        idle_timeout: int,
) -> None:
    timeout_s = idle_timeout / 1000.0
    try:
        while True:
            data = await asyncio.wait_for(reader.read(65535), timeout=timeout_s)
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


async def establish_connection(
        loop: asyncio.AbstractEventLoop,
        interface_ipv4: str,
        peer_sock: socket.socket,
) -> tuple[bool, str, Optional[socket.socket]]:
    """
    Perform TCP handshake and fake TLS injection using current active profile.
    Returns (success, message, outgoing_socket).
    """
    global _profile_store, _registry, _connect_timeout_ms, _inject_timeout_ms

    if _profile_store is None or _registry is None:
        return False, "injector not started", None

    try:
        connect_ip, connect_port, fake_sni_bytes = await _profile_store.get_active_profile()
    except RuntimeError as e:
        return False, str(e), None

    # Build fake TLS ClientHello
    fake_data = ClientHelloMaker.get_client_hello_with(
        os.urandom(32), os.urandom(32), fake_sni_bytes, os.urandom(32)
    )

    # Create outgoing socket
    outgoing = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    outgoing.setblocking(False)
    outgoing.bind((interface_ipv4, 0))
    src_port = outgoing.getsockname()[1]

    # Create monitor connection
    import uuid
    conn = MonitorConnection(
        connection_id=uuid.uuid4().hex,
        profile_id="active",  # not used for much
        sock=outgoing,
        peer_sock=peer_sock,
        src_ip=interface_ipv4,
        dst_ip=connect_ip,
        src_port=src_port,
        dst_port=connect_port,
        fake_data=fake_data,
        bypass_method="wrong_seq",
    )
    _registry.add(conn)

    try:
        # Connect with timeout
        await asyncio.wait_for(
            loop.sock_connect(outgoing, (connect_ip, connect_port)),
            timeout=_connect_timeout_ms / 1000.0,
        )
        # Wait for fake ACK with inject timeout
        await asyncio.wait_for(
            conn.t2a_event.wait(),
            timeout=_inject_timeout_ms / 1000.0,
        )
        if conn.t2a_msg != "fake_data_ack_recv":
            return False, f"unexpected injector state: {conn.t2a_msg}", None
    except asyncio.TimeoutError:
        _registry.remove(conn.id)
        outgoing.close()
        return False, f"timeout ({_connect_timeout_ms if conn.syn_seq == -1 else _inject_timeout_ms} ms)", None
    except Exception as e:
        _registry.remove(conn.id)
        outgoing.close()
        return False, f"connection error: {e}", None

    # Injection succeeded – stop monitoring
    conn.monitor = False
    _registry.remove(conn.id)
    return True, "injection ready", outgoing
