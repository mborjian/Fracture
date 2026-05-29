from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.ping import ProfilePingService
from app.services.profiles import (
    delete_failed_profiles,
    delete_profiles_by_id,
    export_profile_by_id,
    get_active_profile,
    import_profiles_from_text,
    list_profiles,
    rename_profile_by_id,
    reorder_profiles_by_id,
    select_active_profile,
    sort_profiles_by_ping_result,
    sort_profiles_by_speed_result,
)
from app.services.runtime import CoreRuntimeService

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ActiveProfilePayload(BaseModel):
    profile_id: str


class ImportPayload(BaseModel):
    text: str = Field(default="")


class RenamePayload(BaseModel):
    name: str


class PingOnePayload(BaseModel):
    timeout_ms: int = Field(default=8000, ge=500, le=30000)
    mode: str = Field(default="quick", pattern="^(quick|full)$")


class PingAllPayload(BaseModel):
    profile_ids: list[str] | None = None
    timeout_ms: int = Field(default=8000, ge=500, le=30000)
    mode: str = Field(default="quick", pattern="^(quick|full)$")


class ReorderProfilesPayload(BaseModel):
    profile_ids: list[str]


def _runtime_service(request: Request) -> CoreRuntimeService:
    return request.app.state.runtime_service


def _ping_service(request: Request) -> ProfilePingService:
    return request.app.state.ping_service


@router.get("")
async def profiles_list() -> list[dict]:
    return await list_profiles()


@router.get("/active")
async def profiles_active() -> dict:
    return {"activeProfileId": await get_active_profile()}


@router.post("/active")
async def profiles_set_active(payload: ActiveProfilePayload, request: Request) -> dict:
    result = await select_active_profile(payload.profile_id)
    if not result.ok:
        raise HTTPException(status_code=404, detail="Profile not found")

    runtime = _runtime_service(request)
    await runtime.set_active_profile(result.active_profile_id)
    await runtime.on_profile_or_settings_changed("active profile changed")
    return {"ok": True, "activeProfileId": result.active_profile_id}


@router.post("/import")
async def profiles_import(payload: ImportPayload) -> dict:
    result = await import_profiles_from_text(payload.text)
    return {
        "ok": True,
        **result,
    }


@router.post("/{profile_id}/rename")
async def profiles_rename(profile_id: str, payload: RenamePayload) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Profile name cannot be empty")
    changed = await rename_profile_by_id(profile_id, name)
    if not changed:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"ok": True}


@router.get("/{profile_id}/export")
async def profiles_export(profile_id: str) -> dict:
    link = await export_profile_by_id(profile_id)
    if not link:
        raise HTTPException(status_code=404, detail="Profile export link not found")
    return {"link": link}


@router.delete("/{profile_id}")
async def profiles_delete(profile_id: str, request: Request) -> dict:
    removed = await delete_profiles_by_id([profile_id])
    if removed == 0:
        raise HTTPException(status_code=404, detail="Profile not found")

    await _runtime_service(request).on_profile_or_settings_changed("profile deleted")
    return {"ok": True, "removed": removed}


@router.post("/cleanup/failed")
async def profiles_cleanup_failed(request: Request) -> dict:
    removed = await delete_failed_profiles()
    await _runtime_service(request).on_profile_or_settings_changed("failed profiles cleaned")
    return {"ok": True, "removed": removed}


@router.post("/{profile_id}/ping")
async def profiles_ping_one(profile_id: str, payload: PingOnePayload, request: Request) -> dict:
    try:
        return await _ping_service(request).ping_profile(
            profile_id,
            timeout_s=payload.timeout_ms / 1000,
            probe_mode=payload.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{profile_id}/speed")
async def profiles_speed_one(profile_id: str, payload: PingOnePayload, request: Request) -> dict:
    try:
        return await _ping_service(request).speed_profile(
            profile_id,
            timeout_s=payload.timeout_ms / 1000,
            probe_mode=payload.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/ping-all")
async def profiles_ping_all(payload: PingAllPayload, request: Request) -> dict:
    if payload.profile_ids:
        ids = payload.profile_ids
    else:
        ids = [profile["id"] for profile in await list_profiles()]

    try:
        return await _ping_service(request).ping_all(
            ids,
            timeout_s=payload.timeout_ms / 1000,
            probe_mode=payload.mode,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/speed-all")
async def profiles_speed_all(payload: PingAllPayload, request: Request) -> dict:
    if payload.profile_ids:
        ids = payload.profile_ids
    else:
        ids = [profile["id"] for profile in await list_profiles()]

    try:
        return await _ping_service(request).speed_all(
            ids,
            timeout_s=payload.timeout_ms / 1000,
            probe_mode=payload.mode,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/ping-all/cancel")
async def profiles_ping_cancel(request: Request) -> dict:
    return await _ping_service(request).cancel_ping_all()


@router.post("/order")
async def profiles_reorder(payload: ReorderProfilesPayload) -> dict:
    reordered = await reorder_profiles_by_id(payload.profile_ids)
    return {"ok": True, "reordered": reordered}


@router.post("/sort-by-ping")
async def profiles_sort_by_ping() -> dict:
    reordered = await sort_profiles_by_ping_result()
    return {"ok": True, "reordered": reordered}


@router.post("/sort-by-speed")
async def profiles_sort_by_speed() -> dict:
    reordered = await sort_profiles_by_speed_result()
    return {"ok": True, "reordered": reordered}
