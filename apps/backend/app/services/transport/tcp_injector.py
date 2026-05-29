import sys
import threading
import time
from pydivert import WinDivert, Packet

from .monitor_connection import MonitorConnection


class FakeInjectiveConnection(MonitorConnection):
    def __init__(self, sock, src_ip, dst_ip, src_port, dst_port, fake_data: bytes, bypass_method: str, peer_sock):
        super().__init__(sock, src_ip, dst_ip, src_port, dst_port)
        self.fake_data = fake_data
        self.sch_fake_sent = False
        self.fake_sent = False
        self.t2a_event = threading.Event()
        self.t2a_msg = ""
        self.bypass_method = bypass_method
        self.peer_sock = peer_sock


class TcpInjector:
    def __init__(self, w_filter: str, connections: dict):
        self.w = WinDivert(w_filter)
        self.connections = connections

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
                self.w.send(packet, True)

    def _on_unexpected(self, packet: Packet, conn: FakeInjectiveConnection, info: str):
        print(info, packet)
        conn.sock.close()
        conn.peer_sock.close()
        conn.monitor = False
        conn.t2a_msg = "unexpected_close"
        conn.t2a_event.set()
        self.w.send(packet, False)

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
            self.w.send(packet, False)
            return
        # ACK after fake injection
        if packet.tcp.ack and not packet.tcp.syn and not packet.tcp.rst and not packet.tcp.fin and len(
                packet.tcp.payload) == 0 and conn.fake_sent:
            if ((conn.syn_ack_seq + 1) & 0xffffffff) != packet.tcp.seq_num:
                return self._on_unexpected(packet, conn, "post‑inject seq mismatch")
            if packet.tcp.ack_num != ((conn.syn_seq + 1) & 0xffffffff):
                return self._on_unexpected(packet, conn, "post‑inject ack mismatch")
            conn.monitor = False
            conn.t2a_msg = "fake_data_ack_recv"
            conn.t2a_event.set()
            return
        self._on_unexpected(packet, conn, "unexpected inbound")

    def _on_outbound(self, packet: Packet, conn: FakeInjectiveConnection):
        if conn.sch_fake_sent:
            return self._on_unexpected(packet, conn, "already injected")
        # SYN
        if packet.tcp.syn and not packet.tcp.ack and not packet.tcp.rst and not packet.tcp.fin and len(
                packet.tcp.payload) == 0:
            if packet.tcp.ack_num != 0:
                return self._on_unexpected(packet, conn, "non‑zero ack in SYN")
            if conn.syn_seq != -1 and conn.syn_seq != packet.tcp.seq_num:
                return self._on_unexpected(packet, conn, "SYN seq mismatch")
            conn.syn_seq = packet.tcp.seq_num
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
            threading.Thread(target=self._fake_send_thread, args=(packet, conn), daemon=True).start()
            return
        self._on_unexpected(packet, conn, "unexpected outbound")

    def inject(self, packet: Packet):
        if packet.is_inbound:
            cid = (packet.ip.dst_addr, packet.tcp.dst_port, packet.ip.src_addr, packet.tcp.src_port)
        else:
            cid = (packet.ip.src_addr, packet.tcp.src_port, packet.ip.dst_addr, packet.tcp.dst_port)

        conn = self.connections.get(cid)
        if not conn:
            self.w.send(packet, False)
            return
        with conn.thread_lock:
            if not conn.monitor:
                self.w.send(packet, False)
                return
            if packet.is_inbound:
                self._on_inbound(packet, conn)
            else:
                self._on_outbound(packet, conn)

    def run(self):
        with self.w:
            while True:
                pkt = self.w.recv()
                self.inject(pkt)
