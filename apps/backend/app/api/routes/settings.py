from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.db.database import (
    fetch_app_settings,
    fetch_cloudflare_config,
    fetch_core_settings,
    fetch_routing_config,
    save_app_settings,
    save_cloudflare_config,
    save_routing_config,
)
from app.services.runtime import CoreRuntimeService

router = APIRouter(prefix="/api/settings", tags=["settings"])


class CloudflareListenerPayload(BaseModel):
    id: str
    LISTEN_HOST: str = Field(default="0.0.0.0")
    LISTEN_PORT: int = Field(default=40443, ge=1, le=65535)
    CONNECT_IP: str = Field(default="")
    CONNECT_PORT: int = Field(default=443, ge=1, le=65535)
    FAKE_SNI: str = Field(default="")


class CloudflareConfigPayload(BaseModel):
    selectedId: str
    listeners: list[CloudflareListenerPayload] = Field(default_factory=list)


class RoutingPayload(BaseModel):
    dnsServers: str = Field(default="1.1.1.1,8.8.8.8")
    dohUrl: str = Field(default="https://dns.google/dns-query")
    fakeIpCidr: str = Field(default="198.18.0.0/15")
    bypassDomains: str = Field(default="*.lan,*.local,*.msftconnecttest.com")
    routingRules: str = Field(default="geoip:private -> direct\ngeosite:ads -> block")
    tunMode: bool = False
    tunReason: str = Field(default="TUN mode uses sing-box and may require Administrator privileges on Windows.")
    outboundMode: str = Field(default="proxy")


class CoreSettingsPayload(BaseModel):
    proxyScope: str = Field(default="local")
    proxyPort: int = Field(default=2080, ge=1, le=65535)
    socksPort: int = Field(default=2081, ge=1, le=65535)
    autoReconnect: bool = True
    transportMode: str = Field(default="singbox")  # new


class UiSettingsPayload(BaseModel):
    theme: str = Field(default="system")
    updateChannel: str = Field(default="stable")
    runOnStartup: bool = False
    closeToTray: bool = True


def _runtime_service(request: Request) -> CoreRuntimeService:
    return request.app.state.runtime_service


@router.get("/cloudflare")
async def get_cloudflare_config() -> dict:
    return await fetch_cloudflare_config()


@router.post("/cloudflare")
async def update_cloudflare_config(payload: CloudflareConfigPayload, request: Request) -> dict:
    saved = await save_cloudflare_config(payload.model_dump())
    await _runtime_service(request).on_profile_or_settings_changed("cloudflare settings changed")
    return saved


@router.get("/routing")
async def get_routing_settings() -> dict:
    return await fetch_routing_config()


@router.post("/routing")
async def update_routing_settings(payload: RoutingPayload, request: Request) -> dict:
    saved = await save_routing_config(payload.model_dump())
    await _runtime_service(request).on_profile_or_settings_changed("routing settings changed")
    return saved


@router.get("/core")
async def get_core_settings() -> dict:
    return await fetch_core_settings()


@router.post("/core")
async def update_core_settings(payload: CoreSettingsPayload, request: Request) -> dict:
    app_settings = await save_app_settings({"core": payload.model_dump()})
    await _runtime_service(request).on_profile_or_settings_changed("core settings changed")
    return app_settings.get("core", {})


@router.get("/ui")
async def get_ui_settings() -> dict:
    payload = await fetch_app_settings()
    return payload.get("ui", {})


@router.post("/ui")
async def update_ui_settings(payload: UiSettingsPayload) -> dict:
    app_settings = await save_app_settings({"ui": payload.model_dump()})
    return app_settings.get("ui", {})


@router.get("/tunnel-support")
async def tunnel_support() -> dict:
    return {
        "supported": True,
        "reason": "TUN mode is provided by sing-box. Run Fracture with Administrator privileges on Windows when needed.",
    }
