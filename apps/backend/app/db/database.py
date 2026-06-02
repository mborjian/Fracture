from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.config import settings

_DATA_LOCK = asyncio.Lock()

_DEFAULT_PROFILES_DOC: dict[str, Any] = {
    "activeProfileId": None,
    "profiles": [],
}

_DEFAULT_CLOUDFLARE_DOC: dict[str, Any] = {
    "selectedId": "listener-default",
    "listeners": [
        {
            "id": "listener-default",
            "CONNECT_IP": "",
            "FAKE_SNI": "",
        }
    ],
}

_DEFAULT_APP_SETTINGS_DOC: dict[str, Any] = {
    "routing": {
        "dnsServers": "1.1.1.1,8.8.8.8",
        "dohUrl": "https://dns.google/dns-query",
        "fakeIpCidr": "198.18.0.0/15",
        "bypassDomains": "*.lan,*.local,*.msftconnecttest.com",
        "routingRules": "geoip:private -> direct\ngeosite:ads -> block",
        "tunMode": True,
        "tunReason": "TUN mode uses sing-box and may require Administrator privileges on Windows.",
        "outboundMode": "tun",
    },
    "core": {
        "proxyScope": "local",
        "proxyPort": 2080,
        "socksPort": 2081,
        "autoReconnect": True,
        "transportMode": "tcp-inject",  # "singbox" or "tcp-inject"
    },
    "ui": {
        "theme": "system",
        "updateChannel": "stable",
        "runOnStartup": False,
        "closeToTray": True,
        "showDevelopmentLogs": False,
    },
}


