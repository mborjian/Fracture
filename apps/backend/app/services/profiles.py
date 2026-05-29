from __future__ import annotations

from dataclasses import dataclass

from app.db.database import (
    clear_profiles_without_successful_ping,
    delete_profiles,
    fetch_profile_link,
    fetch_profile_by_id,
    fetch_profiles,
    get_active_profile_id,
    profile_exists,
    rename_profile,
    reorder_profiles,
    set_active_profile_id,
    sort_profiles_by_ping,
    sort_profiles_by_speed,
    upsert_profiles,
)
from app.services.profile_import import parse_profiles_from_text


@dataclass(frozen=True)
class ProfileSelectionResult:
    ok: bool
    active_profile_id: str | None


async def list_profiles() -> list[dict]:
    return await fetch_profiles()


async def get_profile(profile_id: str) -> dict | None:
    return await fetch_profile_by_id(profile_id)


async def get_active_profile() -> str | None:
    return await get_active_profile_id()


async def select_active_profile(profile_id: str) -> ProfileSelectionResult:
    exists = await profile_exists(profile_id)
    if not exists:
        return ProfileSelectionResult(ok=False, active_profile_id=await get_active_profile_id())

    await set_active_profile_id(profile_id)
    return ProfileSelectionResult(ok=True, active_profile_id=profile_id)


async def import_profiles_from_text(raw_text: str) -> dict:
    parsed = parse_profiles_from_text(raw_text)
    upsert_result = await upsert_profiles(parsed.profiles)
    return {
        "imported": len(parsed.profiles),
        "created": upsert_result["created"],
        "updated": upsert_result["updated"],
        "errors": parsed.errors,
    }


async def rename_profile_by_id(profile_id: str, name: str) -> bool:
    return await rename_profile(profile_id, name)


async def export_profile_by_id(profile_id: str) -> str | None:
    return await fetch_profile_link(profile_id)


async def reorder_profiles_by_id(profile_ids: list[str]) -> int:
    return await reorder_profiles(profile_ids)


async def sort_profiles_by_ping_result() -> int:
    return await sort_profiles_by_ping()


async def sort_profiles_by_speed_result() -> int:
    return await sort_profiles_by_speed()


async def delete_profiles_by_id(profile_ids: list[str]) -> int:
    return await delete_profiles(profile_ids)


async def delete_failed_profiles() -> int:
    return await clear_profiles_without_successful_ping()
