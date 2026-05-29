import socket
import threading


class MonitorConnection:
    """Tracks TCP handshake state for a single connection."""

    def __init__(self, sock: socket.socket, src_ip: str, dst_ip: str,
                 src_port: int, dst_port: int):
        self.monitor = True
        self.syn_seq = -1  # outbound SYN seq
        self.syn_ack_seq = -1  # inbound SYN‑ACK seq
        self.src_ip = src_ip
        self.dst_ip = dst_ip
        self.src_port = src_port
        self.dst_port = dst_port
        self.id = (self.src_ip, self.src_port, self.dst_ip, self.dst_port)
        self.thread_lock = threading.Lock()
        self.sock = sock
