from __future__ import annotations

import json
import logging
import ssl
import threading
import uuid
from datetime import timedelta
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ModuleNotFoundError:  # Allow server tests to run without the MQTT client package installed.
    mqtt = None  # type: ignore[assignment]
from sqlalchemy import select

from .config import Settings
from .database import SessionLocal
from .models import Command, Device, MqttEvent
from .time_utils import ensure_utc, isoformat_utc, utcnow


logger = logging.getLogger(__name__)


class MqttService:
    """MQTT control-service client for monitoring devices and sending commands."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.connected = threading.Event()
        self.stop_event = threading.Event()
        self.client = None
        if mqtt is not None:
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id="control-service",
                protocol=mqtt.MQTTv5,
            )
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._stale_thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.settings.mqtt_enabled:
            logger.info("Internal MQTT client disabled")
            return
        if mqtt is None or self.client is None:
            raise RuntimeError("paho-mqtt is not installed while MQTT_ENABLED=true")

        required_files = (
            self.settings.mqtt_ca_path,
            self.settings.mqtt_client_cert_path,
            self.settings.mqtt_client_key_path,
        )
        missing = [str(path) for path in required_files if not path.exists()]
        if missing:
            logger.error("MQTT not started; required files are missing: %s", ", ".join(missing))
            return

        self.client.tls_set(
            ca_certs=str(self.settings.mqtt_ca_path),
            certfile=str(self.settings.mqtt_client_cert_path),
            keyfile=str(self.settings.mqtt_client_key_path),
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        self.client.tls_insecure_set(False)
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=45)
        self.client.loop_start()

        self._stale_thread = threading.Thread(
            target=self._mark_stale_devices,
            name="mqtt-stale-monitor",
            daemon=True,
        )
        self._stale_thread.start()
        logger.info(
            "MQTT client started for %s:%s",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
        )

    def stop(self) -> None:
        self.stop_event.set()
        if self.settings.mqtt_enabled and self.client is not None:
            try:
                self.client.disconnect()
            except Exception:  # pragma: no cover - defensive shutdown
                logger.exception("Error disconnecting the MQTT client")
            self.client.loop_stop()
        if self._stale_thread and self._stale_thread.is_alive():
            self._stale_thread.join(timeout=2)

    def publish_command(
        self,
        *,
        device_id: str,
        command_name: str,
        parameters: dict[str, Any],
    ) -> tuple[Command, str, dict[str, Any]]:
        if mqtt is None or self.client is None:
            raise RuntimeError("MQTT client unavailable")

        command_id = uuid.uuid4().hex
        issued_at = utcnow()
        topic = f"devices/{device_id}/command"
        payload = {
            "command_id": command_id,
            "command": command_name,
            "parameters": parameters,
            "issued_at": isoformat_utc(issued_at),
        }
        payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

        with SessionLocal() as db:
            device = db.get(Device, device_id)
            if device is None:
                raise ValueError("device not registered")
            command = Command(
                command_id=command_id,
                device_id=device_id,
                command_name=command_name,
                parameters_json=json.dumps(parameters, ensure_ascii=False),
                status="pending",
                created_at=issued_at,
            )
            db.add(command)
            db.commit()
            db.refresh(command)

            if not self.connected.is_set():
                command.status = "broker-unavailable"
                db.commit()
                raise RuntimeError("control service is not connected to the MQTT broker")

            info = self.client.publish(topic, payload_json, qos=1, retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                command.status = "publish-error"
                db.commit()
                raise RuntimeError(f"MQTT command publish failed: rc={info.rc}")

            try:
                info.wait_for_publish(timeout=5)
            except RuntimeError as exc:
                command.status = "publish-error"
                db.commit()
                raise RuntimeError("MQTT publish was not acknowledged") from exc

            command.status = "sent"
            command.sent_at = utcnow()
            db.commit()
            db.refresh(command)
            return command, topic, payload

    def evict_device(self, device_id: str) -> None:
        """Force broker-side re-authentication after a certificate revocation.

        Client IDs are now bound to certificate CNs by Mosquitto, so the previous
        administrative duplicate-Client-ID eviction trick is intentionally removed.
        Instead, revocation writes a restart request into the broker persistence
        volume. The broker entrypoint observes the request, terminates Mosquitto, and
        Docker immediately restarts it. All valid devices reconnect; the revoked
        certificate is rejected against the updated CRL.
        """
        if not self.settings.mqtt_enabled:
            return
        worker = threading.Thread(
            target=self._request_broker_security_restart,
            args=(device_id,),
            name=f"mqtt-security-restart-{device_id}",
            daemon=True,
        )
        worker.start()

    def evict_device_sync(self, device_id: str) -> None:
        """Blocking variant used by command-line administration tools."""
        if not self.settings.mqtt_enabled:
            return
        self._request_broker_security_restart(device_id)

    def _request_broker_security_restart(self, device_id: str) -> None:
        path = self.settings.broker_restart_request_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "requested_at": isoformat_utc(utcnow()),
                "reason": "certificate-revoked",
                "device_id": device_id,
                "request_id": uuid.uuid4().hex,
            }
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
            logger.warning(
                "Broker security restart requested after revoking %s; all MQTT clients will re-authenticate",
                device_id,
            )
        except OSError:
            logger.exception(
                "Could not request broker security restart after revoking %s", device_id
            )

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:  # type: ignore[no-untyped-def]
        if reason_code.is_failure:
            logger.error("MQTT connection rejected: %s", reason_code)
            self.connected.clear()
            return
        self.connected.set()
        subscriptions = [
            ("devices/+/status", 1),
            ("devices/+/telemetry", 1),
            ("devices/+/response", 1),
        ]
        client.subscribe(subscriptions)
        logger.info("Control service connected to the MQTT broker and subscribed to devices/+")

    def _on_disconnect(
        self,
        _client,
        _userdata,
        _disconnect_flags,
        reason_code,
        _properties,
    ) -> None:  # type: ignore[no-untyped-def]
        self.connected.clear()
        if self.stop_event.is_set():
            logger.info("MQTT client stopped")
        else:
            logger.warning("MQTT client disconnected: %s", reason_code)

    def _on_message(self, _client, _userdata, message) -> None:  # type: ignore[no-untyped-def]
        try:
            parts = message.topic.split("/")
            if len(parts) != 3 or parts[0] != "devices":
                return
            device_id, kind = parts[1], parts[2]
            if kind not in {"status", "telemetry", "response"}:
                return

            payload_text = message.payload.decode("utf-8", errors="replace")
            try:
                payload_obj: Any = json.loads(payload_text)
            except json.JSONDecodeError:
                payload_obj = {"raw": payload_text}

            self._store_event(device_id, kind, message.topic, payload_text, payload_obj)
        except Exception:
            logger.exception("Error processing MQTT message on %s", message.topic)

    def _store_event(
        self,
        device_id: str,
        kind: str,
        topic: str,
        payload_text: str,
        payload_obj: Any,
    ) -> None:
        now = utcnow()
        with SessionLocal() as db:
            device = db.get(Device, device_id)
            if device is None:
                logger.warning("MQTT message ignored for unregistered device: %s", device_id)
                return

            if device.lifecycle_status == "revoked" or not device.enabled:
                # A session opened before revocation may publish briefly until the broker evicts it.
                # It must never mark a revoked device ONLINE again.
                device.online = False
                event = MqttEvent(
                    device_id=device_id,
                    kind=kind,
                    topic=topic,
                    payload=payload_text[:262144],
                    received_at=now,
                )
                db.add_all([device, event])
                db.commit()
                logger.warning(
                    "MQTT message received from revoked/disabled device: %s",
                    device_id,
                )
                return

            device.last_seen = now
            if kind == "status" and isinstance(payload_obj, dict):
                online_value = payload_obj.get("online", True)
                device.online = online_value if isinstance(online_value, bool) else True
                firmware = payload_obj.get("firmware") or payload_obj.get("firmware_version")
                if isinstance(firmware, str):
                    device.firmware_version = firmware[:64]
            else:
                device.online = True

            if kind == "telemetry" and isinstance(payload_obj, (dict, list)):
                device.application_state_json = json.dumps(payload_obj, ensure_ascii=False)
            elif (
                kind == "status"
                and device.application_state_json is None
                and isinstance(payload_obj, (dict, list))
            ):
                device.application_state_json = json.dumps(payload_obj, ensure_ascii=False)

            event = MqttEvent(
                device_id=device_id,
                kind=kind,
                topic=topic,
                payload=payload_text[:262144],
                received_at=now,
            )
            db.add_all([device, event])

            if kind == "response" and isinstance(payload_obj, dict):
                command_id = payload_obj.get("command_id")
                if isinstance(command_id, str):
                    command = db.get(Command, command_id)
                    if command is not None and command.device_id == device_id:
                        command.status = str(payload_obj.get("status", "completed"))[:24]
                        command.response_at = now
                        command.response_payload = payload_text[:262144]
                        db.add(command)

            db.commit()

    def _get_stale_cutoff(self) -> datetime:
        """Returns the UTC cutoff time for marking devices as stale."""
        return utcnow() - timedelta(seconds=self.settings.online_timeout_seconds)

    def _mark_stale_devices(self) -> None:
        interval = max(5, min(30, self.settings.online_timeout_seconds // 3))
        while not self.stop_event.wait(interval):
            cutoff = utcnow() - timedelta(seconds=self.settings.online_timeout_seconds)
            try:
                with SessionLocal() as db:
                    devices = db.scalars(select(Device).where(Device.online.is_(True))).all()
                    changed = False
                    for device in devices:
                        last_seen = ensure_utc(device.last_seen)
                        if last_seen is None or last_seen < cutoff:
                            device.online = False
                            db.add(device)
                            changed = True
                    if changed:
                        db.commit()
            except Exception:
                logger.exception("Error updating inactive devices")
