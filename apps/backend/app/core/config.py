from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "Fracture Backend"
    version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8765

    @property
    def root_dir(self) -> Path:
        return Path(__file__).resolve().parents[4]

    @property
    def binaries_dir(self) -> Path:
        return self.root_dir / "binaries"

    @property
    def singbox_dir(self) -> Path:
        return self.root_dir / "sing-box"

    @property
    def configs_dir(self) -> Path:
        return self.root_dir / "configs"

    @property
    def data_dir(self) -> Path:
        return self.root_dir / "data"

    @property
    def profiles_path(self) -> Path:
        return self.data_dir / "profiles.json"

    @property
    def cloudflare_config_path(self) -> Path:
        return self.data_dir / "cloudflare-config.json"

    @property
    def app_settings_path(self) -> Path:
        return self.data_dir / "app-settings.json"


settings = Settings()
