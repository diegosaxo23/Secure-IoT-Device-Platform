from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the API server and the internal MQTT client."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "IoT Device Platform"
    environment: str = "development"
    log_level: str = "INFO"

    dashboard_username: str = "admin"
    dashboard_password: str = ""
    bootstrap_master_key: str = ""

    database_url: str = "sqlite:////data/iot_device_platform.db"
    challenge_ttl_seconds: int = Field(default=120, ge=30, le=900)
    cert_validity_days: int = Field(default=365, ge=1, le=3650)
    online_timeout_seconds: int = Field(default=90, ge=15, le=3600)
    allow_reprovisioning: bool = False

    api_public_host: str = "127.0.0.1"
    api_public_port: int = Field(default=8443, ge=1, le=65535)
    mqtt_public_host: str = "127.0.0.1"
    mqtt_public_port: int = Field(default=8883, ge=1, le=65535)

    mqtt_enabled: bool = True
    mqtt_host: str = "broker"
    mqtt_port: int = Field(default=8883, ge=1, le=65535)

    simulator_manager_url: str = "http://simulator-manager:8090"

    manufacturing_enabled: bool = True
    manufacturing_agent_url: str = "http://host.docker.internal:8765"
    manufacturing_agent_token: str = ""
    manufacturing_agent_timeout_seconds: int = Field(default=900, ge=60, le=3600)

    ca_cert_path: Path = Path("/pki/ca/ca.crt")
    ca_key_path: Path = Path("/pki/ca/ca.key")
    server_cert_path: Path = Path("/pki/api/api.crt")
    server_key_path: Path = Path("/pki/api/api.key")
    mqtt_ca_path: Path = Path("/pki/ca/ca.crt")
    mqtt_client_cert_path: Path = Path("/pki/control/control.crt")
    mqtt_client_key_path: Path = Path("/pki/control/control.key")
    crl_path: Path = Path("/pki/crl/ca.crl")
    broker_restart_request_path: Path = Path("/data/broker/restart.request")

    @field_validator("dashboard_username", "dashboard_password", "bootstrap_master_key")
    @classmethod
    def validate_required_fields(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field is required; insecure default values must not be used in production")
        return value

    @property
    def api_public_url(self) -> str:
        return f"https://{self.api_public_host}:{self.api_public_port}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
