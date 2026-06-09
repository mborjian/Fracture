from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Optional


class MonitorConnection:
    """
    Tracks TCP handshake state and injection progress for a single connection.
    """

    def __init__(
            self,
            connection_id: str,
            profile_id: str,
            sock: socket.socket,
            peer_sock: socket.socket,
            src_ip: str,
            dst_ip: str,
            src_port: int,
            dst_port: int,
            fake_data: bytes,
            bypass_method: str,
    ):
        self.connection_id = connection_id
        self.profile_id = profile_id
        self.sock = sock  # outbound socket to real target
        self.peer_sock = peer_sock  # inbound socket from local client (may be None for hook-only)
        self.src_ip = src_ip
        self.dst_ip = dst_ip
        self.src_port = src_port
        self.dst_port = dst_port
        self.id = (self.src_ip, self.src_port, self.dst_ip, self.dst_port)
        self.fake_data = fake_data
        self.bypass_method = bypass_method

        # State machine
        self.monitor = True
        self.syn_seq = -1  # outbound SYN seq
        self.syn_ack_seq = -1  # inbound SYN‑ACK seq
        self.fake_seq_num = -1
        self.fake_ack_num = -1
        self.fake_sent = False
        self.sch_fake_sent = False

        # Completion event
        self.t2a_event = asyncio.Event()
        self.t2a_msg = ""

        # Thread safety and timestamps
        self.thread_lock = threading.Lock()
        self.created_at = time.monotonic()
        self.last_seen_at = self.created_at
        self.running_loop = asyncio.get_running_loop()
