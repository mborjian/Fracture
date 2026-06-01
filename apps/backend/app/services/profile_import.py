from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json

from app.services.singbox import parse_profile, profile_to_record, record_to_profile


@dataclass
class ImportResult:
    profiles: list[dict[str, Any]]
    errors: list[str]


def _decode_base64(value: str) -> str:
    # Reuse the sing-box import surface and only keep the nested subscription convenience here.
    import base64

    cleaned = value.strip().replace("-", "+").replace("_", "/")
    cleaned += "=" * ((4 - (len(cleaned) % 4)) % 4)
    return base64.b64decode(cleaned.encode("utf-8")).decode("utf-8", errors="ignore")


def _try_parse_link(link: str) -> dict[str, Any]:
    profile = parse_profile(link)
    return profile_to_record(profile, link)


def _try_parse_json_profile(payload: dict[str, Any]) -> dict[str, Any]:
    profile = record_to_profile(payload)
    link = str(payload.get("link", "")).strip()
    record = profile_to_record(profile, link)
    if payload.get("id"):
        record["id"] = str(payload["id"])
    if payload.get("name"):
        record["name"] = str(payload["name"])
    if payload.get("group"):
        record["group"] = str(payload["group"])
    return record


def _extract_json_profiles(raw_text: str) -> list[dict[str, Any]]:
    text = raw_text.strip()
    if not text or not text.startswith(("{", "[")):
        return []
    parsed = json.loads(text)
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if not isinstance(parsed, dict):
        return []
    if isinstance(parsed.get("profiles"), list):
        return [item for item in parsed["profiles"] if isinstance(item, dict)]
    if parsed.get("protocol") or parsed.get("params") or parsed.get("link"):
        return [parsed]
    return []


def _extract_candidate_links(raw_text: str) -> list[str]:
    text = raw_text.strip()
    if not text:
        return []

    normalized = text.replace("\r", "\n")
    tokens = [piece.strip() for piece in normalized.split("\n") if piece.strip()]
    if len(tokens) == 1 and "://" not in tokens[0]:
        # Some bulk payloads are base64 encoded, each line inside may be a link.
        try:
            decoded = _decode_base64(tokens[0])
            nested = [piece.strip() for piece in decoded.replace("\r", "\n").split("\n") if piece.strip()]
            if nested:
                tokens = nested
        except Exception:
            pass

    if len(tokens) == 1 and "://" in tokens[0]:
        import re

        matches = re.findall(r"(?:vmess|vless|trojan|ss|socks|socks5|socks4|http|https|hysteria2|hy2|tuic|wireguard|naive\+https|naive\+quic|anytls)://\S+", tokens[0])
        if matches:
            tokens = [match.strip() for match in matches if match.strip()]

    return tokens


def parse_profiles_from_text(raw_text: str) -> ImportResult:
    profiles: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        json_profiles = _extract_json_profiles(raw_text)
    except Exception as exc:  # noqa: BLE001
        json_profiles = []
        if raw_text.strip().startswith(("{", "[")):
            errors.append(f"json import -> {exc}")

    for item in json_profiles:
        try:
            profile = _try_parse_json_profile(item)
            if not profile["server"]:
                raise ValueError("missing server")
            profiles.append(profile)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"json profile -> {exc}")

    if json_profiles:
        return ImportResult(profiles=profiles, errors=errors)

    for token in _extract_candidate_links(raw_text):
        try:
            profile = _try_parse_link(token)
            if not profile["server"]:
                raise ValueError("missing server")
            profiles.append(profile)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{token[:60]} -> {exc}")

    return ImportResult(profiles=profiles, errors=errors)