def _deep_clone(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return _deep_clone(fallback)

    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return parsed
    return _deep_clone(fallback)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


async def _load_profiles_doc() -> dict[str, Any]:
    return _read_json(settings.profiles_path, _DEFAULT_PROFILES_DOC)


async def _save_profiles_doc(payload: dict[str, Any]) -> None:
    _write_json(settings.profiles_path, payload)


def _cleanup_legacy_profiles_doc(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False

    if "subscriptions" in payload:
        payload.pop("subscriptions", None)
        changed = True

    profiles = payload.get("profiles")
    if isinstance(profiles, list):
        for profile in profiles:
            if isinstance(profile, dict) and "favorite" in profile:
                profile.pop("favorite", None)
                changed = True

    profile_list = [item for item in payload.get("profiles", []) if isinstance(item, dict) and item.get("id")]
    active_id = payload.get("activeProfileId")
    if profile_list:
        ids = {str(item.get("id")) for item in profile_list}
        if not active_id or str(active_id) not in ids:
            payload["activeProfileId"] = str(profile_list[0].get("id"))
            changed = True

    return payload, changed


def _normalize_cloudflare_doc(payload: dict[str, Any]) -> dict[str, Any]:
    listeners = payload.get("listeners")
    selected_id = str(payload.get("selectedId", "")).strip()

    if isinstance(listeners, list) and listeners:
        normalized_listeners = []
        for index, item in enumerate(listeners):
            if not isinstance(item, dict):
                continue
            listener_id = str(item.get("id", f"listener-{index + 1}")).strip() or f"listener-{index + 1}"
            normalized_listeners.append(
                {
                    "id": listener_id,
                    "CONNECT_IP": str(item.get("CONNECT_IP", "")),
                    "FAKE_SNI": str(item.get("FAKE_SNI", "")),
                }
            )
        if not normalized_listeners:
            normalized_listeners = _DEFAULT_CLOUDFLARE_DOC["listeners"]
        ids = {item["id"] for item in normalized_listeners}
        if not selected_id or selected_id not in ids:
            selected_id = normalized_listeners[0]["id"]
        return {
            "selectedId": selected_id,
            "listeners": normalized_listeners,
        }

    legacy = {
        "id": "listener-default",
        "CONNECT_IP": str(payload.get("CONNECT_IP", "")),
        "FAKE_SNI": str(payload.get("FAKE_SNI", "")),
    }
    return {
        "selectedId": "listener-default",
        "listeners": [legacy],
    }


async def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.configs_dir.mkdir(parents=True, exist_ok=True)
    settings.singbox_dir.mkdir(parents=True, exist_ok=True)
    settings.binaries_dir.mkdir(parents=True, exist_ok=True)
    if not settings.profiles_path.exists():
        _write_json(settings.profiles_path, _DEFAULT_PROFILES_DOC)
    else:
        async with _DATA_LOCK:
            current = await _load_profiles_doc()
            cleaned, changed = _cleanup_legacy_profiles_doc(current)
            if changed:
                await _save_profiles_doc(cleaned)
    if not settings.cloudflare_config_path.exists():
        _write_json(settings.cloudflare_config_path, _DEFAULT_CLOUDFLARE_DOC)
    else:
        async with _DATA_LOCK:
            cloudflare = _read_json(settings.cloudflare_config_path, _DEFAULT_CLOUDFLARE_DOC)
            _write_json(settings.cloudflare_config_path, _normalize_cloudflare_doc(cloudflare))
    if not settings.app_settings_path.exists():
        _write_json(settings.app_settings_path, _DEFAULT_APP_SETTINGS_DOC)


def _normalized_profile(item: dict[str, Any]) -> dict[str, Any]:
    protocol = str(item.get("protocol", "vless")).lower()
    if protocol in {"ss", "shadowsocks"}:
        protocol = "shadowsocks"

    return {
        "id": str(item.get("id", "")),
        "name": str(item.get("name", "Unnamed")),
        "protocol": protocol,
        "server": str(item.get("server", "")),
        "port": int(item.get("port", 443)),
        "group": str(item.get("group", "default")),
        "link": str(item.get("link", "")),
        "params": item.get("params", {}) if isinstance(item.get("params"), dict) else {},
        "lastPingMs": int(item.get("lastPingMs")) if item.get("lastPingMs") is not None else None,
        "lastPingAt": item.get("lastPingAt"),
        "lastSpeedMbps": float(item.get("lastSpeedMbps")) if item.get("lastSpeedMbps") is not None else None,
        "lastSpeedAt": item.get("lastSpeedAt"),
        "pingSuccessCount": int(item.get("pingSuccessCount", 0)),
        "pingFailCount": int(item.get("pingFailCount", 0)),
    }


def _profile_link_key(profile: dict[str, Any]) -> str:
    return str(profile.get("link", "")).strip()


def _profile_identity(profile: dict[str, Any]) -> str:
    params = profile.get("params", {})
    if not isinstance(params, dict):
        return ""
    for key in ("uuid_or_password", "id", "password", "uuid"):
        value = str(params.get(key, "")).strip()
        if value:
            return value
    return ""


def _profile_fingerprint(profile: dict[str, Any]) -> str:
    params = profile.get("params", {})
    if not isinstance(params, dict):
        params = {}
    extras = params.get("extras", {})
    if not isinstance(extras, dict):
        extras = {}

    fields = [
        str(profile.get("protocol", "")).lower(),
        str(profile.get("server", "")).lower(),
        str(int(profile.get("port", 0) or 0)),
        _profile_identity(profile),
        str(params.get("username", "")),
        str(params.get("network", "")),
        str(params.get("sni", "")),
        str(params.get("tls", "")),
        str(extras.get("host", "")),
        str(extras.get("path", "")),
        str(extras.get("service_name", "")),
        str(extras.get("plugin", "")),
        str(extras.get("mode", "")),
    ]
    return "|".join(fields)


async def fetch_profiles() -> list[dict]:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        cleaned, changed = _cleanup_legacy_profiles_doc(data)
        if changed:
            await _save_profiles_doc(cleaned)
            data = cleaned
        profiles = [_normalized_profile(item) for item in data.get("profiles", []) if item.get("id")]
        return profiles


async def fetch_profile_by_id(profile_id: str) -> dict | None:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        for item in data.get("profiles", []):
            if str(item.get("id")) == profile_id:
                return _normalized_profile(item)
    return None


async def upsert_profiles(imported_profiles: list[dict[str, Any]]) -> dict[str, int]:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        existing = [item for item in data.get("profiles", []) if isinstance(item, dict)]

        by_id = {str(item.get("id")): item for item in existing if item.get("id")}
        by_link = {_profile_link_key(item): item for item in existing if _profile_link_key(item)}
        by_fingerprint = {_profile_fingerprint(item): item for item in existing}

        created = 0
        updated = 0
        for profile in imported_profiles:
            normalized = _normalized_profile(profile)
            if not normalized["id"]:
                continue
            link = _profile_link_key(normalized)
            fp = _profile_fingerprint(normalized)

            if normalized["id"] in by_id:
                by_id[normalized["id"]].update(normalized)
                updated += 1
                continue

            if link and link in by_link:
                current = by_link[link]
                normalized["id"] = str(current.get("id", normalized["id"]))
                current.update(normalized)
                updated += 1
                continue

            if fp in by_fingerprint:
                current = by_fingerprint[fp]
                normalized["id"] = str(current.get("id", normalized["id"]))
                current.update(normalized)
                updated += 1
                continue

            existing.append(normalized)
            by_id[normalized["id"]] = normalized
            if link:
                by_link[link] = normalized
            by_fingerprint[fp] = normalized
            created += 1

        data["profiles"] = existing
        if existing and (not data.get("activeProfileId") or all(
                str(item.get("id")) != str(data.get("activeProfileId")) for item in existing)):
            data["activeProfileId"] = str(existing[0].get("id"))
        await _save_profiles_doc(data)

    return {"created": created, "updated": updated}


async def rename_profile(profile_id: str, name: str) -> bool:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        for profile in data.get("profiles", []):
            if str(profile.get("id")) != profile_id:
                continue
            profile["name"] = name
            await _save_profiles_doc(data)
            return True
        return False


async def fetch_profile_link(profile_id: str) -> str | None:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        for profile in data.get("profiles", []):
            if str(profile.get("id")) == profile_id:
                link = str(profile.get("link", "")).strip()
                return link or None
        return None


async def delete_profiles(profile_ids: list[str]) -> int:
    target_ids = {item for item in profile_ids if item}
    if not target_ids:
        return 0

    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        active_id = str(data.get("activeProfileId") or "")
        if active_id and active_id in target_ids:
            return 0
        old_profiles = data.get("profiles", [])
        new_profiles = [item for item in old_profiles if str(item.get("id")) not in target_ids]
        removed = len(old_profiles) - len(new_profiles)
        data["profiles"] = new_profiles

        if removed > 0:
            await _save_profiles_doc(data)
        return removed


async def clear_profiles_without_successful_ping() -> int:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        old_profiles = data.get("profiles", [])
        new_profiles = [item for item in old_profiles if int(item.get("pingSuccessCount", 0)) > 0]
        removed = len(old_profiles) - len(new_profiles)
        data["profiles"] = new_profiles
        if str(data.get("activeProfileId")) and all(
                str(p.get("id")) != str(data.get("activeProfileId")) for p in new_profiles):
            data["activeProfileId"] = new_profiles[0].get("id") if new_profiles else None

        if removed > 0:
            await _save_profiles_doc(data)
        return removed


async def get_active_profile_id() -> str | None:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        cleaned, changed = _cleanup_legacy_profiles_doc(data)
        if changed:
            await _save_profiles_doc(cleaned)
            data = cleaned
        active = data.get("activeProfileId")
        if active is None:
            return None
        return str(active)


async def set_active_profile_id(profile_id: str | None) -> None:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        if profile_id is None:
            profiles = [item for item in data.get("profiles", []) if isinstance(item, dict) and item.get("id")]
            data["activeProfileId"] = str(profiles[0].get("id")) if profiles else None
        else:
            data["activeProfileId"] = profile_id
        await _save_profiles_doc(data)


async def profile_exists(profile_id: str) -> bool:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        profiles = data.get("profiles", [])
        return any(str(item.get("id")) == profile_id for item in profiles)


async def reorder_profiles(profile_ids: list[str]) -> int:
    clean_ids = [pid for pid in profile_ids if pid]
    if not clean_ids:
        return 0

    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        profiles = [item for item in data.get("profiles", []) if isinstance(item, dict)]
        by_id = {str(item.get("id")): item for item in profiles if item.get("id")}
        ordered_ids = []
        seen: set[str] = set()

        for pid in clean_ids:
            if pid in by_id and pid not in seen:
                ordered_ids.append(pid)
                seen.add(pid)

        for item in profiles:
            pid = str(item.get("id", ""))
            if pid and pid not in seen:
                ordered_ids.append(pid)
                seen.add(pid)

        reordered = [by_id[pid] for pid in ordered_ids if pid in by_id]
        old_ids = [str(item.get("id", "")) for item in profiles]
        new_ids = [str(item.get("id", "")) for item in reordered]
        if old_ids == new_ids:
            return 0

        data["profiles"] = reordered
        await _save_profiles_doc(data)
        return len(reordered)


async def sort_profiles_by_ping() -> int:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        profiles = [item for item in data.get("profiles", []) if isinstance(item, dict)]
        indexed = list(enumerate(profiles))

        indexed.sort(
            key=lambda pair: (
                pair[1].get("lastPingMs") is None,
                int(pair[1].get("lastPingMs") or 10 ** 9),
                pair[0],
            )
        )

        sorted_profiles = [item for _, item in indexed]
        old_ids = [str(item.get("id", "")) for item in profiles]
        new_ids = [str(item.get("id", "")) for item in sorted_profiles]
        if old_ids == new_ids:
            return 0

        data["profiles"] = sorted_profiles
        await _save_profiles_doc(data)
        return len(sorted_profiles)


async def sort_profiles_by_speed() -> int:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        profiles = [item for item in data.get("profiles", []) if isinstance(item, dict)]
        indexed = list(enumerate(profiles))

        indexed.sort(
            key=lambda pair: (
                pair[1].get("lastSpeedMbps") is None,
                -float(pair[1].get("lastSpeedMbps") or 0),
                pair[0],
            )
        )

        sorted_profiles = [item for _, item in indexed]
        old_ids = [str(item.get("id", "")) for item in profiles]
        new_ids = [str(item.get("id", "")) for item in sorted_profiles]
        if old_ids == new_ids:
            return 0

        data["profiles"] = sorted_profiles
        await _save_profiles_doc(data)
        return len(sorted_profiles)


async def save_profile_ping_result(profile_id: str, latency_ms: int | None, when_iso: str, success: bool) -> bool:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        updated = False
        for profile in data.get("profiles", []):
            if str(profile.get("id")) == profile_id:
                profile["lastPingMs"] = latency_ms
                profile["lastPingAt"] = when_iso
                if success:
                    profile["pingSuccessCount"] = int(profile.get("pingSuccessCount", 0)) + 1
                else:
                    profile["pingFailCount"] = int(profile.get("pingFailCount", 0)) + 1
                updated = True
                break
        if updated:
            await _save_profiles_doc(data)
        return updated


async def save_profile_speed_result(profile_id: str, speed_mbps: float | None, when_iso: str) -> bool:
    async with _DATA_LOCK:
        data = await _load_profiles_doc()
        updated = False
        for profile in data.get("profiles", []):
            if str(profile.get("id")) == profile_id:
                profile["lastSpeedMbps"] = speed_mbps
                profile["lastSpeedAt"] = when_iso
                updated = True
                break
        if updated:
            await _save_profiles_doc(data)
        return updated


async def fetch_cloudflare_config() -> dict:
    async with _DATA_LOCK:
        payload = _normalize_cloudflare_doc(_read_json(settings.cloudflare_config_path, _DEFAULT_CLOUDFLARE_DOC))
        selected_id = payload["selectedId"]
        selected = next((item for item in payload["listeners"] if item["id"] == selected_id), payload["listeners"][0])
        return {
            "selectedId": selected_id,
            "selected": selected,
            "listeners": payload["listeners"],
        }


async def fetch_selected_cloudflare_listener() -> dict | None:
    payload = await fetch_cloudflare_config()
    selected = payload.get("selected")
    return selected if isinstance(selected, dict) else None


async def save_cloudflare_config(payload: dict) -> dict:
    async with _DATA_LOCK:
        doc = _normalize_cloudflare_doc(payload)
        _write_json(settings.cloudflare_config_path, doc)
        selected = next((item for item in doc["listeners"] if item["id"] == doc["selectedId"]), doc["listeners"][0])
        return {
            "selectedId": doc["selectedId"],
            "selected": selected,
            "listeners": doc["listeners"],
        }


async def fetch_app_settings() -> dict:
    async with _DATA_LOCK:
        payload = _read_json(settings.app_settings_path, _DEFAULT_APP_SETTINGS_DOC)
        merged = _deep_clone(_DEFAULT_APP_SETTINGS_DOC)
        for key in merged:
            value = payload.get(key)
            if isinstance(value, dict):
                merged[key].update(value)
        return merged


async def save_app_settings(payload: dict[str, Any]) -> dict:
    async with _DATA_LOCK:
        current = _read_json(settings.app_settings_path, _DEFAULT_APP_SETTINGS_DOC)
        for key, value in payload.items():
            if isinstance(value, dict):
                existing = current.get(key)
                if not isinstance(existing, dict):
                    existing = {}
                existing.update(value)
                current[key] = existing
            else:
                current[key] = value
        _write_json(settings.app_settings_path, current)

    return await fetch_app_settings()


async def fetch_routing_config() -> dict:
    payload = await fetch_app_settings()
    return payload.get("routing", {})


async def save_routing_config(payload: dict[str, Any]) -> dict:
    data = await save_app_settings({"routing": payload})
    return data.get("routing", {})


async def fetch_core_settings() -> dict:
    payload = await fetch_app_settings()
    return payload.get("core", {})
