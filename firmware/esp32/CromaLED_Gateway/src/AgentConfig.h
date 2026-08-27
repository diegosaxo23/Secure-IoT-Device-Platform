#pragma once

/*
 * Common configuration shared by every unit in a product family.
 *
 * These values never contain a per-device identity. The device_id and
 * bootstrap_secret are injected once through the serial manufacturing link and
 * stored in NVS, so every unit in the same family can use the same firmware.
 *
 * scripts/factory_program_esp32.py temporarily generates FactoryBuildConfig.h.
 * When present, that file overrides family, server, and Wi-Fi settings for a
 * manufacturing build without modifying this source file.
 */

#if defined(__has_include)
#  if __has_include("../.factory-build-cache/FactoryBuildConfig.h")
#    include "../.factory-build-cache/FactoryBuildConfig.h"
#  endif
#endif

// Laboratory Wi-Fi. A production product can replace this with a captive portal,
// SmartConfig, BLE provisioning, or another controlled network setup mechanism.
#ifndef IOT_WIFI_SSID
#define IOT_WIFI_SSID "CHANGE_SSID"
#endif
#ifndef IOT_WIFI_PASSWORD
#define IOT_WIFI_PASSWORD "CHANGE_PASSWORD"
#endif

// Must match an IP address or DNS name included in the API TLS certificate SAN.
#ifndef IOT_BOOTSTRAP_HOST
#define IOT_BOOTSTRAP_HOST "CHANGE_BOOTSTRAP_HOST"
#endif
#ifndef IOT_BOOTSTRAP_PORT
#define IOT_BOOTSTRAP_PORT 8443
#endif

// Values shared by the entire product family.
#ifndef IOT_PRODUCT_FAMILY
#define IOT_PRODUCT_FAMILY "CromaLED"
#endif
#ifndef IOT_FIRMWARE_VERSION
#define IOT_FIRMWARE_VERSION "cromaled-1.1.1"
#endif

// Runtime periods and limits.
#define IOT_TELEMETRY_INTERVAL_MS 5000UL
#define IOT_STATUS_INTERVAL_MS 30000UL
#define IOT_MQTT_RECONNECT_MS 5000UL
#define IOT_WIFI_CONNECT_TIMEOUT_MS 30000UL
#define IOT_TIME_SYNC_TIMEOUT_MS 7000UL
#define IOT_HTTP_TIMEOUT_MS 20000UL

// TLS needs a trustworthy clock. The platform exposes an HTTP time endpoint
// whose response is signed with a dedicated ECDSA P-256 key. The public key is
// embedded temporarily by the factory build, so no Internet/NTP access is needed.
#ifndef IOT_TIME_SERVICE_PORT
#define IOT_TIME_SERVICE_PORT 8091
#endif
#ifndef IOT_HAVE_TIME_SIGNING_PUBLIC_KEY
static const char IOT_TIME_SIGNING_PUBLIC_KEY[] = "";
#endif

// Optional: hold this pin low during boot to clear identity and credentials.
// Use -1 to disable the feature.
#define IOT_FACTORY_RESET_PIN -1
