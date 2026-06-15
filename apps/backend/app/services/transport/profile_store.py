from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.db.database import fetch_selected_cloudflare_listener


class ProfileStore:
    """
    Hot‑reloadable provider of the active Cloudflare listener (CONNECT_IP, FAKE_SNI, etc.).
    Reloads from the database on every call to get_active_profile().
    """

    def __init__(self, config_path: Optional[Path] = None):
        # config_path is ignored; we always read from the database.
        self._config_path = config_path

    async def get_active_profile(self) -> tuple[str, int, bytes]:
        """
        Returns (connect_ip, connect_port, fake_sni_bytes).
        Raises RuntimeError if no valid listener is configured.
        """
        listener = await fetch_selected_cloudflare_listener()
        if not listener:
            raise RuntimeError("No Cloudflare listener selected for TCP Inject mode")

        connect_ip = str(listener.get("CONNECT_IP", "")).strip()
        fake_sni = str(listener.get("FAKE_SNI", "")).strip()
        connect_port = 443  # fixed for now, could be made configurable

        if not connect_ip:
            raise RuntimeError("TCP Inject mode requires CONNECT_IP in selected listener")
        if not fake_sni:
            raise RuntimeError("TCP Inject mode requires FAKE_SNI in selected listener")

        return connect_ip, connect_port, fake_sni.encode()
