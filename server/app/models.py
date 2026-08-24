from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .time_utils import utcnow


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    family: Mapped[str] = mapped_column(String(64), default="generic", index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deployment_type: Mapped[str] = mapped_column(String(16), default="physical", nullable=False, index=True)
    bootstrap_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    allow_reprovisioning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    certificate_serial: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    certificate_not_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    certificate_pem: Mapped[str | None] = mapped_column(Text, nullable=True)

    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    firmware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    application_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    bootstrap_sessions: Mapped[list[BootstrapSession]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
    )
    events: Mapped[list[MqttEvent]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
    )
    commands: Mapped[list[Command]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
    )


class BootstrapSession(Base):
    __tablename__ = "bootstrap_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    nonce_b64: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    csr_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    device: Mapped[Device] = relationship(back_populates="bootstrap_sessions")


class MqttEvent(Base):
    __tablename__ = "mqtt_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    topic: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    device: Mapped[Device] = relationship(back_populates="events")


class Command(Base):
    __tablename__ = "commands"

    command_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    command_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    device: Mapped[Device] = relationship(back_populates="commands")


Index("ix_mqtt_event_device_time", MqttEvent.device_id, MqttEvent.received_at)
Index("ix_command_device_time", Command.device_id, Command.created_at)


class RevokedCertificate(Base):
    __tablename__ = "revoked_certificates"

    serial_hex: Mapped[str] = mapped_column(String(80), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), default="key_compromise", nullable=False)
