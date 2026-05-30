from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import time
import urllib.error
import traceback
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
from app.services.singbox import (
    DEFAULT_TUN_NAME,
    Profile,
    RunningInstance,
    make_proxy_opener,
    record_to_profile,
    start_profile,
    stop_all_warm_instances,
    stop_instance,
)
from app.services.transport import manager as transport_manager
from app.services.transport.traffic import fetch_egress_via_socks5

CoreRuntime = Literal["sing-box"]
ConnectionState = Literal["stopped", "starting", "running", "error"]

logger = logging.getLogger(__name__)


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
            await self._refresh_runtime_metadata_locked()
            await self._emit_status_locked()
            await self._emit_log_locked("info", f"starting runtime={runtime} profile={profile_id or 'auto'}")

            try:
                await self._spawn_runtime_locked()
                self._status.state = "running"
                self._status.started_at = datetime.now(timezone.utc)
                self._status.ready = True
                self._last_sample_monotonic = time.monotonic()
                self._start_sampler_locked()
                await self._refresh_egress_info_locked()
                await self._emit_log_locked("info", f"runtime={runtime} started")
            except Exception as exc:  # noqa: BLE001
                await self._cleanup_instance_locked()
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
            await self._stop_runtime_locked()
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
            await self._refresh_runtime_metadata_locked()
            await self._emit_log_locked("info", "runtime stopped")
            await self._emit_status_locked()
            return self._status.as_dict()

    async def shutdown(self) -> None:
        async with self._lock:
            await self._stop_runtime_locked()
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
    def _apply_cloudflare_listener(profile: Profile, listener: dict[str, object] | None) -> Profile:
        if listener is None:
            return profile

        connect_ip = str(listener.get("CONNECT_IP", "")).strip()
        connect_port_raw = listener.get("CONNECT_PORT", profile.port)
        fake_sni = str(listener.get("FAKE_SNI", "")).strip()
        if not connect_ip:
            return profile

        needs_listener_resolution = profile.server in {"127.0.0.1", "0.0.0.0", "localhost"}
        if not needs_listener_resolution:
            return profile

        extras = dict(profile.extras)
        if fake_sni:
            extras["host"] = fake_sni
            if not profile.sni:
                profile = Profile(**{**profile.__dict__, "sni": fake_sni})

        return Profile(
            scheme=profile.scheme,
            name=profile.name,
            server=connect_ip,
            port=int(connect_port_raw),
            uuid_or_password=profile.uuid_or_password,
            username=profile.username,
            tls=profile.tls,
            network=profile.network,
            sni=profile.sni or fake_sni,
            alpn=profile.alpn,
            allow_insecure=profile.allow_insecure,
            fingerprint=profile.fingerprint,
            reality_public_key=profile.reality_public_key,
            reality_short_id=profile.reality_short_id,
            reality_spider_x=profile.reality_spider_x,
            remark=profile.remark,
            extras=extras,
        )

    async def _spawn_runtime_locked(self) -> None:
        profile_id = self._status.active_profile_id
        if not profile_id:
            raise RuntimeError("No active profile selected")

        record = await fetch_profile_by_id(profile_id)
        if record is None:
            raise RuntimeError("Selected profile does not exist")

        binary_path = settings.singbox_dir / self._binary_name("sing-box")
        if not binary_path.exists():
            raise RuntimeError(f"sing-box binary not found at {binary_path}")

        profile = record_to_profile(record)
        cloudflare_listener = await fetch_selected_cloudflare_listener()
        profile = self._apply_cloudflare_listener(profile, cloudflare_listener)
        routing = await fetch_routing_config()
        core_settings = await fetch_core_settings()
        transport_mode = core_settings.get("transportMode", "singbox")

        if transport_mode == "tcp-inject":
            # Use TCP injection instead of sing-box
            await self._emit_log_locked("info", f"runtime mode=tcp-inject profile={profile_id}")
            await self._spawn_tcp_inject_locked(profile, core_settings, cloudflare_listener)
            return

        mode = "tun" if bool(routing.get("tunMode", False)) else "proxy"
        http_port = int(core_settings.get("proxyPort", 2080))
        socks_port = int(core_settings.get("socksPort", 2081))
        listen_host = "0.0.0.0" if str(core_settings.get("proxyScope", "local")).lower() == "lan" else "127.0.0.1"
        self._status.listen_host = listen_host
        await self._emit_log_locked(
            "debug",
            f"runtime mode=sing-box profile={profile_id} network={mode} listen={listen_host}:{http_port}/{socks_port}",
        )

        instance = await asyncio.to_thread(
            start_profile,
            profile,
            binary_path,
            mode,
            socks_port,
            http_port,
            DEFAULT_TUN_NAME,
            routing,
            listen_host,
            "runtime.json",
        )
        self._instance = instance
        await self._ensure_readiness_locked()

    async def _spawn_tcp_inject_locked(self, profile: Profile, core_settings: dict, listener: dict | None) -> None:
        # Build real server details from listener (same as _apply_cloudflare_listener)
        if listener and profile.server in {"127.0.0.1", "0.0.0.0", "localhost"}:
            connect_ip = str(listener.get("CONNECT_IP", "")).strip()
            connect_port = int(listener.get("CONNECT_PORT", profile.port))
            fake_sni = str(listener.get("FAKE_SNI", "")).strip()
            if not connect_ip:
                raise RuntimeError("TCP Inject mode requires CONNECT_IP in listener")
        else:
            connect_ip = profile.server
            connect_port = profile.port
            fake_sni = profile.sni or ""

        interface_ipv4 = self._resolve_local_device_ip() or "0.0.0.0"
        # Start the background injector
        socks_port = int(core_settings.get("socksPort", 2081))
        transport_manager.start_injector(interface_ipv4, connect_ip, connect_port, fake_sni.encode(), socks_port)
        await self._emit_log_locked(
            "debug",
            f"tcp-inject interface={interface_ipv4} target={connect_ip}:{connect_port} socks={socks_port}",
        )

        # Now we need to "claim" that the runtime is ready – but the actual per‑connection
        # injection will happen inside handle() of the main TCP listener.
        # For status reporting we mark as running immediately.
        self._status.ready = True
        self._status.runtime = "tcp-inject"
        # We won't have a sing‑box process, so we set instance to None
        self._instance = None

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
        transport_manager.stop_injector()

        if self._sampler_task is not None:
            self._sampler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sampler_task
            self._sampler_task = None

        await self._cleanup_instance_locked()

    async def _cleanup_instance_locked(self) -> None:
        instance = self._instance
        self._instance = None
        if instance is None:
            return
        await asyncio.to_thread(stop_instance, instance)

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

                # ---- TCP inject mode branch ----
                if self._status.runtime == "tcp-inject":
                    now = time.monotonic()
                    elapsed = now - self._last_sample_monotonic if self._last_sample_monotonic is not None else 1.0
                    self._last_sample_monotonic = now

                    # Get traffic from the SOCKS5 relay
                    down = _traffic.download_bytes
                    up = _traffic.upload_bytes

                    self._status.download_bps = down / elapsed
                    self._status.upload_bps = up / elapsed
                    self._status.session_download_bytes += down
                    self._status.session_upload_bytes += up

                    # Reset traffic counters for next second
                    _traffic.download_bytes = 0
                    _traffic.upload_bytes = 0

                    await self._refresh_egress_info_locked()
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
                await self._refresh_egress_info_locked()
                self._status.session_download_bytes += self._status.download_bps * elapsed
                self._status.session_upload_bytes += self._status.upload_bps * elapsed
                await self._emit_status_locked()

    async def _refresh_egress_info_locked(self) -> None:
        if self._status.state != "running":
            return

        # For TCP inject mode, use SOCKS5 proxy to fetch egress info
        if self._status.runtime == "tcp-inject":
            socks_port = transport_manager.get_active_socks_port()
            if socks_port:
                try:
                    ip, country = await fetch_egress_via_socks5(socks_port)
                except Exception as exc:  # noqa: BLE001
                    await self._emit_log_locked(
                        "warning",
                        f"egress lookup via socks5 failed: {exc}",
                        trace=traceback.format_exc(),
                    )
                else:
                    if ip:
                        self._status.egress_ip = ip
                    if country:
                        self._status.egress_country = country
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
        download_bps = payload.get("downloadBps")

        if ip:
            self._status.egress_ip = ip
        if country:
            self._status.egress_country = country
        if isinstance(latency_ms, int):
            self._status.latency_ms = latency_ms
        if isinstance(download_bps, (int, float)):
            self._status.download_bps = float(download_bps)

    async def _refresh_runtime_metadata_locked(self) -> None:
        core_settings = await fetch_core_settings()
        self._status.proxy_scope = str(core_settings.get("proxyScope", "local")).lower()
        self._status.listen_host = "0.0.0.0" if self._status.proxy_scope == "lan" else "127.0.0.1"
        self._status.http_port = int(core_settings.get("proxyPort", 2080))
        self._status.socks_port = int(core_settings.get("socksPort", 2081))

        self._status.local_device_ip = await asyncio.to_thread(self._resolve_local_device_ip)

    async def _emit_status_locked(self) -> None:
        await self._publish_event("status", self._status.as_dict())

    async def _emit_log_locked(self, level: str, message: str, *, source: str = "runtime", trace: str | None = None) -> None:
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
    def _lookup_egress_via_http_proxy(port: int) -> dict[str, object]:
        opener = make_proxy_opener(port)
        payload: dict[str, object] = {
            "ip": None,
            "country": None,
            "latencyMs": None,
            "downloadBps": None,
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
                payload["country"] = data.get("country_name")
                if not payload.get("ip"):
                    payload["ip"] = data.get("ip")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass

        elapsed = max((datetime.now(timezone.utc) - start).total_seconds(), 0.001)
        payload["latencyMs"] = max(1, int(elapsed * 1000))
        payload["downloadBps"] = 0.0
        return payload

    @staticmethod
    def _resolve_local_device_ip() -> str | None:
        candidates: list[str] = []

        try:
            hostname = socket.gethostname()
            for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = str(sockaddr[0])
                if ip and not ip.startswith("127."):
                    candidates.append(ip)
        except Exception:
            pass

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                ip = str(sock.getsockname()[0])
                if ip and not ip.startswith("127."):
                    candidates.append(ip)
        except Exception:
            pass

        for candidate in candidates:
            if candidate:
                return candidate
        return None
