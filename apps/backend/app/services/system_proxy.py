from __future__ import annotations

import ctypes
import platform
from ctypes import wintypes
from typing import Any

try:
    import winreg
except Exception:  # noqa: BLE001
    winreg = None  # type: ignore[assignment]


INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37


def _notify_internet_settings() -> None:
    if not hasattr(ctypes, "windll"):
        return
    internet_set_option = ctypes.windll.wininet.InternetSetOptionW
    internet_set_option.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
    internet_set_option.restype = wintypes.BOOL
    internet_set_option(None, INTERNET_OPTION_SETTINGS_CHANGED, None, 0)
    internet_set_option(None, INTERNET_OPTION_REFRESH, None, 0)


def _ensure_windows() -> None:
    if platform.system().lower() != "windows" or winreg is None:
        raise RuntimeError("System proxy control is currently supported only on Windows")


def enable_system_proxy(host: str, port: int, bypass: str) -> dict:
    _ensure_windows()
    proxy_server = f"{host}:{port}"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_server)
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, bypass)

    _notify_internet_settings()
    return {"enabled": True, "host": host, "port": port, "bypass": bypass}


def restore_system_proxy_state(state: dict[str, Any]) -> dict:
    _ensure_windows()
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if state.get("enabled") else 0)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, str(state.get("server", "")))
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, str(state.get("bypass", "")))

    _notify_internet_settings()
    return {"restored": True, **state}


def disable_system_proxy() -> dict:
    _ensure_windows()
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, "")

    _notify_internet_settings()
    return {"enabled": False}


def get_system_proxy_state() -> dict:
    _ensure_windows()
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        0,
        winreg.KEY_READ,
    ) as key:
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        try:
            bypass, _ = winreg.QueryValueEx(key, "ProxyOverride")
        except FileNotFoundError:
            bypass = ""

    host = ""
    port = 0
    if isinstance(server, str) and ":" in server:
        host, _, port_raw = server.rpartition(":")
        try:
            port = int(port_raw)
        except ValueError:
            port = 0

    return {
        "enabled": bool(enabled),
        "host": host,
        "port": port,
        "server": server,
        "bypass": bypass,
    }
