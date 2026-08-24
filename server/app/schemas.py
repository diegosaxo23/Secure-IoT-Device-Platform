from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .security import validate_device_id


class ChallengeRequest(BaseModel):
    device_id: str = Field(min_length=3, max_length=64)

    @field_validator("device_id")
    @classmethod
    def normalize_device_id(cls, value: str) -> str:
        return validate_device_id(value)


class ChallengeResponse(BaseModel):
    protocol: Literal["IOT-BOOTSTRAP-V1"] = "IOT-BOOTSTRAP-V1"
    device_id: str
    session_id: str
    nonce: str
    expires_at: datetime
    hmac_algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    proof_format: str = "hex-lowercase"
    canonical_fields: list[str] = [
        "protocol",
        "device_id",
        "session_id",
        "nonce",
        "csr_sha256",
    ]


class EnrollmentRequest(BaseModel):
    device_id: str = Field(min_length=3, max_length=64)
    session_id: str = Field(min_length=16, max_length=64)
    csr_pem: str = Field(min_length=128, max_length=16384)
    proof: str = Field(min_length=64, max_length=64, pattern=r"^[0-9A-Fa-f]{64}$")

    @field_validator("device_id")
    @classmethod
    def normalize_device_id(cls, value: str) -> str:
        return validate_device_id(value)


class MqttProvisioningData(BaseModel):
    host: str
    port: int
    tls: bool = True
    client_id: str
    status_topic: str
    telemetry_topic: str
    command_topic: str
    response_topic: str


class EnrollmentResponse(BaseModel):
    protocol: Literal["IOT-BOOTSTRAP-V1"] = "IOT-BOOTSTRAP-V1"
    device_id: str
    certificate_pem: str
    ca_certificate_pem: str
    certificate_serial: str
    certificate_not_after: datetime
    mqtt: MqttProvisioningData


class DeviceCreateRequest(BaseModel):
    device_id: str = Field(min_length=3, max_length=64)
    family: str = Field(default="generic", min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    deployment_type: Literal["physical", "simulated"] = "physical"
    allow_reprovisioning: bool = False

    @field_validator("device_id")
    @classmethod
    def normalize_device_id(cls, value: str) -> str:
        return validate_device_id(value)

    @field_validator("family")
    @classmethod
    def normalize_family(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("family cannot be empty")
        return value


class DeviceCreateResponse(BaseModel):
    device_id: str
    family: str
    deployment_type: Literal["physical", "simulated"]
    bootstrap_secret: str
    warning: str = "The bootstrap secret is shown only once. Load it into the unit through a secure manufacturing process."


class DeviceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    family: str
    display_name: str | None
    deployment_type: str
    enabled: bool
    lifecycle_status: str
    allow_reprovisioning: bool
    online: bool
    last_seen: datetime | None
    firmware_version: str | None
    provisioned_at: datetime | None
    certificate_serial: str | None
    certificate_not_after: datetime | None
    revoked_at: datetime | None


class DeviceRuntimeState(BaseModel):
    """Lightweight operational state used by the dashboard for live refresh."""

    device_id: str
    family: str
    deployment_type: str
    online: bool
    lifecycle_status: str
    last_seen: datetime | None
    firmware_version: str | None
    latest_state: Any | None = None


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("command")
    @classmethod
    def normalize_command(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("command cannot be empty")
        return value


class CommandResponse(BaseModel):
    command_id: str
    device_id: str
    status: str
    topic: str
    payload: dict[str, Any]


class CommandActivity(BaseModel):
    """One transmitted command or one MQTT response received from a device."""

    direction: str
    command_id: str | None = None
    command_name: str
    status: str
    timestamp: datetime
    details: str = ""


class ApiMessage(BaseModel):
    message: str
