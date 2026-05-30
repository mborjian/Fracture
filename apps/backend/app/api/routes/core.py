from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db.database import fetch_core_settings
from app.services.profiles import list_profiles
from app.services.runtime import CoreRuntimeService

router = APIRouter(prefix="/api/core", tags=["core"])


class StartPayload(BaseModel):
    profile_id: str | None = None


def _runtime_service(request: Request) -> CoreRuntimeService:
    return request.app.state.runtime_service


@router.get("/status")
async def core_status(request: Request) -> dict:
    runtime_service = _runtime_service(request)
    return await runtime_service.get_status()


@router.post("/start")
async def core_start(payload: StartPayload, request: Request) -> dict:
    runtime_service = _runtime_service(request)

    profile_id = payload.profile_id
    if profile_id is None:
        status = await runtime_service.get_status()
        profile_id = status.get("activeProfileId")

    if profile_id is None:
        profiles = await list_profiles()
        if not profiles:
            raise HTTPException(status_code=400, detail="No profiles available")
        profile_id = profiles[0]["id"]

    # Get transport mode from core settings
    core_settings = await fetch_core_settings()
    runtime = "tcp-inject" if core_settings.get("transportMode") == "tcp-inject" else "sing-box"

    status = await runtime_service.start(runtime, profile_id)
    if status.get("state") != "running" or not status.get("ready", False):
        raise HTTPException(status_code=500, detail="Failed to start runtime")
    return status


@router.post("/stop")
async def core_stop(request: Request) -> dict:
    runtime_service = _runtime_service(request)
    return await runtime_service.stop()


@router.post("/restart")
async def core_restart(request: Request) -> dict:
    runtime_service = _runtime_service(request)
    return await runtime_service.restart(reason="manual")


@router.post("/egress/refresh")
async def core_refresh_egress(request: Request) -> dict:
    runtime_service = _runtime_service(request)
    return await runtime_service.refresh_egress()


@router.get("/health")
async def core_health(request: Request) -> dict:
    runtime_service = _runtime_service(request)
    try:
        status = await runtime_service.get_status()
        return {
            "ok": True,
            "runtimeState": status.get("state"),
            "ready": status.get("ready", False),
            "lastError": status.get("lastError"),
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
