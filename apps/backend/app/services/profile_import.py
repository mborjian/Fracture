from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.singbox import parse_profile, profile_to_record


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

    for token in _extract_candidate_links(raw_text):
        try:
            profile = _try_parse_link(token)
            if not profile["server"]:
                raise ValueError("missing server")
            profiles.append(profile)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{token[:60]} -> {exc}")

    return ImportResult(profiles=profiles, errors=errors)
