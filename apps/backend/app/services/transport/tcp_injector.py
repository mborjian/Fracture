import contextlib
import os
import threading
import time

try:
    from pydivert import WinDivert, Packet
except Exception:  # noqa: BLE001
    WinDivert = None  # type: ignore[assignment]
    Packet = object  # type: ignore[assignment,misc]

from .monitor_connection import MonitorConnection
from .packet_templates import ClientHelloMaker


class FakeInjectiveConnection(MonitorConnection):
    def __init__(
        self,
        sock,
        src_ip,
        dst_ip,
        src_port,
        dst_port,
        fake_data: bytes,
        bypass_method: str,
        peer_sock,
        *,
        auto_managed: bool = False,
    ):
        super().__init__(sock, src_ip, dst_ip, src_port, dst_port)
        self.fake_data = fake_data
        self.sch_fake_sent = False
        self.fake_sent = False
        self.t2a_event = threading.Event()
        self.t2a_msg = ""
        self.bypass_method = bypass_method
        self.peer_sock = peer_sock
        self.auto_managed = auto_managed
        self.created_at = time.monotonic()


class TcpInjector:
    def __init__(
        self,
        w_filter: str,
        connections: dict,
        *,
        fake_sni: bytes = b"",
        auto_monitor: bool = False,
        match_port: int = 443,
        max_auto_connections: int = 1024,
    ):
        if WinDivert is None:
            raise RuntimeError("pydivert/WinDivert is not available; install the driver and run as Administrator")
        self.w = WinDivert(w_filter)
        self.connections = connections
        self.fake_sni = fake_sni
        self.auto_monitor = auto_monitor
        self.match_port = int(match_port)
        self.max_auto_connections = int(max_auto_connections)
        self.running = True
        self._last_cleanup_at = time.monotonic()
        self.stats = {
            "packetsSeen": 0,
            "packetsMatched": 0,
            "outboundSynSeen": 0,
            "inboundSynAckSeen": 0,
            "handshakeAckSeen": 0,
            "fakeSent": 0,
            "fakeAckSeen": 0,
            "unexpectedClose": 0,
            "unmatchedPassThrough": 0,
            "autoConnectionsCreated": 0,
            "autoConnectionsExpired": 0,
            "autoConnectionsDropped": 0,
            "autoUnexpectedPassThrough": 0,
        }

    def stop(self):
        self.running = False
        with contextlib.suppress(Exception):
            self.w.close()

    def _fake_send_thread(self, packet: Packet, conn: FakeInjectiveConnection):
        time.sleep(0.001)
        with conn.thread_lock:
            if not conn.monitor:
                return
            packet.tcp.psh = True
            packet.ip.packet_len = packet.ip.packet_len + len(conn.fake_data)
            packet.tcp.payload = conn.fake_data
            if conn.bypass_method == "wrong_seq":
                packet.tcp.seq_num = (conn.syn_seq + 1 - len(packet.tcp.payload)) & 0xffffffff
                conn.fake_sent = True
                self.stats["fakeSent"] += 1
                self.w.send(packet, True)

    def _on_unexpected(self, packet: Packet, conn: FakeInjectiveConnection, info: str):
        print(info, packet)
        if getattr(conn, "auto_managed", False):
            # In hook-only mode we must never tear down real app sockets.
            # Stop monitoring this flow and pass traffic through untouched.
            conn.monitor = False
            conn.t2a_msg = "auto_unexpected_passthrough"
            conn.t2a_event.set()
            self.stats["autoUnexpectedPassThrough"] += 1
            self.w.send(packet, False)
            return

        self.stats["unexpectedClose"] += 1
        if conn.sock is not None:
            conn.sock.close()
        if conn.peer_sock is not None:
            conn.peer_sock.close()
        conn.monitor = False
        conn.t2a_msg = "unexpected_close"
        conn.t2a_event.set()
        self.w.send(packet, False)

    def _cleanup_connections(self):
        now = time.monotonic()
        if now - self._last_cleanup_at < 0.5:
            return
        self._last_cleanup_at = now
        stale_ids = []
        for cid, conn in self.connections.items():
            if not conn.monitor:
                stale_ids.append(cid)
                continue
            if getattr(conn, "auto_managed", False) and now - getattr(conn, "created_at", now) > 8.0:
                stale_ids.append(cid)
                self.stats["autoConnectionsExpired"] += 1
        for cid in stale_ids:
            self.connections.pop(cid, None)

    @staticmethod
    def _is_outbound_syn(packet: Packet) -> bool:
        return (
            packet.tcp.syn
            and not packet.tcp.ack
            and not packet.tcp.rst
            and not packet.tcp.fin
            and len(packet.tcp.payload) == 0
        )

    def _try_register_auto_connection(self, packet: Packet):
        if not self.auto_monitor or packet.is_inbound:
            return None
        if packet.tcp.dst_port != self.match_port:
            return None
        if not self._is_outbound_syn(packet):
            return None
        cid = (packet.ip.src_addr, packet.tcp.src_port, packet.ip.dst_addr, packet.tcp.dst_port)
        if cid in self.connections:
            return self.connections[cid]
        if len(self.connections) >= self.max_auto_connections:
            self.stats["autoConnectionsDropped"] += 1
            return None
        fake_data = ClientHelloMaker.get_client_hello_with(
            os.urandom(32), os.urandom(32), self.fake_sni, os.urandom(32)
        )
        conn = FakeInjectiveConnection(
            None,
            packet.ip.src_addr,
            packet.ip.dst_addr,
            packet.tcp.src_port,
            packet.tcp.dst_port,
            fake_data,
            "wrong_seq",
            None,
            auto_managed=True,
        )
        self.connections[cid] = conn
        self.stats["autoConnectionsCreated"] += 1
        return conn

    def _on_inbound(self, packet: Packet, conn: FakeInjectiveConnection):
        if conn.syn_seq == -1:
            return self._on_unexpected(packet, conn, "no SYN sent")
        # SYN‑ACK
        if packet.tcp.ack and packet.tcp.syn and not packet.tcp.rst and not packet.tcp.fin and len(
                packet.tcp.payload) == 0:
            if conn.syn_ack_seq != -1 and conn.syn_ack_seq != packet.tcp.seq_num:
                return self._on_unexpected(packet, conn, "SYN‑ACK seq mismatch")
            if packet.tcp.ack_num != ((conn.syn_seq + 1) & 0xffffffff):
                return self._on_unexpected(packet, conn, "SYN‑ACK ack mismatch")
            conn.syn_ack_seq = packet.tcp.seq_num
            self.stats["inboundSynAckSeen"] += 1
            self.w.send(packet, False)
            return
        # ACK after fake injection
        if packet.tcp.ack and not packet.tcp.syn and not packet.tcp.rst and not packet.tcp.fin and conn.fake_sent:
            if getattr(conn, "auto_managed", False):
                if packet.tcp.ack_num == ((conn.syn_seq + 1) & 0xffffffff):
                    conn.monitor = False
                    conn.t2a_msg = "fake_data_ack_recv"
                    conn.t2a_event.set()
                    self.stats["fakeAckSeen"] += 1
                    self.w.send(packet, False)
                    return
                # Keep passing auto-managed traffic even when ACK shape differs.
                return self._on_unexpected(packet, conn, "post-inject inbound passthrough")
            if ((conn.syn_ack_seq + 1) & 0xffffffff) != packet.tcp.seq_num:
                return self._on_unexpected(packet, conn, "post‑inject seq mismatch")
            if packet.tcp.ack_num != ((conn.syn_seq + 1) & 0xffffffff):
                return self._on_unexpected(packet, conn, "post‑inject ack mismatch")
            conn.monitor = False
            conn.t2a_msg = "fake_data_ack_recv"
            conn.t2a_event.set()
            self.stats["fakeAckSeen"] += 1
            return
        self._on_unexpected(packet, conn, "unexpected inbound")

    def _on_outbound(self, packet: Packet, conn: FakeInjectiveConnection):
        if conn.sch_fake_sent:
            if getattr(conn, "auto_managed", False):
                self.w.send(packet, False)
                return
            return self._on_unexpected(packet, conn, "already injected")
        # SYN
        if packet.tcp.syn and not packet.tcp.ack and not packet.tcp.rst and not packet.tcp.fin and len(
                packet.tcp.payload) == 0:
            if packet.tcp.ack_num != 0:
                return self._on_unexpected(packet, conn, "non‑zero ack in SYN")
            if conn.syn_seq != -1 and conn.syn_seq != packet.tcp.seq_num:
                return self._on_unexpected(packet, conn, "SYN seq mismatch")
            conn.syn_seq = packet.tcp.seq_num
            self.stats["outboundSynSeen"] += 1
            self.w.send(packet, False)
            return
        # ACK (completes handshake)
        if packet.tcp.ack and not packet.tcp.syn and not packet.tcp.rst and not packet.tcp.fin and len(
                packet.tcp.payload) == 0:
            if ((conn.syn_seq + 1) & 0xffffffff) != packet.tcp.seq_num:
                return self._on_unexpected(packet, conn, "handshake ACK seq mismatch")
            if ((conn.syn_ack_seq + 1) & 0xffffffff) != packet.tcp.ack_num:
                return self._on_unexpected(packet, conn, "handshake ACK ack mismatch")
            self.w.send(packet, False)
            conn.sch_fake_sent = True
            self.stats["handshakeAckSeen"] += 1
            threading.Thread(target=self._fake_send_thread, args=(packet, conn), daemon=True).start()
            return
        self._on_unexpected(packet, conn, "unexpected outbound")

    def inject(self, packet: Packet):
        self.stats["packetsSeen"] += 1
        self._cleanup_connections()
        if packet.is_inbound:
            cid = (packet.ip.dst_addr, packet.tcp.dst_port, packet.ip.src_addr, packet.tcp.src_port)
        else:
            cid = (packet.ip.src_addr, packet.tcp.src_port, packet.ip.dst_addr, packet.tcp.dst_port)

        conn = self.connections.get(cid)
        if conn is None:
            conn = self._try_register_auto_connection(packet)
        if not conn:
            self.stats["unmatchedPassThrough"] += 1
            self.w.send(packet, False)
            return
        self.stats["packetsMatched"] += 1
        with conn.thread_lock:
            if not conn.monitor:
                self.w.send(packet, False)
                return
            if packet.is_inbound:
                self._on_inbound(packet, conn)
            else:
                self._on_outbound(packet, conn)
        if not conn.monitor:
            self.connections.pop(cid, None)

    def run(self):
        with self.w:
            while self.running:
                with contextlib.suppress(Exception):
                    pkt = self.w.recv()
                    self.inject(pkt)
                    continue
                break

    def get_stats(self) -> dict:
        return dict(self.stats)
