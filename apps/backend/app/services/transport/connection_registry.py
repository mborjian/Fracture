from __future__ import annotations

import threading
import time
from typing import Optional

from .monitor_connection import MonitorConnection


class ConnectionRegistry:
    """Thread‑safe store of active monitored connections keyed by 4‑tuple (src_ip, src_port, dst_ip, dst_port)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._connections: dict[tuple, MonitorConnection] = {}

    def add(self, connection: MonitorConnection) -> None:
        with self._lock:
            self._connections[connection.id] = connection

    def remove(self, connection_id: tuple) -> None:
        with self._lock:
            self._connections.pop(connection_id, None)

    def get(self, connection_id: tuple) -> Optional[MonitorConnection]:
        with self._lock:
            return self._connections.get(connection_id)

    def prune(self, stale_after_seconds: float) -> int:
        """Remove connections that have been inactive or are no longer monitored."""
        now = time.monotonic()
        removed: list[tuple] = []

        with self._lock:
            for key, conn in list(self._connections.items()):
                age = now - conn.last_seen_at
                if (not conn.monitor) or age > stale_after_seconds:
                    removed.append(key)
                    self._connections.pop(key, None)

        return len(removed)

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "active_connections": len(self._connections),
                "monitored_connections": sum(1 for c in self._connections.values() if c.monitor),
            }
