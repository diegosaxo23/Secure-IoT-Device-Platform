# MQTT Contract 2.0

Each device uses a dedicated topic branch:

```text
devices/<device_id>/status
devices/<device_id>/telemetry
devices/<device_id>/command
devices/<device_id>/response
devices/<device_id>/config
```

## Status

Status messages normally identify the device, family, firmware version, online state, and runtime metadata.

## Telemetry

Telemetry payloads are application-specific but should include stable identity/runtime fields and a product-specific state or measurement section. CromaLED and AREA LZ7 expose channel state; AS7341 exposes spectral bands.

## Commands

The control service publishes a command envelope containing a command ID, command name, and parameters. The command ID allows a later response to be correlated with the dashboard request.

## Responses

A response should include the command ID and a terminal or intermediate status such as `completed`, `rejected`, or an application-specific error result.

## Configuration

The device may subscribe to `config` for installation/application configuration messages. The current demonstrator primarily uses command/response for interactive control, but the ACL reserves the configuration branch for device-specific settings.

## ACL

The broker derives the MQTT username from the client-certificate Common Name. The ACL gives a device access only to its own branch. The dedicated control-service certificate has the wider publish/subscribe permissions required by the dashboard.

This means knowledge of another device ID is not sufficient to publish or subscribe as that device; the peer must also present a valid certificate whose identity satisfies broker authorization.
