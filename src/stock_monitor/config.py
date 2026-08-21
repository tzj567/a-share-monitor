"""Desktop configuration and secret storage.

Non-sensitive preferences live in JSON. API tokens are delegated to the
operating-system keyring and are never written to the JSON file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import keyring
from keyring.errors import KeyringError
from pydantic import Field

from .models import StrictModel


ProviderName = Literal["csv", "akshare", "tushare", "ifind"]
VolumeUnit = Literal["shares", "lots"]
SECRET_SERVICE = "A股量化监控"
SECRET_KEYS = ("tushare_token", "ifind_refresh_token", "cls_token", "tdengine_password")


def default_app_dir() -> Path:
    base = os.getenv("APPDATA") or str(Path.home())
    return Path(base) / "AStockMonitor"


class DesktopConfig(StrictModel):
    provider: ProviderName = "akshare"
    database_path: str = ""
    csv_bars_path: str = ""
    csv_reference_closes_path: str = ""
    csv_volume_unit: VolumeUnit = "shares"
    ifind_base_url: str = "https://quantapi.51ifind.com/api/v1"
    ifind_volume_unit: VolumeUnit = "shares"
    cls_api_base_url: str = ""
    cls_news_endpoint: str = ""
    fund_flow_provider: Literal["auto", "ifind", "akshare"] = "auto"
    auto_sync_news: bool = True
    auto_sync_fund_flow: bool = True
    auto_monitor_on_start: bool = True
    auto_monitor_interval_minutes: int = Field(default=5, ge=5, le=60)
    sync_days: int = Field(default=180, ge=40, le=3650)
    interval: str = "1d"
    auto_evaluate: bool = True
    retry_attempts: int = Field(default=3, ge=1, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0, le=10)
    advanced_mode: bool = False
    kafka_bootstrap_servers: str = Field(default="localhost:19092", min_length=3, max_length=500)
    kafka_market_topic: str = Field(default="ashare.market.bar.v1", pattern=r"^[a-zA-Z0-9._-]{1,249}$")
    kafka_fund_flow_topic: str = Field(default="ashare.fund-flow.v1", pattern=r"^[a-zA-Z0-9._-]{1,249}$")
    kafka_news_topic: str = Field(default="ashare.news.v1", pattern=r"^[a-zA-Z0-9._-]{1,249}$")
    tdengine_rest_url: str = Field(default="http://localhost:6041", min_length=8, max_length=500)
    tdengine_database: str = Field(default="ashare", pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
    tdengine_user: str = Field(default="root", min_length=1, max_length=128)
    flink_dashboard_url: str = Field(default="http://localhost:8081", min_length=8, max_length=500)

    def resolved_database_path(self) -> Path:
        return Path(self.database_path) if self.database_path else default_app_dir() / "stock_monitor.db"


class ConfigStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_app_dir() / "config.json"

    def load(self) -> DesktopConfig:
        if not self.path.exists():
            return DesktopConfig()
        try:
            return DesktopConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return DesktopConfig()

    def save(self, config: DesktopConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)


class SecretStore:
    """Thin wrapper around Windows Credential Manager via keyring."""

    def get(self, name: str) -> str:
        if name not in SECRET_KEYS:
            raise ValueError(f"unsupported secret: {name}")
        environment_name = f"STOCK_MONITOR_{name.upper()}"
        environment_value = os.getenv(environment_name)
        if environment_value:
            return environment_value
        try:
            return keyring.get_password(SECRET_SERVICE, name) or ""
        except KeyringError:
            return ""

    def set(self, name: str, value: str) -> None:
        if name not in SECRET_KEYS:
            raise ValueError(f"unsupported secret: {name}")
        try:
            if value:
                keyring.set_password(SECRET_SERVICE, name, value)
            else:
                try:
                    keyring.delete_password(SECRET_SERVICE, name)
                except KeyringError:
                    pass
        except KeyringError as error:
            raise RuntimeError("Windows 凭据存储不可用，请检查系统凭据服务") from error
