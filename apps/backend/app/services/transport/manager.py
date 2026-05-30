import asyncio
import os
import requests
import socket
import socks
import threading
import time
import contextlib
import concurrent.futures
from typing import Optional

from .packet_templates import ClientHelloMaker
from .socks5 import Socks5Server
from .tcp_injector import TcpInjector, FakeInjectiveConnection

_injector_thread: Optional[threading.Thread] = None
_socks_thread: Optional[threading.Thread] = None
_connections: dict = {}
_running = False
_socks_server: Optional[Socks5Server] = None
_socks_loop: Optional[asyncio.AbstractEventLoop] = None
_active_socks_port: Optional[int] = None


def start_injector(interface_ipv4: str, connect_ip: str, connect_port: int, fake_sni: bytes, socks_port: int):
    """Launch the WinDivert injector in a background thread and start SOCKS5 proxy."""
    global _injector_thread, _socks_thread, _connections, _running, _socks_server, _socks_loop, _active_socks_port

    if _running:
        stop_injector()

    _connections.clear()
    w_filter = (
        f"tcp and ((ip.SrcAddr == {interface_ipv4} and ip.DstAddr == {connect_ip}) or "
        f"(ip.SrcAddr == {connect_ip} and ip.DstAddr == {interface_ipv4}))"
    )
    injector = TcpInjector(w_filter, _connections)
    _running = True
    _active_socks_port = socks_port

    # Start WinDivert capture thread
    def _run():
        with contextlib.suppress(Exception):
            injector.run()

    _injector_thread = threading.Thread(target=_run, daemon=True)
    _injector_thread.start()

    # Start SOCKS5 proxy in its own asyncio loop
    _socks_loop = asyncio.new_event_loop()
    _socks_server = Socks5Server("127.0.0.1", socks_port, interface_ipv4, connect_ip, connect_port, fake_sni)

    def _run_proxy():
        loop = _socks_loop
        server = _socks_server
        if loop is None or server is None:
            return
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.start())
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    _socks_thread = threading.Thread(target=_run_proxy, daemon=True)
    _socks_thread.start()
    return True


def stop_injector():
    """Stop WinDivert injector and SOCKS5 proxy."""
    global _running, _injector_thread, _socks_thread, _socks_server, _socks_loop, _active_socks_port
    _running = False

    if _socks_server and _socks_loop and not _socks_loop.is_closed():
        async def _stop():
            await _socks_server.stop()

        try:
            future = asyncio.run_coroutine_threadsafe(_stop(), _socks_loop)
            future.result(timeout=2)
        except (RuntimeError, concurrent.futures.TimeoutError):
            pass
        finally:
            with contextlib.suppress(RuntimeError):
                _socks_loop.call_soon_threadsafe(_socks_loop.stop)

    if _socks_thread and _socks_thread.is_alive():
        _socks_thread.join(timeout=2)

    _injector_thread = None
    _socks_thread = None
    _socks_server = None
    _socks_loop = None
    _connections.clear()
    _active_socks_port = None


def get_active_socks_port() -> Optional[int]:
    """Return the SOCKS port currently used by the injector, if any."""
    return _active_socks_port


def test_delay_via_socks5(timeout_s: float = 3.0) -> float:
    """
    Measure latency (ms) to www.gstatic.com:443 through the active SOCKS5 proxy.
    """
    port = get_active_socks_port()
    if not port:
        raise RuntimeError("No active SOCKS5 proxy")
    proxies = {
        'http': f'socks5://127.0.0.1:{port}',
        'https': f'socks5://127.0.0.1:{port}'
    }
    start = time.perf_counter()
    try:
        # A simple GET request (small) to a reliable server
        requests.get("https://www.gstatic.com/generate_204", proxies=proxies, timeout=timeout_s)
        return (time.perf_counter() - start) * 1000.0
    except Exception as e:
        raise TimeoutError(f"SOCKS5 delay test failed: {e}")


def test_speed_via_socks5(timeout_s: float = 5.0) -> float:
    """
    Download a 1 MB test file via SOCKS5 proxy and return throughput (bytes/sec).
    """
    port = get_active_socks_port()
    if not port:
        raise RuntimeError("No active SOCKS5 proxy")
    proxies = {
        'http': f'socks5://127.0.0.1:{port}',
        'https': f'socks5://127.0.0.1:{port}'
    }
    url = "https://cachefly.cachefly.net/1mb.test"
    start = time.perf_counter()
    total_bytes = 0
    try:
        with requests.get(url, stream=True, proxies=proxies, timeout=timeout_s) as r:
            for chunk in r.iter_content(65535):
                if chunk:
                    total_bytes += len(chunk)
                    if time.perf_counter() - start >= timeout_s:
                        break
        elapsed = max(time.perf_counter() - start, 0.001)
        return total_bytes / elapsed
    except Exception as e:
        raise TimeoutError(f"SOCKS5 speed test failed: {e}")


async def establish_connection(
        loop: asyncio.AbstractEventLoop,
        interface_ipv4: str,
        connect_ip: str,
        connect_port: int,
        fake_sni: bytes,
        peer_sock: socket.socket
) -> tuple[bool, str, Optional[socket.socket]]:
    """
    Perform the TCP handshake and fake TLS injection.
    Returns (success, message, outgoing_socket) or (False, error, None).
    """
    outgoing = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    outgoing.setblocking(False)
    outgoing.bind((interface_ipv4, 0))
    src_port = outgoing.getsockname()[1]

    fake_data = ClientHelloMaker.get_client_hello_with(
        os.urandom(32), os.urandom(32), fake_sni, os.urandom(32)
    )
    conn = FakeInjectiveConnection(
        outgoing, interface_ipv4, connect_ip, src_port, connect_port,
        fake_data, "wrong_seq", peer_sock
    )
    _connections[conn.id] = conn

    try:
        await loop.sock_connect(outgoing, (connect_ip, connect_port))
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
