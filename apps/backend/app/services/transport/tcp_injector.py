from __future__ import annotations

import contextlib
import logging
import time
from pydivert import WinDivert, Packet

from .connection_registry import ConnectionRegistry
from .monitor_connection import MonitorConnection


class TcpInjector:
    """
    WinDivert packet interceptor that uses a broad filter ("tcp and ip")
    and delegates per‑connection state to a ConnectionRegistry.
    """

    def __init__(self, registry: ConnectionRegistry, logger: logging.Logger):
        self.registry = registry
        self.logger = logger
        # Broad filter: all TCP/IPv4 traffic. Registry matches exact flows.
        self.w = WinDivert("tcp and ip")
        self.running = True
        self.stats = {
            "packets_seen": 0,
            "packets_matched": 0,
            "outbound_syn": 0,
            "inbound_syn_ack": 0,
            "handshake_ack": 0,
            "fake_sent": 0,
            "fake_ack": 0,
            "unexpected_close": 0,
            "unmatched_pass": 0,
        }

    def stop(self) -> None:
        self.running = False
        with contextlib.suppress(Exception):
            self.w.close()

    def _log_event(self, level: int, event_name: str, **fields) -> None:
        self.logger.log(level, event_name, extra={"event_name": event_name, "fields": fields})

    def _close_connection(self, conn: MonitorConnection) -> None:
        try:
            conn.sock.close()
        except Exception:
            pass
        try:
            if conn.peer_sock:
                conn.peer_sock.close()
        except Exception:
            pass

    def _on_unexpected(self, packet: Packet, conn: MonitorConnection, reason: str) -> None:
        self.stats["unexpected_close"] += 1
        self._log_event(
            logging.WARNING,
            "injector.unexpected_packet",
            connection_id=conn.connection_id,
            profile_id=conn.profile_id,
            reason=reason,
            packet_direction="inbound" if packet.is_inbound else "outbound",
            src_addr=packet.src_addr,
            src_port=packet.tcp.src_port,
            dst_addr=packet.dst_addr,
            dst_port=packet.tcp.dst_port,
            seq=packet.tcp.seq_num,
            ack=packet.tcp.ack_num,
            payload_len=len(packet.tcp.payload),
        )
        self._close_connection(conn)
        conn.monitor = False
        conn.t2a_msg = "unexpected_close"
        conn.running_loop.call_soon_threadsafe(conn.t2a_event.set)
        self.w.send(packet, False)

    def _schedule_fake_send(self, packet: Packet, conn: MonitorConnection) -> None:
        # Small delay to let the original ACK be forwarded first
        conn.running_loop.call_later(0.001, self._execute_fake_send, packet, conn)

    def _execute_fake_send(self, packet: Packet, conn: MonitorConnection) -> None:
        with conn.thread_lock:
            if not conn.monitor:
                return

            if conn.bypass_method == "wrong_seq":
                conn.fake_seq_num = (conn.syn_seq + 1 - len(conn.fake_data)) & 0xFFFFFFFF
                conn.fake_ack_num = (conn.syn_ack_seq + 1) & 0xFFFFFFFF
                conn.fake_sent = True

                # Clone packet and modify for injection
                p = packet
                p.tcp.psh = True
                p.ip.packet_len = p.ip.packet_len + len(conn.fake_data)
                p.tcp.payload = conn.fake_data
                if hasattr(p, "ipv4") and p.ipv4:
                    p.ipv4.ident = (p.ipv4.ident + 1) & 0xFFFF
                p.tcp.seq_num = conn.fake_seq_num
                p.tcp.ack_num = conn.fake_ack_num

                self.w.send(p, True)
                self.stats["fake_sent"] += 1
                self._log_event(
                    logging.INFO,
                    "injector.fake_send",
                    connection_id=conn.connection_id,
                    profile_id=conn.profile_id,
                    src_ip=conn.src_ip,
                    src_port=conn.src_port,
                    dst_ip=conn.dst_ip,
                    dst_port=conn.dst_port,
                    fake_payload_len=len(conn.fake_data),
                    fake_seq=conn.fake_seq_num,
                    fake_ack=conn.fake_ack_num,
                )
            else:
                self._log_event(logging.ERROR, "injector.unsupported_method", method=conn.bypass_method)
                self._close_connection(conn)
                conn.monitor = False
                conn.t2a_msg = "unsupported_bypass"
                conn.running_loop.call_soon_threadsafe(conn.t2a_event.set)

    def _on_inbound(self, packet: Packet, conn: MonitorConnection) -> None:
        conn.last_seen_at = time.monotonic()

        if conn.syn_seq == -1:
            return self._on_unexpected(packet, conn, "no SYN sent")

        # SYN‑ACK
        if packet.tcp.ack and packet.tcp.syn and not packet.tcp.rst and not packet.tcp.fin and len(
                packet.tcp.payload) == 0:
            if conn.syn_ack_seq != -1 and conn.syn_ack_seq != packet.tcp.seq_num:
                return self._on_unexpected(packet, conn, "SYN‑ACK seq mismatch")
            if packet.tcp.ack_num != ((conn.syn_seq + 1) & 0xFFFFFFFF):
                return self._on_unexpected(packet, conn, "SYN‑ACK ack mismatch")
            conn.syn_ack_seq = packet.tcp.seq_num
            self.stats["inbound_syn_ack"] += 1
            self.w.send(packet, False)
            return

        # ACK after fake injection
        if packet.tcp.ack and not packet.tcp.syn and not packet.tcp.rst and not packet.tcp.fin and conn.fake_sent:
            expected_seq = (conn.syn_ack_seq + 1) & 0xFFFFFFFF
            expected_ack = (conn.syn_seq + 1) & 0xFFFFFFFF
            if packet.tcp.seq_num != expected_seq or packet.tcp.ack_num != expected_ack:
                return self._on_unexpected(packet, conn, "post‑inject ack mismatch")
            conn.monitor = False
            conn.t2a_msg = "fake_data_ack_recv"
            conn.running_loop.call_soon_threadsafe(conn.t2a_event.set)
            self.stats["fake_ack"] += 1
            self._log_event(
                logging.INFO,
                "injector.ack_received",
                connection_id=conn.connection_id,
                profile_id=conn.profile_id,
            )
            # Do NOT pass this packet – it is the ACK for our fake data;
            # the real socket relay will handle further data.
            return

        self._on_unexpected(packet, conn, "unexpected inbound packet")

    def _on_outbound(self, packet: Packet, conn: MonitorConnection) -> None:
        conn.last_seen_at = time.monotonic()

        if conn.sch_fake_sent:
            # Already scheduled injection; ignore further outbound packets
            self.w.send(packet, False)
            return

        # SYN
        if packet.tcp.syn and not packet.tcp.ack and not packet.tcp.rst and not packet.tcp.fin and len(
                packet.tcp.payload) == 0:
            if packet.tcp.ack_num != 0:
                return self._on_unexpected(packet, conn, "non‑zero ack in SYN")
            if conn.syn_seq != -1 and conn.syn_seq != packet.tcp.seq_num:
                return self._on_unexpected(packet, conn, "SYN seq mismatch")
            conn.syn_seq = packet.tcp.seq_num
            self.stats["outbound_syn"] += 1
            self.w.send(packet, False)
            return

        # Pure ACK completing handshake
        if packet.tcp.ack and not packet.tcp.syn and not packet.tcp.rst and not packet.tcp.fin and len(
                packet.tcp.payload) == 0:
            expected_seq = (conn.syn_seq + 1) & 0xFFFFFFFF
            expected_ack = (conn.syn_ack_seq + 1) & 0xFFFFFFFF
            if packet.tcp.seq_num != expected_seq:
                return self._on_unexpected(packet, conn, "handshake ACK seq mismatch")
            if packet.tcp.ack_num != expected_ack:
                return self._on_unexpected(packet, conn, "handshake ACK ack mismatch")
            self.w.send(packet, False)
            conn.sch_fake_sent = True
            self.stats["handshake_ack"] += 1
            self._schedule_fake_send(packet, conn)
            return

        self._on_unexpected(packet, conn, "unexpected outbound packet")

    def inject(self, packet: Packet) -> None:
        self.stats["packets_seen"] += 1

        # Build connection 4‑tuple
        if packet.is_inbound:
            cid = (packet.dst_addr, packet.tcp.dst_port, packet.src_addr, packet.tcp.src_port)
        else:
            cid = (packet.src_addr, packet.tcp.src_port, packet.dst_addr, packet.tcp.dst_port)

        conn = self.registry.get(cid)
        if conn is None:
            self.stats["unmatched_pass"] += 1
            self.w.send(packet, False)
            return

        self.stats["packets_matched"] += 1
        with conn.thread_lock:
            if not conn.monitor:
                self.w.send(packet, False)
                return
            if packet.is_inbound:
                self._on_inbound(packet, conn)
            else:
                self._on_outbound(packet, conn)

        # If connection is done, remove it from registry
        if not conn.monitor:
            self.registry.remove(conn.id)

    def run(self) -> None:
        with self.w:
            while self.running:
                with contextlib.suppress(Exception):
                    pkt = self.w.recv()
                    self.inject(pkt)

    def get_stats(self) -> dict:
        return dict(self.stats)
