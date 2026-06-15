from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import logging
import os
import socket
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from app.core.config import settings
from app.db.database import (
    fetch_core_settings,
    fetch_selected_cloudflare_listener,
    fetch_profile_by_id,
    fetch_routing_config,
)
from app.services.curl_socks import EgressLookupError, curl_available, fetch_egress_via_socks5, probe_latency_via_socks5
from app.services.network_counters import total_rxtx_bytes
from app.services.singbox import (
    DEFAULT_TUN_NAME,
    Profile,
    RunningInstance,
    make_proxy_opener,
    pick_free_port,
    record_to_profile,
    cleanup_stale_runtime_artifacts,
    start_profile,
    stop_all_warm_instances,
    stop_instance,
)
from app.services.system_proxy import enable_system_proxy, get_system_proxy_state, restore_system_proxy_state
from app.services.transport import manager as transport_manager
from app.services.transport.traffic import _traffic

CoreRuntime = Literal["sing-box", "tcp-inject"]
ConnectionState = Literal["stopped", "starting", "running", "error"]

logger = logging.getLogger(__name__)

TCP_INJECT_LISTEN_HOST = "0.0.0.0"
TCP_INJECT_LISTEN_PORT = 40443
TCP_INJECT_CONNECT_PORT = 443
STOP_TIMEOUT_SECONDS = 6.0


@dataclass
class RuntimeStatus:
    state: ConnectionState = "stopped"
    runtime: CoreRuntime | None = None
    active_profile_id: str | None = None
    started_at: datetime | None = None
    latency_ms: int | None = None
    download_bps: float = 0.0
    upload_bps: float = 0.0
    session_download_bytes: float = 0.0
    session_upload_bytes: float = 0.0
    restarts: int = 0
    ready: bool = False
    egress_ip: str | None = None
    egress_country: str | None = None
    local_device_ip: str | None = None
    proxy_scope: str = "local"
    listen_host: str = "127.0.0.1"
    http_port: int = 2080
    socks_port: int = 2081
    tun_mode: bool = False
    network_mode: str = "proxy"
    last_error: str | None = None

    def as_dict(self) -> dict:
        uptime = 0
        if self.started_at is not None and self.state == "running":
            uptime = max(0, int((datetime.now(timezone.utc) - self.started_at).total_seconds()))

        return {
            "state": self.state,
            "runtime": self.runtime,
            "activeProfileId": self.active_profile_id,
            "uptimeSeconds": uptime,
            "latencyMs": self.latency_ms,
            "downloadBps": round(self.download_bps, 2),
            "uploadBps": round(self.upload_bps, 2),
            "sessionDownloadBytes": round(self.session_download_bytes, 2),
            "sessionUploadBytes": round(self.session_upload_bytes, 2),
            "restarts": self.restarts,
            "ready": self.ready,
            "egressIp": self.egress_ip,
            "egressCountry": self.egress_country,
            "localDeviceIp": self.local_device_ip,
            "proxyScope": self.proxy_scope,
            "listenHost": self.listen_host,
            "httpPort": self.http_port,
            "socksPort": self.socks_port,
            "tunMode": self.tun_mode,
            "networkMode": self.network_mode,
            "lastError": self.last_error,
        }


