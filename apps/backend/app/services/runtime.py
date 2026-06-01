from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
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
from app.services.curl_socks import curl_available, fetch_egress_via_socks5
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
from app.services.transport.traffic import _traffic
from app.services.system_proxy import enable_system_proxy, get_system_proxy_state, restore_system_proxy_state

CoreRuntime = Literal["sing-box", "tcp-inject"]
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
                await self._apply_system_proxy_locked()
                self._status.state = "running"
                self._status.started_at = datetime.now(timezone.utc)
                self._status.ready = True
                self._last_sample_monotonic = time.monotonic()
                self._start_sampler_locked()
                await self._refresh_egress_info_locked()
                await self._emit_log_locked("info", f"runtime={runtime} started")
            except Exception as exc:  # noqa: BLE001
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
    def _listener_bridge_target(listener: dict[str, object] | None) -> tuple[str, int] | None:
        if listener is None:
            return None
        listen_host = str(listener.get("LISTEN_HOST", "127.0.0.1")).strip() or "127.0.0.1"
        listen_port = int(listener.get("LISTEN_PORT", 40443))
        return listen_host, listen_port

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
            sni=profile.sni,
            alpn=list(profile.alpn),
            allow_insecure=profile.allow_insecure,
            fingerprint=profile.fingerprint,
            reality_public_key=profile.reality_public_key,
            reality_short_id=profile.reality_short_id,
            reality_spider_x=profile.reality_spider_x,
            remark=profile.remark,
            extras=dict(profile.extras),
        )

    async def _spawn_runtime_locked(self) -> None:
        profile_id = self._status.active_profile_id
        if not profile_id:
            raise RuntimeError("No active profile selected")

        record = await fetch_profile_by_id(profile_id)
        if record is None:
            raise RuntimeError("Selected profile does not exist")

        cloudflare_listener = await fetch_selected_cloudflare_listener()
        profile = record_to_profile(record)
        runtime_profile = self._rewrite_profile_for_bridge(profile, cloudflare_listener)
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
        http_port = int(core_settings.get("proxyPort", 2080))
        socks_port = int(core_settings.get("socksPort", 2081))
        listen_host = "0.0.0.0" if str(core_settings.get("proxyScope", "local")).lower() == "lan" else "127.0.0.1"
        self._status.listen_host = listen_host
        self._status.tun_mode = mode == "tun"
        self._status.network_mode = mode
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
            await self._spawn_tcp_inject_locked(profile, core_settings, cloudflare_listener)

    async def _spawn_tcp_inject_locked(self, profile: Profile, core_settings: dict, listener: dict | None) -> None:
        connect_ip = str((listener or {}).get("CONNECT_IP", "")).strip()
        connect_port = int((listener or {}).get("CONNECT_PORT", profile.port))
        fake_sni = str((listener or {}).get("FAKE_SNI", "")).strip()
        if not connect_ip:
            raise RuntimeError("TCP Inject mode requires CONNECT_IP in listener")
        if not fake_sni:
            raise RuntimeError("TCP Inject mode requires FAKE_SNI in listener")

        interface_ipv4 = self._resolve_local_device_ip() or "0.0.0.0"
        # Start the background injector as a transport hook while sing-box
        # remains responsible for outbound protocol handling.
        socks_port = int(core_settings.get("socksPort", 2081))
        http_port = int(core_settings.get("proxyPort", 2080))
        listen_host = "0.0.0.0" if str(core_settings.get("proxyScope", "local")).lower() == "lan" else "127.0.0.1"
        self._status.listen_host = listen_host
        self._status.http_port = http_port
        self._status.socks_port = socks_port
        transport_manager.start_injector(
            interface_ipv4,
            connect_ip,
            connect_port,
            fake_sni.encode(),
            socks_port,
            http_port,
            listen_host,
            True,
        )
        target_label = f"{connect_ip}:{connect_port}"
        await self._emit_log_locked(
            "debug",
            f"tcp-inject interface={interface_ipv4} target={target_label} listen={listen_host}:{http_port}/{socks_port}",
        )
        await self._emit_log_locked(
            "info",
            f"tcp-inject self-check hint: GET /api/core/self-check (target={target_label})",
        )

        # Runtime readiness is managed by sing-box; injector runs in parallel.
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
        transport_manager.stop_injector()

        if self._sampler_task is not None:
            self._sampler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sampler_task
            self._sampler_task = None

        await self._cleanup_instance_locked()
        await self._restore_system_proxy_locked()

    async def _apply_system_proxy_locked(self) -> None:
        core_settings = await fetch_core_settings()
        routing = await fetch_routing_config()
        bypass = self._system_proxy_bypass(routing)
        try:
            if self._saved_system_proxy_state is None:
                self._saved_system_proxy_state = await asyncio.to_thread(get_system_proxy_state)
            await asyncio.to_thread(enable_system_proxy, "127.0.0.1", int(core_settings.get("proxyPort", 2080)), bypass)
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

                    down, up = _traffic.consume()

                    self._status.download_bps = down / elapsed
                    self._status.upload_bps = up / elapsed
                    self._status.session_download_bytes += down
                    self._status.session_upload_bytes += up

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
                down, up = await asyncio.to_thread(self._read_singbox_traffic_delta)
                await self._refresh_egress_info_locked()
                if down is not None and up is not None and elapsed > 0:
                    self._status.download_bps = down / elapsed
                    self._status.upload_bps = up / elapsed
                    self._status.session_download_bytes += down
                    self._status.session_upload_bytes += up
                else:
                    self._status.session_download_bytes += self._status.download_bps * elapsed
                    self._status.session_upload_bytes += self._status.upload_bps * elapsed
                await self._emit_status_locked()

    async def _refresh_egress_info_locked(self) -> None:
        if self._status.state != "running":
            return

        socks_port = None
        if self._status.runtime == "tcp-inject":
            socks_port = transport_manager.get_active_socks_port()
        elif self._instance is not None:
            socks_port = self._instance.socks_port

        if socks_port and curl_available():
            try:
                payload = await asyncio.to_thread(fetch_egress_via_socks5, "127.0.0.1", socks_port)
            except Exception as exc:  # noqa: BLE001
                await self._emit_log_locked(
                    "warning",
                    f"egress lookup via socks5 failed: {exc}",
                    trace=traceback.format_exc(),
                )
            else:
                self._status.egress_ip = payload.ip or self._status.egress_ip
                self._status.egress_country = payload.country or self._status.egress_country
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
        if "<local>" not in {item.lower() for item in domains}:
            domains.append("<local>")
        return ";".join(domains)

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

    @staticmethod
    def _has_admin_privileges() -> bool:
        if os.name != "nt":
            return True
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
