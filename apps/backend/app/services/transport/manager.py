import asyncio
import os
import socket
import threading
from typing import Optional

from .packet_templates import ClientHelloMaker
from .tcp_injector import TcpInjector, FakeInjectiveConnection

_injector_thread: Optional[threading.Thread] = None
_connections: dict = {}
_running = False


def start_injector(interface_ipv4: str, connect_ip: str, connect_port: int, fake_sni: bytes):
    """Launch the WinDivert injector in a background thread."""
    global _injector_thread, _connections, _running
    if _running:
        stop_injector()
    _connections.clear()
    w_filter = (
        f"tcp and ((ip.SrcAddr == {interface_ipv4} and ip.DstAddr == {connect_ip}) or "
        f"(ip.SrcAddr == {connect_ip} and ip.DstAddr == {interface_ipv4}))"
    )
    injector = TcpInjector(w_filter, _connections)
    _running = True

    def _run():
        injector.run()

    _injector_thread = threading.Thread(target=_run, daemon=True)
    _injector_thread.start()
    return True


def stop_injector():
    global _running, _injector_thread
    _running = False
    # WinDivert context will be released when thread exits (no explicit stop)
    _injector_thread = None
    _connections.clear()


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
