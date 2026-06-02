from __future__ import annotations

import json
import math
from collections.abc import Iterable
import shutil
import subprocess
import time
from dataclasses import dataclass

import requests


SOCKS_PROBE_URLS = [
    "http://cp.cloudflare.com/generate_204",
    "http://connectivitycheck.gstatic.com/generate_204",
    "http://detectportal.firefox.com/success.txt",
    "http://www.apple.com/library/test/success.html",
]

SPEED_TEST_URLS = [
    "https://speed.cloudflare.com/__down?bytes=1048576",
    "https://cachefly.cachefly.net/1mb.test",
    "https://speed.hetzner.de/1MB.bin",
]


@dataclass(frozen=True)
class EgressResult:
    ip: str
    country: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


def curl_available() -> bool:
    return shutil.which("curl") is not None


def socks5_endpoint(host: str, port: int) -> str:
    target = host.strip()
    if ":" in target and target.count(".") != 3:
        return f"[{target}]:{port}"
    return f"{target}:{port}"


def fetch_egress_via_socks5(proxy_host: str, proxy_port: int, timeout_s: float = 8.0) -> EgressResult:
    socks = socks5_endpoint(proxy_host, proxy_port)
    try:
        payload = _run_curl_json(
            [
                "-sS",
                "--max-time",
                str(max(1, int(round(timeout_s)))),
                "--socks5-hostname",
                socks,
                "https://ipinfo.io/json",
            ]
        )
        ip = str(payload.get("ip") or "").strip()
        if not ip:
            raise ValueError("missing ip in ipinfo response")
        latitude, longitude = _parse_loc(payload.get("loc"))
        return EgressResult(
            ip=ip,
            country=_normalize_text(payload.get("country")),
            city=_normalize_text(payload.get("city")),
            latitude=latitude,
            longitude=longitude,
        )
    except Exception:
        payload = _run_curl_json(
            [
                "-sS",
                "--max-time",
                str(max(1, int(round(timeout_s)))),
                "--socks5-hostname",
                socks,
                "http://ip-api.com/json",
            ]
        )
        ip = str(payload.get("query") or "").strip()
        if not ip:
            raise ValueError("missing ip in ip-api response")
        return EgressResult(
            ip=ip,
            country=_normalize_text(payload.get("countryCode")),
            city=_normalize_text(payload.get("city")),
            latitude=_to_float(payload.get("lat")),
            longitude=_to_float(payload.get("lon")),
        )


def probe_latency_via_socks5(proxy_host: str, proxy_port: int, timeout_s: float = 15.0) -> int:
    socks = socks5_endpoint(proxy_host, proxy_port)
    deadline = time.monotonic() + max(timeout_s, 1.0)
    last_error = "failed"
    per_probe_cap = max(5, int(round(timeout_s)))

    for url in SOCKS_PROBE_URLS:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            break
        slice_timeout = max(2, min(per_probe_cap, int(round(remaining))))
        try:
            millis = _probe_single_url(socks, url, slice_timeout)
            if millis is not None:
                return millis
        except Exception as exc:
            last_error = short_curl_error(str(exc))

    raise RuntimeError(last_error)


def measure_download_via_socks5(
    proxy_host: str,
    proxy_port: int,
    timeout_s: float = 8.0,
    urls: Iterable[str] | None = None,
) -> float:
    socks = socks5_endpoint(proxy_host, proxy_port)
    deadline = time.monotonic() + max(timeout_s, 1.0)
    candidates = list(urls or SPEED_TEST_URLS)
    last_error = "failed"
    best_rate = 0.0

    for url in candidates:
        remaining = deadline - time.monotonic()
        if remaining <= 0.25:
            break
        try:
            rate = _download_single_url_via_socks(socks, url, remaining)
        except Exception as exc:
            last_error = short_curl_error(str(exc))
            continue
        if rate > best_rate:
            best_rate = rate
        if rate > 0:
            return rate

    if best_rate > 0:
        return best_rate
    raise RuntimeError(last_error)


def _probe_single_url(socks: str, url: str, timeout_s: int) -> int | None:
    output = _run_curl_text(
        [
            "-sS",
            "-o",
            "NUL",
            "--connect-timeout",
            str(timeout_s),
            "--max-time",
            str(timeout_s),
            "--socks5-hostname",
            socks,
            "-w",
            "%{http_code} %{time_total}",
            url,
        ]
    ).strip()
    fields = output.split()
    if len(fields) != 2:
        return None
    http_code, seconds = fields
    if not _is_success_http_code(http_code):
        raise RuntimeError(f"http {http_code}")
    millis = max(1, int(round(float(seconds) * 1000)))
    return millis


def _download_single_url_via_socks(socks: str, url: str, timeout_s: float) -> float:
    started = time.perf_counter()
    total = 0
    timeout_slice = max(2.0, min(timeout_s, 20.0))
    proxy_url = f"socks5h://{socks}"
    min_payload_bytes = 64 * 1024

    response = requests.get(
        url,
        stream=True,
        timeout=(min(5.0, timeout_slice), timeout_slice),
        proxies={"http": proxy_url, "https": proxy_url},
        headers={"User-Agent": "Fracture"},
        allow_redirects=True,
    )
    response.raise_for_status()
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if time.perf_counter() - started >= timeout_s:
                break
    finally:
        response.close()

    elapsed = max(time.perf_counter() - started, 0.001)
    if total < min_payload_bytes:
        if total <= 0:
            raise RuntimeError("no response")
        rounded_kib = math.floor(total / 1024)
        raise RuntimeError(f"too little data ({rounded_kib} KiB)")
    return total / elapsed


def short_curl_error(message: str) -> str:
    msg = (message or "").strip().lower()
    if "timeout" in msg or " 28" in msg or "curl: (28" in msg:
        return "timeout"
    if "refused" in msg or "curl: (7" in msg:
        return "refused"
    if "socks" in msg:
        return "proxy"
    if "http 000" in msg or "000" == msg:
        return "no response"
    if msg.startswith("http "):
        return msg
    return "failed"


def _run_curl_json(arguments: list[str]) -> dict:
    output = _run_curl_text(arguments)
    data = json.loads(output)
    if not isinstance(data, dict):
        raise ValueError("unexpected json payload")
    return data


def _run_curl_text(arguments: list[str]) -> str:
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is not available")
    proc = subprocess.run(
        [curl, *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        stderr = proc.stderr.strip()
        raise RuntimeError(stderr or f"curl failed with exit code {proc.returncode}")
    return proc.stdout


def _parse_loc(value: object) -> tuple[float | None, float | None]:
    if not isinstance(value, str) or "," not in value:
        return None, None
    left, right = value.split(",", 1)
    return _to_float(left), _to_float(right)


def _to_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _is_success_http_code(code: str) -> bool:
    try:
        value = int(code)
    except ValueError:
        return False
    return 200 <= value <= 399
