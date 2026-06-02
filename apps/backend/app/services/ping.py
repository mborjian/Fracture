from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import traceback
from uuid import uuid4

from app.core.config import settings
from app.db.database import (
    fetch_core_settings,
    fetch_selected_cloudflare_listener,
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
        self._probe_bridge_lock = asyncio.Lock()
        self._probe_bridge_refs = 0
        self._probe_bridge_server: asyncio.Server | None = None

    async def _emit_log(self, level: str, message: str, *, source: str = "ping", trace: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            "id": str(uuid4()),
            "ts": now.isoformat(),
            "level": level,
            "message": message,
            "source": source,
        }
        if trace:
            payload["trace"] = trace
        await self._publish_event("log", payload)

    async def ping_profile(self, profile_id: str, timeout_s: float = 8.0, probe_mode: ProbeMode = "quick") -> dict:
        profile = await fetch_profile_by_id(profile_id)
        if profile is None:
            raise ValueError("Profile not found")

        routing = await fetch_routing_config()
        core_settings = await fetch_core_settings()
        listener = await fetch_selected_cloudflare_listener()
        async with self._temporary_probe_bridge(core_settings, listener):
            result = await asyncio.to_thread(self._delay_probe_sync, profile, routing, core_settings, listener, probe_mode)
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
        if result["ok"]:
            await self._emit_log(
                "info",
                f"delay profile={profile_id} latency={result['latencyMs']} ms mode={probe_mode}",
            )
        else:
            await self._emit_log(
                "warning",
                f"delay profile={profile_id} failed: {result.get('error') or 'unknown error'}",
            )
        return payload

    async def ping_all(self, profile_ids: list[str], timeout_s: float = 8.0, probe_mode: ProbeMode = "quick") -> dict:
        async with self._lock:
            if self._state.running:
                raise RuntimeError("Delay test is already running")
            self._state.running = True
            self._state.cancel_requested = False
            self._state.mode = "delay"

        await self._emit_log("info", f"delay test started profiles={len(profile_ids)} mode={probe_mode}")

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
            await self._emit_log(
                "info",
                f"delay test finished completed={completed} ok={successes} failed={failures}{' cancelled' if cancelled else ''}",
            )
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
        async with self._probe_bridge_lock:
            self._probe_bridge_refs = 0
            await self._stop_probe_bridge_locked()

    async def speed_profile(self, profile_id: str, timeout_s: float = 15.0, probe_mode: ProbeMode = "quick") -> dict:
        profile = await fetch_profile_by_id(profile_id)
        if profile is None:
            raise ValueError("Profile not found")

        routing = await fetch_routing_config()
        core_settings = await fetch_core_settings()
        listener = await fetch_selected_cloudflare_listener()
        async with self._temporary_probe_bridge(core_settings, listener):
            result = await asyncio.to_thread(self._speed_probe_sync, profile, routing, core_settings, listener, timeout_s, probe_mode)
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
        if result["ok"]:
            await self._emit_log(
                "info",
                f"speed profile={profile_id} throughput={result['speedMBps']} MB/s mode={probe_mode}",
            )
        else:
            await self._emit_log(
                "warning",
                f"speed profile={profile_id} failed: {result.get('error') or 'unknown error'}",
            )
        return payload

    async def speed_all(self, profile_ids: list[str], timeout_s: float = 15.0, probe_mode: ProbeMode = "quick") -> dict:
        async with self._lock:
            if self._state.running:
                raise RuntimeError("Speed test is already running")
            self._state.running = True
            self._state.cancel_requested = False
            self._state.mode = "speed"

        await self._emit_log("info", f"speed test started profiles={len(profile_ids)} mode={probe_mode}")

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
            await self._emit_log(
                "info",
                f"speed test finished completed={completed} ok={successes} failed={failures}{' cancelled' if cancelled else ''}",
            )
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
                        await self._emit_log("warning", f"delay profile={profile_id} failed: {exc}", trace=traceback.format_exc())
                    else:
                        await save_profile_speed_result(profile_id, None, now_iso)
                        await self._publish_event(
                            "speed",
                            {
                                "profileId": profile_id,
                                "ok": False,
                                "speedMBps": None,
                                "error": str(exc),
                                "at": now_iso,
                            },
                        )
                        await self._emit_log("warning", f"speed profile={profile_id} failed: {exc}", trace=traceback.format_exc())
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

    @contextlib.asynccontextmanager
    async def _temporary_probe_bridge(self, core_settings: dict, listener: dict | None):
        active_tcp_inject_runtime = (
            self._runtime_service._status.state == "running"
            and self._runtime_service.get_runtime_mode() == "tcp-inject"
            and self._runtime_service._instance is None
        )
        should_start = (
            core_settings.get("transportMode") == "tcp-inject"
            and not active_tcp_inject_runtime
        )
        if not should_start:
            yield
            return

        async with self._probe_bridge_lock:
            if self._probe_bridge_refs == 0:
                await self._start_probe_bridge_locked(listener)
            self._probe_bridge_refs += 1
        try:
            yield
        finally:
            async with self._probe_bridge_lock:
                self._probe_bridge_refs = max(0, self._probe_bridge_refs - 1)
                if self._probe_bridge_refs == 0:
                    await self._stop_probe_bridge_locked()

    async def _start_probe_bridge_locked(self, listener: dict | None) -> None:
        if listener is None:
            raise RuntimeError("TCP Inject probes require a selected listener")
        connect_ip = str(listener.get("CONNECT_IP", "")).strip()
        connect_port = int(listener.get("CONNECT_PORT", 443))
        fake_sni = str(listener.get("FAKE_SNI", "")).strip()
        if not connect_ip:
            raise RuntimeError("TCP Inject probes require CONNECT_IP in listener")
        if not fake_sni:
            raise RuntimeError("TCP Inject probes require FAKE_SNI in listener")

        bridge_host, bridge_port = self._runtime_service._listener_bridge_target(listener) or ("127.0.0.1", 40443)
        interface_ipv4 = self._runtime_service._resolve_local_device_ip()
        if not interface_ipv4:
            raise RuntimeError("TCP Inject probes could not determine a usable local IPv4 address")
        try:
            transport_manager.start_injector(
                interface_ipv4,
                connect_ip,
                connect_port,
                fake_sni.encode(),
                None,
                None,
                bridge_host,
                False,
            )
            self._probe_bridge_server = await asyncio.start_server(
                transport_manager.handle_bridge_client,
                bridge_host,
                bridge_port,
            )
        except Exception:
            await self._stop_probe_bridge_locked()
            raise

    async def _stop_probe_bridge_locked(self) -> None:
        server = self._probe_bridge_server
        self._probe_bridge_server = None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        transport_manager.stop_injector()

    def _delay_probe_sync(
        self,
        profile_record: dict,
        routing: dict,
        core_settings: dict,
        listener: dict | None,
        probe_mode: ProbeMode,
    ) -> dict:
        # Check current runtime mode
        mode = self._runtime_service.get_runtime_mode()
        runtime_instance = self._runtime_service._instance
        if self._runtime_service._status.state == "running" and mode == "tcp-inject" and runtime_instance is None:
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
            profile = self._probe_profile_for_transport(profile_record, routing, core_settings, listener)
            latency_ms = test_delay(profile, binary_path, routing=routing, mode=probe_mode)
            return {"ok": True, "latencyMs": max(1, int(latency_ms))}
        except Exception as exc:
            return {"ok": False, "latencyMs": -1, "error": str(exc)}

    def _speed_probe_sync(
        self,
        profile_record: dict,
        routing: dict,
        core_settings: dict,
        listener: dict | None,
        timeout_s: float,
        probe_mode: ProbeMode,
    ) -> dict:
        mode = self._runtime_service.get_runtime_mode()
        runtime_instance = self._runtime_service._instance
        if self._runtime_service._status.state == "running" and mode == "tcp-inject" and runtime_instance is None:
            try:
                bytes_per_sec = transport_manager.test_speed_via_socks5(timeout_s=timeout_s)
                return {"ok": True, "speedMBps": round(bytes_per_sec / (1024 * 1024), 2)}
            except Exception as exc:
                return {"ok": False, "speedMBps": None, "error": str(exc)}
        # Else sing-box mode
        binary_path = settings.singbox_dir / self._runtime_service._binary_name("sing-box")
        if not binary_path.exists():
            return {"ok": False, "speedMBps": None, "error": f"sing-box binary not found at {binary_path}"}
        try:
            effective_seconds = min(timeout_s, 4.0) if probe_mode == "quick" else min(timeout_s, 10.0)
            profile = self._probe_profile_for_transport(profile_record, routing, core_settings, listener)
            bytes_per_sec = test_speed(
                profile,
                binary_path,
                routing=routing,
                seconds=max(0.8 if probe_mode == "quick" else 2.0, effective_seconds),
                mode=probe_mode,
            )
            return {"ok": True, "speedMBps": round(bytes_per_sec / (1024 * 1024), 2)}
        except Exception as exc:
            return {"ok": False, "speedMBps": None, "error": str(exc)}

    def _probe_profile_for_transport(
        self,
        profile_record: dict,
        routing: dict,
        core_settings: dict,
        listener: dict | None,
    ):
        profile = record_to_profile(profile_record)
        if core_settings.get("transportMode") != "tcp-inject":
            return profile
        rewritten = self._runtime_service._rewrite_profile_for_bridge(profile, listener)
        if listener is not None:
            routing["connectIpException"] = str(listener.get("CONNECT_IP", "")).strip()
        return rewritten
