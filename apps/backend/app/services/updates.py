from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from app.core.config import settings

SINGBOX_REPO = "SagerNet/sing-box"
GITHUB_ACCEPT = "application/vnd.github+json"
USER_AGENT = "FractureBackend/0.1.0"


@dataclass
class ReleaseInfo:
    current_version: str
    latest_version: str | None
    update_available: bool
    release_url: str | None
    download_url: str | None
    notes: str | None
    checked: bool
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "currentVersion": self.current_version,
            "latestVersion": self.latest_version,
            "updateAvailable": self.update_available,
            "releaseUrl": self.release_url,
            "downloadUrl": self.download_url,
            "notes": self.notes,
            "checked": self.checked,
            "error": self.error,
        }


def _http_get_json(url: str) -> object:
    request = Request(
        url,
        headers={
            "Accept": GITHUB_ACCEPT,
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _download_file(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _normalize_version(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lstrip("v")
    return normalized or None


def _current_singbox_binary() -> Path:
    binary_name = "sing-box.exe" if settings.root_dir.drive else "sing-box"
    return settings.singbox_dir / binary_name


def current_singbox_version() -> str | None:
    binary_path = _current_singbox_binary()
    if not binary_path.exists():
        return None

    try:
        result = subprocess.run(
            [str(binary_path), "version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except Exception:
        return None

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    first = lines[0]
    if first.lower().startswith("sing-box version "):
        return first[len("sing-box version ") :].strip()
    return first


def latest_singbox_release() -> ReleaseInfo:
    current_version = current_singbox_version() or ""

    try:
        payload = _http_get_json(f"https://api.github.com/repos/{SINGBOX_REPO}/releases/latest")
    except Exception as exc:  # noqa: BLE001
        return ReleaseInfo(
            current_version=current_version,
            latest_version=None,
            update_available=False,
            release_url=f"https://github.com/{SINGBOX_REPO}/releases",
            download_url=None,
            notes=None,
            checked=False,
            error=str(exc),
        )

    if not isinstance(payload, dict):
        return ReleaseInfo(
            current_version=current_version,
            latest_version=None,
            update_available=False,
            release_url=f"https://github.com/{SINGBOX_REPO}/releases",
            download_url=None,
            notes=None,
            checked=False,
            error="unexpected GitHub response",
        )

    latest_version = _normalize_version(str(payload.get("tag_name", "")))
    release_url = str(payload.get("html_url", "")) or None
    assets = payload.get("assets", [])
    download_url = None
    if isinstance(assets, list):
        preferred = None
        fallback = None
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name", ""))
            url = str(asset.get("browser_download_url", "")) or None
            if not url:
                continue
            lower_name = name.lower()
            if lower_name == f"sing-box-{latest_version}-windows-amd64.zip":
                preferred = url
                break
            if lower_name == f"sing-box-{latest_version}-windows-amd64-legacy-windows-7.zip":
                fallback = url
        download_url = preferred or fallback

    return ReleaseInfo(
        current_version=current_version,
        latest_version=latest_version,
        update_available=bool(latest_version and latest_version != _normalize_version(current_version)),
        release_url=release_url,
        download_url=download_url,
        notes=str(payload.get("body", "")) or None,
        checked=True,
        error=None,
    )


def update_singbox_binary() -> dict:
    release = latest_singbox_release()
    if release.error:
        return {
            "ok": False,
            "message": release.error,
            "version": release.latest_version,
            "releaseUrl": release.release_url,
        }

    if not release.update_available:
        return {
            "ok": True,
            "message": "sing-box is already up to date",
            "version": release.current_version or release.latest_version,
            "releaseUrl": release.release_url,
        }

    if not release.download_url:
        return {
            "ok": False,
            "message": "No compatible Windows sing-box download was found in the latest release",
            "version": release.latest_version,
            "releaseUrl": release.release_url,
        }

    binary_path = _current_singbox_binary()
    backup_path = binary_path.with_suffix(binary_path.suffix + ".bak")
    temp_dir = Path(tempfile.mkdtemp(prefix="fracture_singbox_update_"))
    archive_path = temp_dir / "sing-box.zip"

    try:
        _download_file(release.download_url, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.namelist()
            binary_member = next((name for name in members if name.lower().endswith("sing-box.exe")), None)
            cronet_member = next((name for name in members if name.lower().endswith("libcronet.dll")), None)
            if not binary_member:
                raise RuntimeError("downloaded sing-box archive does not contain sing-box.exe")
            archive.extract(binary_member, temp_dir)
            if cronet_member:
                archive.extract(cronet_member, temp_dir)

        extracted_binary = next(temp_dir.rglob("sing-box.exe"))
        extracted_cronet = next(temp_dir.rglob("libcronet.dll"), None)
        settings.singbox_dir.mkdir(parents=True, exist_ok=True)
        if binary_path.exists():
            shutil.copy2(binary_path, backup_path)
        shutil.copy2(extracted_binary, binary_path)
        if extracted_cronet is not None:
            shutil.copy2(extracted_cronet, settings.singbox_dir / "libcronet.dll")
    except Exception as exc:  # noqa: BLE001
        if backup_path.exists():
            with contextlib.suppress(Exception):
                shutil.copy2(backup_path, binary_path)
        return {
            "ok": False,
            "message": f"Failed to update sing-box: {exc}",
            "version": release.latest_version,
            "releaseUrl": release.release_url,
        }
    finally:
        with contextlib.suppress(Exception):
            shutil.rmtree(temp_dir, ignore_errors=True)
        with contextlib.suppress(Exception):
            if backup_path.exists():
                backup_path.unlink()

    return {
        "ok": True,
        "message": f"sing-box updated to {release.latest_version}",
        "version": release.latest_version,
        "releaseUrl": release.release_url,
    }
