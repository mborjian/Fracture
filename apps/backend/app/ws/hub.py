from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import WebSocket


class WsEventHub:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def publish(self, event_type: str, payload: dict) -> None:
        message = json.dumps({"type": event_type, "payload": payload}, ensure_ascii=True)
        async with self._lock:
            sockets = list(self._connections)

        stale: list[WebSocket] = []
        for socket in sockets:
            try:
                await socket.send_text(message)
            except Exception:  # noqa: BLE001
                stale.append(socket)

        if stale:
            async with self._lock:
                for socket in stale:
                    self._connections.discard(socket)

    async def publish_log(self, level: str, message: str) -> None:
        await self.publish(
            "log",
            {
                "id": str(uuid4()),
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "message": message,
            },
        )