class CoreRuntimeService:
    def __init__(
            self,
            publish_event: Callable[[str, dict], Awaitable[None]],
    ) -> None:
        self._publish_event = publish_event
        self._status = RuntimeStatus()
        self._lock = asyncio.Lock()
        self._instance: RunningInstance | None = None
        self._sampler_task: asyncio.Task[None] | None = None
        self._last_sample_monotonic: float | None = None
        self._saved_system_proxy_state: dict | None = None
        self._clash_traffic_stream: urllib.request.addinfourl | None = None
        self._bridge_server: asyncio.Server | None = None
        self._session_baseline_rx = 0
        self._session_baseline_tx = 0
        self._last_counter_rx = 0
        self._last_counter_tx = 0

    async def get_status(self) -> dict:
        async with self._lock:
            await self._refresh_runtime_metadata_locked()
            return self._status.as_dict()

    async def set_active_profile(self, profile_id: str | None) -> dict:
        async with self._lock:
            self._status.active_profile_id = profile_id
            if profile_id:
                await self._emit_log_locked("info", f"active profile selected: {profile_id}")
            await self._emit_status_locked()
            return self._status.as_dict()

    async def start(self, runtime: CoreRuntime, profile_id: str | None) -> dict:
        async with self._lock:
            if self._status.state in {"starting", "running"}:
                if profile_id:
                    self._status.active_profile_id = profile_id
                await self._emit_status_locked()
                return self._status.as_dict()

            self._status.state = "starting"
            self._status.runtime = runtime
            self._status.active_profile_id = profile_id
            self._status.started_at = None
            self._status.latency_ms = None
            self._status.download_bps = 0
            self._status.upload_bps = 0
            self._status.session_download_bytes = 0
            self._status.session_upload_bytes = 0
            self._status.ready = False
            self._status.egress_ip = None
            self._status.egress_country = None
            self._status.last_error = None
            self._last_sample_monotonic = None
            self._reset_network_counters_locked()
            await self._refresh_runtime_metadata_locked()
            await self._emit_status_locked()
            await self._emit_log_locked("info", f"starting runtime={runtime} profile={profile_id or 'auto'}")

            try:
                await asyncio.to_thread(stop_all_warm_instances)
                await asyncio.to_thread(cleanup_stale_runtime_artifacts, 0.0)
                await self._spawn_runtime_locked()
                await self._apply_system_proxy_locked()
                self._status.state = "running"
                self._status.started_at = datetime.now(timezone.utc)
                self._status.ready = True
                self._last_sample_monotonic = time.monotonic()
                self._prime_network_counters_locked()
                self._start_sampler_locked()
                await self._refresh_egress_info_locked()
                await self._emit_log_locked("info", f"runtime={runtime} started")
            except Exception as exc:  # noqa: BLE001
                await self._stop_bridge_locked()
                transport_manager.stop_injector()
                await self._cleanup_instance_locked()
                await self._restore_system_proxy_locked()
                self._status.state = "error"
                self._status.started_at = None
                self._status.ready = False
                self._status.last_error = str(exc)
                await self._emit_log_locked(
                    "error",
                    f"failed to start runtime: {exc}",
                    trace=traceback.format_exc(),
                )

            await self._emit_status_locked()
            return self._status.as_dict()

    async def stop(self) -> dict:
        async with self._lock:
            try:
                await asyncio.wait_for(self._stop_runtime_locked(), timeout=STOP_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                await self._emit_log_locked("warning", "runtime stop timed out; forcing final state to stopped")
            self._status.state = "stopped"
            self._status.started_at = None
            self._status.latency_ms = None
            self._status.download_bps = 0
            self._status.upload_bps = 0
            self._status.session_download_bytes = 0
            self._status.session_upload_bytes = 0
            self._status.ready = False
            self._status.egress_ip = None
            self._status.egress_country = None
            self._status.last_error = None
            self._last_sample_monotonic = None
            self._reset_network_counters_locked()
            await self._refresh_runtime_metadata_locked()
            await self._emit_log_locked("info", "runtime stopped")
            await self._emit_status_locked()
            return self._status.as_dict()

    async def shutdown(self) -> None:
        async with self._lock:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop_runtime_locked(), timeout=STOP_TIMEOUT_SECONDS)
        await asyncio.to_thread(stop_all_warm_instances)

    async def restart(self, reason: str = "manual") -> dict:
        async with self._lock:
            if self._status.runtime is None:
                return self._status.as_dict()
            runtime = self._status.runtime
            profile_id = self._status.active_profile_id

        await self.stop()
        await self._publish_event(
            "log",
            {
                "id": str(uuid4()),
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": "info",
                "message": f"restarting runtime due to {reason}",
            },
        )
        return await self.start(runtime, profile_id)

    async def on_profile_or_settings_changed(self, reason: str) -> dict:
        async with self._lock:
            if self._status.state != "running":
                return self._status.as_dict()
        core_settings = await fetch_core_settings()
        if bool(core_settings.get("autoReconnect", True)):
            return await self.restart(reason=reason)
        return await self.get_status()

    async def refresh_egress(self) -> dict:
        async with self._lock:
            if self._status.state != "running":
                return self._status.as_dict()
            await self._refresh_runtime_metadata_locked()
            await self._refresh_egress_info_locked()
            await self._emit_status_locked()
            return self._status.as_dict()

    def get_runtime_mode(self) -> str:
        """Return current runtime mode: 'sing-box' or 'tcp-inject'."""
        return self._status.runtime or "sing-box"

    @staticmethod
    def _binary_name(runtime: CoreRuntime) -> str:
        return "sing-box.exe" if settings.root_dir.drive else "sing-box"

    @staticmethod
    def _listener_bridge_target(listener: dict[str, object] | None) -> tuple[str, int] | None:
        if listener is None:
            return None
        listen_host = TCP_INJECT_LISTEN_HOST
        listen_port = TCP_INJECT_LISTEN_PORT
        connect_host = "127.0.0.1" if listen_host == "0.0.0.0" else listen_host
        return connect_host, listen_port

    @classmethod
    def _rewrite_profile_for_bridge(cls, profile: Profile, listener: dict[str, object] | None) -> Profile:
        bridge_target = cls._listener_bridge_target(listener)
        if bridge_target is None:
            return profile
        listen_host, listen_port = bridge_target
        return Profile(
            scheme=profile.scheme,
            name=profile.name,
            server=listen_host,
            port=listen_port,
            uuid_or_password=profile.uuid_or_password,
            username=profile.username,
            tls=profile.tls,
            network=profile.network,
            sni=profile.sni or profile.server,
            alpn=list(profile.alpn),
            allow_insecure=profile.allow_insecure,
            fingerprint=profile.fingerprint,
            reality_public_key=profile.reality_public_key,
            reality_short_id=profile.reality_short_id,
            reality_spider_x=profile.reality_spider_x,
            remark=profile.remark,
            extras=dict(profile.extras),
        )

    async def _claim_runtime_port_locked(self, host: str, preferred: int, label: str) -> int:
        if preferred <= 0 or preferred > 65535:
            raise RuntimeError(f"{label} port is outside the valid range")
        if await asyncio.to_thread(self._can_bind_tcp, host, preferred):
            return preferred
        replacement = await asyncio.to_thread(pick_free_port)
        await self._emit_log_locked(
            "warning",
            f"{label} port {preferred} is busy; using free port {replacement}",
        )
        return replacement

    async def _spawn_runtime_locked(self) -> None:
        profile_id = self._status.active_profile_id
        if not profile_id:
            raise RuntimeError("No active profile selected")

        record = await fetch_profile_by_id(profile_id)
        if record is None:
            raise RuntimeError("Selected profile does not exist")

        cloudflare_listener = await fetch_selected_cloudflare_listener()
        profile = record_to_profile(record)
        routing = await fetch_routing_config()
        core_settings = await fetch_core_settings()
        transport_mode = core_settings.get("transportMode", "singbox")
        if transport_mode == "tcp-inject" and not self._has_admin_privileges():
            raise RuntimeError(
                "TCP Inject mode requires Administrator privileges on Windows. "
                "Please restart Fracture as Administrator."
            )

        binary_path = settings.singbox_dir / self._binary_name("sing-box")
        if not binary_path.exists():
            raise RuntimeError(f"sing-box binary not found at {binary_path}")

        mode = "tun" if bool(routing.get("tunMode", False)) else "proxy"
        if mode == "tun" and not self._has_admin_privileges():
            raise RuntimeError(
                "TUN mode requires Administrator privileges on Windows. "
                "Please restart Fracture as Administrator or switch to proxy mode."
            )
        listen_host = "0.0.0.0" if str(core_settings.get("proxyScope", "local")).lower() == "lan" else "127.0.0.1"
        readiness_host = "127.0.0.1" if listen_host == "0.0.0.0" else listen_host
        http_port = await self._claim_runtime_port_locked(readiness_host, int(core_settings.get("proxyPort", 2080)),
                                                          "HTTP proxy")
        socks_port = await self._claim_runtime_port_locked(readiness_host, int(core_settings.get("socksPort", 2081)),
                                                           "SOCKS proxy")
        if socks_port == http_port:
            socks_port = await self._claim_runtime_port_locked(readiness_host, pick_free_port(), "SOCKS proxy")
        self._status.listen_host = listen_host
        self._status.http_port = http_port
        self._status.socks_port = socks_port
        self._status.tun_mode = mode == "tun"
        self._status.network_mode = mode
        runtime_profile = profile

        # Extract timeout settings (use defaults if not present)
        connect_timeout_ms = int(core_settings.get("connectTimeoutMs", 3000))
        inject_timeout_ms = int(core_settings.get("injectTimeoutMs", 2000))
        relay_idle_ms = int(core_settings.get("relayIdleTimeoutMs", 120000))
        stale_ms = int(core_settings.get("staleConnectionMs", 30000))
        cleanup_ms = int(core_settings.get("cleanupIntervalMs", 5000))

        if transport_mode == "tcp-inject":
            await self._spawn_tcp_inject_locked(
                profile,
                core_settings,
                cloudflare_listener,
                connect_timeout_ms,
                inject_timeout_ms,
                relay_idle_ms,
                stale_ms,
                cleanup_ms,
            )
            runtime_profile = self._rewrite_profile_for_bridge(profile, cloudflare_listener)
            routing = dict(routing)
            routing["connectIpException"] = str((cloudflare_listener or {}).get("CONNECT_IP", "")).strip()

        await self._emit_log_locked(
            "debug",
            f"runtime mode={transport_mode} profile={profile_id} network={mode} listen={listen_host}:{http_port}/{socks_port}",
        )
        if mode == "tun":
            await self._emit_log_locked("info", "network mode=tun full system tunnel enabled")
        else:
            await self._emit_log_locked(
                "warning",
                "network mode=proxy only proxy-aware apps will use Fracture",
            )

        instance = await asyncio.to_thread(
            start_profile,
            runtime_profile,
            binary_path,
            mode,
            socks_port,
            http_port,
            DEFAULT_TUN_NAME,
            routing,
            listen_host,
            "runtime.json",
            True,
        )
        self._instance = instance
        await self._ensure_readiness_locked()

        if transport_mode == "tcp-inject":
            await self._emit_log_locked("info", f"runtime mode=tcp-inject profile={profile_id}")

    async def _spawn_tcp_inject_locked(self, profile: Profile, core_settings: dict, listener: dict | None,
                                       connect_timeout_ms: int, inject_timeout_ms: int, relay_idle_ms: int,
                                       stale_ms: int, cleanup_ms: int, ) -> None:
        """Start the TCP injector (broad filter) and the local bridge."""
        if listener is None:
            raise RuntimeError("TCP Inject mode requires a selected listener")
        connect_ip = str((listener or {}).get("CONNECT_IP", "")).strip()
        connect_port = TCP_INJECT_CONNECT_PORT
        fake_sni = str((listener or {}).get("FAKE_SNI", "")).strip()
        bridge_host, bridge_port = self._listener_bridge_target(listener) or ("127.0.0.1", TCP_INJECT_LISTEN_PORT)
        if not connect_ip:
            raise RuntimeError("TCP Inject mode requires CONNECT_IP in listener")
        if not fake_sni:
            raise RuntimeError("TCP Inject mode requires FAKE_SNI in listener")

        interface_ipv4 = self._resolve_local_device_ip(exclude_prefixes=("127.", "172.19."))
        if not interface_ipv4:
            raise RuntimeError("TCP Inject mode could not determine a usable local IPv4 address")

        # Start the injector using the new signature with timeouts
        transport_manager.start_injector(
            interface_ipv4,
            connect_ip,  # kept for compatibility / logging only
            connect_port,
            fake_sni.encode(),
            None,  # socks_port (None = don't start local proxies)
            None,  # http_port
            bridge_host,
            False,  # start_local_proxies = False (we only need the bridge)
            connect_timeout_ms,
            inject_timeout_ms,
            relay_idle_ms,
            stale_ms,
            cleanup_ms,
        )

        # Start the bridge server (listens for connections from sing‑box)
        self._bridge_server = await asyncio.start_server(
            transport_manager.handle_bridge_client,
            bridge_host,
            bridge_port,
        )

        target_label = f"{connect_ip}:{connect_port}"
        await self._emit_log_locked(
            "debug",
            f"tcp-inject interface={interface_ipv4} target={target_label} bridge={bridge_host}:{bridge_port}",
        )
        await self._emit_log_locked(
            "info",
            f"tcp-inject self-check hint: GET /api/core/self-check (target={target_label})",
        )

        self._status.runtime = "tcp-inject"

    async def _ensure_readiness_locked(self) -> None:
        if self._instance is None:
            raise RuntimeError("Runtime instance was not created")

        for _ in range(30):
            await asyncio.sleep(0.35)
            process = self._instance.process
            if process.returncode is not None:
                raise RuntimeError(self._instance.last_error_summary())
            if self._check_tcp(self._instance.readiness_host, self._instance.http_port):
                return

        raise RuntimeError(
            f"runtime readiness check failed on {self._instance.readiness_host}:{self._instance.http_port}. "
            f"{self._instance.last_error_summary()}"
        )

    async def _stop_runtime_locked(self) -> None:
        await self._stop_bridge_locked()
        transport_manager.stop_injector()

        if self._sampler_task is not None:
            self._sampler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sampler_task
            self._sampler_task = None

        await asyncio.to_thread(stop_all_warm_instances)
        cleanup_task = asyncio.create_task(self._cleanup_instance_locked())
        restore_task = asyncio.create_task(self._restore_system_proxy_locked())
        results = await asyncio.gather(cleanup_task, restore_task, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                raise result

    async def _stop_bridge_locked(self) -> None:
        server = self._bridge_server
        self._bridge_server = None
        if server is None:
            return
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()

    async def _apply_system_proxy_locked(self) -> None:
        routing = await fetch_routing_config()
        if bool(routing.get("tunMode", False)):
            await self._restore_system_proxy_locked()
            await self._emit_log_locked("debug", "system proxy skipped while TUN mode is active")
            return
        bypass = self._system_proxy_bypass(routing)
        try:
            if self._saved_system_proxy_state is None:
                self._saved_system_proxy_state = await asyncio.to_thread(get_system_proxy_state)
            await asyncio.to_thread(
                enable_system_proxy,
                "127.0.0.1",
                self._status.http_port,
                bypass,
                self._status.socks_port,
            )
        except Exception as exc:  # noqa: BLE001
            saved_state = self._saved_system_proxy_state
            self._saved_system_proxy_state = None
            if saved_state is not None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(restore_system_proxy_state, saved_state)
            await self._emit_log_locked(
                "warning",
                f"system proxy was not applied: {exc}",
                trace=traceback.format_exc(),
            )
        else:
            await self._emit_log_locked("info", "Windows system proxy applied")

    async def _restore_system_proxy_locked(self) -> None:
        saved_state = self._saved_system_proxy_state
        self._saved_system_proxy_state = None
        self._close_clash_traffic_stream()
        if saved_state is None:
            return
        try:
            await asyncio.to_thread(restore_system_proxy_state, saved_state)
        except Exception as exc:  # noqa: BLE001
            await self._emit_log_locked(
                "warning",
                f"system proxy restore failed: {exc}",
                trace=traceback.format_exc(),
            )
        else:
            await self._emit_log_locked("info", "Windows system proxy restored")

    async def _cleanup_instance_locked(self) -> None:
        instance = self._instance
        self._instance = None
        if instance is None:
            return
        await asyncio.to_thread(stop_instance, instance)

    def _close_clash_traffic_stream(self) -> None:
        stream = self._clash_traffic_stream
        self._clash_traffic_stream = None
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.close()

    def _prime_network_counters_locked(self) -> None:
        counters = total_rxtx_bytes()
        if counters is None:
            self._reset_network_counters_locked()
            return
        rx, tx = counters
        self._session_baseline_rx = rx
        self._session_baseline_tx = tx
        self._last_counter_rx = rx
        self._last_counter_tx = tx
        self._status.session_download_bytes = 0
        self._status.session_upload_bytes = 0
        self._status.download_bps = 0
        self._status.upload_bps = 0

    def _reset_network_counters_locked(self) -> None:
        self._session_baseline_rx = 0
        self._session_baseline_tx = 0
        self._last_counter_rx = 0
        self._last_counter_tx = 0
        self._status.session_download_bytes = 0
        self._status.session_upload_bytes = 0
        self._status.download_bps = 0
        self._status.upload_bps = 0

    def _update_network_counters_locked(self, elapsed: float) -> bool:
        counters = total_rxtx_bytes()
        if counters is None:
            self._status.download_bps = 0
            self._status.upload_bps = 0
            return False

        rx, tx = counters
        if self._session_baseline_rx == 0 and self._session_baseline_tx == 0:
            self._session_baseline_rx = rx
            self._session_baseline_tx = tx
        if self._last_counter_rx == 0 and self._last_counter_tx == 0:
            self._last_counter_rx = rx
            self._last_counter_tx = tx

        self._status.session_download_bytes = max(rx - self._session_baseline_rx, 0)
        self._status.session_upload_bytes = max(tx - self._session_baseline_tx, 0)
        if elapsed > 0:
            self._status.download_bps = max(rx - self._last_counter_rx, 0) / elapsed
            self._status.upload_bps = max(tx - self._last_counter_tx, 0) / elapsed
        self._last_counter_rx = rx
        self._last_counter_tx = tx
        return True

    def _start_sampler_locked(self) -> None:
        if self._sampler_task is not None:
            self._sampler_task.cancel()
        self._sampler_task = asyncio.create_task(self._sample_loop())

    async def _sample_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            async with self._lock:
                if self._status.state != "running":
                    return

                # ---- TCP inject branch without sing-box instance ----
                if self._status.runtime == "tcp-inject" and self._instance is None:
                    now = time.monotonic()
                    elapsed = now - self._last_sample_monotonic if self._last_sample_monotonic is not None else 1.0
                    self._last_sample_monotonic = now

                    if not self._update_network_counters_locked(elapsed):
                        down, up = _traffic.consume()
                        if elapsed > 0:
                            self._status.download_bps = down / elapsed
                            self._status.upload_bps = up / elapsed
                        self._status.session_download_bytes += down
                        self._status.session_upload_bytes += up
                    await self._emit_status_locked()
                    continue

                # ---- sing-box mode (existing logic) ----
                if self._instance is None:
                    return
                process = self._instance.process
                if process.returncode is not None:
                    self._status.state = "error"
                    self._status.ready = False
                    self._status.started_at = None
                    self._status.restarts += 1
                    self._status.last_error = "runtime exited unexpectedly"
                    self._last_sample_monotonic = None
                    await self._emit_log_locked("error", "runtime exited unexpectedly")
                    await self._emit_status_locked()
                    return

                now = time.monotonic()
                elapsed = 0.0 if self._last_sample_monotonic is None else max(0.0, now - self._last_sample_monotonic)
                self._last_sample_monotonic = now
                await self._refresh_runtime_metadata_locked()
                if not self._update_network_counters_locked(elapsed):
                    down, up = await asyncio.to_thread(self._read_singbox_traffic_delta)
                    if down is not None and up is not None and elapsed > 0:
                        self._status.download_bps = down / elapsed
                        self._status.upload_bps = up / elapsed
                        self._status.session_download_bytes += down
                        self._status.session_upload_bytes += up
                await self._emit_status_locked()

    async def _refresh_egress_info_locked(self) -> None:
        if self._status.state != "running":
            return

        socks_port = None
        if self._status.runtime == "tcp-inject":
            socks_port = transport_manager.get_active_socks_port()
            if socks_port is None and self._instance is not None:
                socks_port = self._instance.socks_port
        elif self._instance is not None:
            socks_port = self._instance.socks_port

        if socks_port and curl_available():
            try:
                payload = await asyncio.to_thread(fetch_egress_via_socks5, "127.0.0.1", socks_port)
            except EgressLookupError as exc:
                await self._emit_log_locked("debug", str(exc))
            except Exception as exc:  # noqa: BLE001
                await self._emit_log_locked(
                    "debug",
                    f"egress lookup via socks5 failed: {exc}",
                    trace=traceback.format_exc(),
                )
            else:
                self._status.egress_ip = payload.ip or self._status.egress_ip
                self._status.egress_country = payload.country or self._status.egress_country
                with contextlib.suppress(Exception):
                    self._status.latency_ms = await asyncio.to_thread(
                        probe_latency_via_socks5,
                        "127.0.0.1",
                        socks_port,
                        8.0,
                    )
                return

        if self._status.runtime == "tcp-inject" and self._instance is None:
            return

        # For sing-box mode, use the existing HTTP proxy method
        if self._instance is None:
            return
        try:
            payload = await asyncio.to_thread(self._lookup_egress_via_http_proxy, self._instance.http_port)
        except Exception as exc:  # noqa: BLE001
            await self._emit_log_locked(
                "warning",
                f"egress lookup via http proxy failed: {exc}",
                trace=traceback.format_exc(),
            )
            return

        ip = payload.get("ip")
        country = payload.get("country")
        latency_ms = payload.get("latencyMs")
        if ip:
            self._status.egress_ip = ip
        if country:
            self._status.egress_country = country
        if isinstance(latency_ms, int):
            self._status.latency_ms = latency_ms

    async def _refresh_runtime_metadata_locked(self) -> None:
        core_settings = await fetch_core_settings()
        routing = await fetch_routing_config()
        self._status.proxy_scope = str(core_settings.get("proxyScope", "local")).lower()
        self._status.listen_host = "0.0.0.0" if self._status.proxy_scope == "lan" else "127.0.0.1"
        self._status.http_port = int(core_settings.get("proxyPort", 2080))
        self._status.socks_port = int(core_settings.get("socksPort", 2081))
        self._status.tun_mode = bool(routing.get("tunMode", False))
        self._status.network_mode = "tun" if self._status.tun_mode else "proxy"

        self._status.local_device_ip = await asyncio.to_thread(self._resolve_local_device_ip)

    async def _emit_status_locked(self) -> None:
        await self._publish_event("status", self._status.as_dict())

    async def _emit_log_locked(self, level: str, message: str, *, source: str = "runtime",
                               trace: str | None = None) -> None:
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }
        logger.log(level_map.get(level, logging.INFO), message)
        payload = {
            "id": str(uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
            "source": source,
        }
        if trace:
            payload["trace"] = trace
        await self._publish_event("log", payload)

    @staticmethod
    def _check_tcp(host: str, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.35)
        try:
            return sock.connect_ex((host, port)) == 0
        except Exception:
            return False
        finally:
            with contextlib.suppress(Exception):
                sock.close()

    @staticmethod
    def _can_bind_tcp(host: str, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return True
        except OSError:
            return False
        finally:
            with contextlib.suppress(Exception):
                sock.close()

    @staticmethod
    def _lookup_egress_via_http_proxy(port: int) -> dict[str, object]:
        opener = make_proxy_opener(port)
        payload: dict[str, object] = {
            "ip": None,
            "country": None,
            "latencyMs": None,
        }

        start = datetime.now(timezone.utc)
        try:
            with opener.open("https://api.ipify.org?format=json", timeout=5) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
                payload["ip"] = data.get("ip")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass

        try:
            with opener.open("https://ipapi.co/json", timeout=5) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
                payload["country"] = data.get("country") or data.get("country_code") or data.get("country_name")
                if not payload.get("ip"):
                    payload["ip"] = data.get("ip")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass

        elapsed = max((datetime.now(timezone.utc) - start).total_seconds(), 0.001)
        payload["latencyMs"] = max(1, int(elapsed * 1000))
        return payload

    def _read_singbox_traffic_delta(self) -> tuple[int | None, int | None]:
        instance = self._instance
        if instance is None or instance.clash_api_port is None:
            return None, None

        try:
            if self._clash_traffic_stream is None:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{instance.clash_api_port}/traffic",
                    headers={"User-Agent": "Fracture"},
                )
                self._clash_traffic_stream = urllib.request.urlopen(request, timeout=2)

            line = self._clash_traffic_stream.readline()
            if not line:
                self._close_clash_traffic_stream()
                return None, None
            payload = json.loads(line.decode("utf-8", errors="replace"))
            down = payload.get("down")
            up = payload.get("up")
            if isinstance(down, (int, float)) and isinstance(up, (int, float)):
                return int(down), int(up)
        except Exception:
            self._close_clash_traffic_stream()
        return None, None

    @staticmethod
    def _system_proxy_bypass(routing: dict[str, object]) -> str:
        raw = str(routing.get("bypassDomains", "")).strip()
        domains = [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]
        required_entries = ("<local>", "localhost", "127.0.0.1", "::1")
        existing = {item.lower() for item in domains}
        for entry in required_entries:
            if entry.lower() not in existing:
                domains.append(entry)
        return ";".join(domains)

    @staticmethod
    def _resolve_local_device_ip(exclude_prefixes: tuple[str, ...] = ("127.",)) -> str | None:
        candidates: list[str] = []

        try:
            hostname = socket.gethostname()
            for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = str(sockaddr[0])
                if ip and not any(ip.startswith(prefix) for prefix in exclude_prefixes):
                    candidates.append(ip)
        except Exception:
            pass

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                ip = str(sock.getsockname()[0])
                if ip and not any(ip.startswith(prefix) for prefix in exclude_prefixes):
                    candidates.append(ip)
        except Exception:
            pass

        for candidate in candidates:
            if candidate:
                return candidate
        return None

    @staticmethod
    def _has_admin_privileges() -> bool:
        if os.name != "nt":
            return True
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
