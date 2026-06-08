from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import WebSocket

SEND_TIMEOUT_SECONDS = 1.0


class WsEventHub:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._recent_logs: deque[dict] = deque(maxlen=1500)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def recent_logs(self) -> list[dict]:
        async with self._lock:
            return [dict(item) for item in self._recent_logs]

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def publish(self, event_type: str, payload: dict) -> None:
        async with self._lock:
            if event_type == "log" and isinstance(payload, dict):
                self._recent_logs.append(dict(payload))
            sockets = list(self._connections)
        message = json.dumps({"type": event_type, "payload": payload}, ensure_ascii=True)

        async def send(socket: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(socket.send_text(message), timeout=SEND_TIMEOUT_SECONDS)
            except Exception:  # noqa: BLE001
                return socket
            return None

        stale = [socket for socket in await asyncio.gather(*(send(socket) for socket in sockets)) if socket is not None]

        if stale:
            async with self._lock:
                for socket in stale:
                    self._connections.discard(socket)

    async def publish_log(self, level: str, message: str, *, source: str = "backend", trace: str | None = None) -> None:
        payload = {
            "id": str(uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
            "source": source,
        }
        if trace:
            payload["trace"] = trace
        await self.publish(
            "log",
            payload,
        )
