from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import settings
from app.db.database import (
    fetch_profile_by_id,
    fetch_routing_config,
    save_profile_ping_result,
    save_profile_speed_result,
)
from app.services.runtime import CoreRuntimeService
from app.services.singbox import record_to_profile, stop_all_warm_instances, test_delay, test_speed
from app.services.transport import manager as transport_manager


@dataclass
class PingTaskState:
    running: bool = False
    cancel_requested: bool = False
    mode: str | None = None


ProbeMode = str


class ProfilePingService:
    def __init__(
            self,
            publish_event: Callable[[str, dict], Awaitable[None]],
            runtime_service: CoreRuntimeService,
    ) -> None:
        self._publish_event = publish_event
        self._runtime_service = runtime_service
        self._state = PingTaskState()
        self._lock = asyncio.Lock()
        self._delay_parallelism = 4
        self._speed_parallelism = 3

    async def ping_profile(self, profile_id: str, timeout_s: float = 8.0, probe_mode: ProbeMode = "quick") -> dict:
        profile = await fetch_profile_by_id(profile_id)
        if profile is None:
            raise ValueError("Profile not found")

        routing = await fetch_routing_config()
        result = await asyncio.to_thread(self._delay_probe_sync, profile, routing, probe_mode)
        now_iso = datetime.now(timezone.utc).isoformat()
        await save_profile_ping_result(
            profile_id,
            result["latencyMs"],
            now_iso,
            success=result["ok"],
        )

        payload = {
            "profileId": profile_id,
            "ok": result["ok"],
            "latencyMs": result["latencyMs"],
            "error": result.get("error"),
            "at": now_iso,
        }
        await self._publish_event("ping", payload)
        return payload

    async def ping_all(self, profile_ids: list[str], timeout_s: float = 8.0, probe_mode: ProbeMode = "quick") -> dict:
        async with self._lock:
            if self._state.running:
                raise RuntimeError("Delay test is already running")
            self._state.running = True
            self._state.cancel_requested = False
            self._state.mode = "delay"

        completed = 0
        successes = 0
        failures = 0
        cancelled = False

        try:
            results = await self._probe_all(profile_ids, timeout_s, mode="delay", probe_mode=probe_mode)
            completed = results["completed"]
            successes = results["successes"]
            failures = results["failures"]
            cancelled = results["cancelled"]

            summary = {
                "running": False,
                "completed": completed,
                "successes": successes,
                "failures": failures,
                "cancelled": cancelled,
            }
            await self._publish_event("ping-summary", summary)
            return summary
        finally:
            await asyncio.to_thread(stop_all_warm_instances)
            async with self._lock:
                self._state.running = False
                self._state.cancel_requested = False
                self._state.mode = None

    async def cancel_ping_all(self) -> dict:
        async with self._lock:
            if not self._state.running:
                return {"ok": False, "message": "No bulk test job is running"}
            self._state.cancel_requested = True
            return {"ok": True}

    async def shutdown(self) -> None:
        async with self._lock:
            self._state.cancel_requested = True
        await asyncio.to_thread(stop_all_warm_instances)

    async def speed_profile(self, profile_id: str, timeout_s: float = 15.0, probe_mode: ProbeMode = "quick") -> dict:
        profile = await fetch_profile_by_id(profile_id)
        if profile is None:
            raise ValueError("Profile not found")

        routing = await fetch_routing_config()
        result = await asyncio.to_thread(self._speed_probe_sync, profile, routing, timeout_s, probe_mode)
        now_iso = datetime.now(timezone.utc).isoformat()
        await save_profile_speed_result(profile_id, result["speedMBps"], now_iso)

        payload = {
            "profileId": profile_id,
            "ok": result["ok"],
            "speedMBps": result["speedMBps"],
            "error": result.get("error"),
            "at": now_iso,
        }
        await self._publish_event("speed", payload)
        return payload

    async def speed_all(self, profile_ids: list[str], timeout_s: float = 15.0, probe_mode: ProbeMode = "quick") -> dict:
        async with self._lock:
            if self._state.running:
                raise RuntimeError("Speed test is already running")
            self._state.running = True
            self._state.cancel_requested = False
            self._state.mode = "speed"

        completed = 0
        successes = 0
        failures = 0
        cancelled = False

        try:
            results = await self._probe_all(profile_ids, timeout_s, mode="speed", probe_mode=probe_mode)
            completed = results["completed"]
            successes = results["successes"]
            failures = results["failures"]
            cancelled = results["cancelled"]

            summary = {
                "running": False,
                "completed": completed,
                "successes": successes,
                "failures": failures,
                "cancelled": cancelled,
            }
            await self._publish_event("speed-summary", summary)
            return summary
        finally:
            await asyncio.to_thread(stop_all_warm_instances)
            async with self._lock:
                self._state.running = False
                self._state.cancel_requested = False
                self._state.mode = None

    async def _probe_all(self, profile_ids: list[str], timeout_s: float, mode: str, probe_mode: ProbeMode) -> dict:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        for profile_id in profile_ids:
            queue.put_nowait(profile_id)

        parallelism = min(
            len(profile_ids),
            self._delay_parallelism if mode == "delay" else self._speed_parallelism,
        )
        parallelism = max(parallelism, 1)
        for _ in range(parallelism):
            queue.put_nowait(None)

        counters = {"completed": 0, "successes": 0, "failures": 0}
        counter_lock = asyncio.Lock()

        async def worker() -> None:
            while True:
                profile_id = await queue.get()
                if profile_id is None:
                    return

                async with self._lock:
                    if self._state.cancel_requested:
                        return

                try:
                    if mode == "delay":
                        result = await self.ping_profile(profile_id, timeout_s=timeout_s, probe_mode=probe_mode)
                        ok = bool(result["ok"])
                    else:
                        result = await self.speed_profile(profile_id, timeout_s=timeout_s, probe_mode=probe_mode)
                        ok = bool(result["ok"])
                except Exception as exc:  # noqa: BLE001
                    now_iso = datetime.now(timezone.utc).isoformat()
                    if mode == "delay":
                        await save_profile_ping_result(profile_id, None, now_iso, success=False)
                        await self._publish_event(
                            "ping",
                            {
                                "profileId": profile_id,
                                "ok": False,
                                "latencyMs": None,
                                "error": str(exc),
                                "at": now_iso,
                            },
                        )
                    else:
                        await save_profile_speed_result(profile_id, 0.0, now_iso)
                        await self._publish_event(
                            "speed",
                            {
                                "profileId": profile_id,
                                "ok": False,
                                "speedMBps": 0.0,
                                "error": str(exc),
                                "at": now_iso,
                            },
                        )
                    ok = False

                async with counter_lock:
                    counters["completed"] += 1
                    if ok:
                        counters["successes"] += 1
                    else:
                        counters["failures"] += 1

        workers = [asyncio.create_task(worker()) for _ in range(parallelism)]
        await asyncio.gather(*workers)

        async with self._lock:
            cancelled = self._state.cancel_requested

        return {
            "completed": counters["completed"],
            "successes": counters["successes"],
            "failures": counters["failures"],
            "cancelled": cancelled,
        }

    def _delay_probe_sync(self, profile_record: dict, routing: dict, probe_mode: ProbeMode) -> dict:
        # Check current runtime mode
        mode = self._runtime_service.get_runtime_mode()
        if mode == "tcp-inject":
            try:
                timeout = 3.0 if probe_mode == "quick" else 6.0
                latency_ms = transport_manager.test_delay_via_socks5(timeout_s=timeout)
                return {"ok": True, "latencyMs": max(1, int(latency_ms))}
            except Exception as exc:
                return {"ok": False, "latencyMs": -1, "error": str(exc)}
        # Else sing-box mode
        binary_path = settings.singbox_dir / self._runtime_service._binary_name("sing-box")
        if not binary_path.exists():
            return {"ok": False, "latencyMs": None, "error": f"sing-box binary not found at {binary_path}"}
        try:
            latency_ms = test_delay(record_to_profile(profile_record), binary_path, routing=routing, mode=probe_mode)
            return {"ok": True, "latencyMs": max(1, int(latency_ms))}
        except Exception as exc:
            return {"ok": False, "latencyMs": -1, "error": str(exc)}

    def _speed_probe_sync(self, profile_record: dict, routing: dict, timeout_s: float, probe_mode: ProbeMode) -> dict:
        mode = self._runtime_service.get_runtime_mode()
        if mode == "tcp-inject":
            try:
                bytes_per_sec = transport_manager.test_speed_via_socks5(timeout_s=timeout_s)
                return {"ok": True, "speedMBps": round(bytes_per_sec / (1024 * 1024), 2)}
            except Exception as exc:
                return {"ok": False, "speedMBps": 0.0, "error": str(exc)}
        # Else sing-box mode
        binary_path = settings.singbox_dir / self._runtime_service._binary_name("sing-box")
        if not binary_path.exists():
            return {"ok": False, "speedMBps": None, "error": f"sing-box binary not found at {binary_path}"}
        try:
            effective_seconds = min(timeout_s, 4.0) if probe_mode == "quick" else min(timeout_s, 10.0)
            bytes_per_sec = test_speed(
                record_to_profile(profile_record),
                binary_path,
                routing=routing,
                seconds=max(0.8 if probe_mode == "quick" else 2.0, effective_seconds),
                mode=probe_mode,
            )
            return {"ok": True, "speedMBps": round(bytes_per_sec / (1024 * 1024), 2)}
        except Exception as exc:
            return {"ok": False, "speedMBps": 0.0, "error": str(exc)}
