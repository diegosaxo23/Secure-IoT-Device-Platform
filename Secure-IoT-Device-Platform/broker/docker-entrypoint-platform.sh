#!/bin/sh
set -eu

SOURCE=/pki-src
DEST=/mosquitto/certs
CRL_SOURCE="$SOURCE/crl/ca.crl"

required_files="
$SOURCE/ca/ca.crt
$SOURCE/broker/broker.crt
$SOURCE/broker/broker.key
$SOURCE/control/control.crt
$SOURCE/control/control.key
$SOURCE/healthcheck/healthcheck.crt
$SOURCE/healthcheck/healthcheck.key
$CRL_SOURCE
"

for file in $required_files; do
    if [ ! -f "$file" ]; then
        echo "[broker] ERROR: required PKI file is missing: $file" >&2
        echo "[broker] Run scripts/setup.py first to initialize the platform." >&2
        exit 1
    fi
done

mkdir -p "$DEST/ca" "$DEST/broker" "$DEST/control" "$DEST/healthcheck"

cp "$SOURCE/ca/ca.crt" "$DEST/ca/ca.crt"
cp "$SOURCE/broker/broker.crt" "$DEST/broker/broker.crt"
cp "$SOURCE/broker/broker.key" "$DEST/broker/broker.key"
cp "$SOURCE/control/control.crt" "$DEST/control/control.crt"
cp "$SOURCE/control/control.key" "$DEST/control/control.key"
cp "$SOURCE/healthcheck/healthcheck.crt" "$DEST/healthcheck/healthcheck.crt"
cp "$SOURCE/healthcheck/healthcheck.key" "$DEST/healthcheck/healthcheck.key"

chown -R mosquitto:mosquitto "$DEST"
chmod 0755 "$DEST" "$DEST/ca" "$DEST/broker" "$DEST/control" "$DEST/healthcheck"
chmod 0644 "$DEST/ca/ca.crt" "$DEST/broker/broker.crt" "$DEST/control/control.crt" "$DEST/healthcheck/healthcheck.crt"
chmod 0600 "$DEST/broker/broker.key" "$DEST/control/control.key" "$DEST/healthcheck/healthcheck.key"

# Mosquitto can reload certificates and the CRL with SIGHUP. The API regenerates
# /pki-src/crl/ca.crl atomically, so this watcher detects changes and reloads TLS
# configuration without restarting the container.
"$@" &
MOSQUITTO_PID=$!

watch_crl() {
    restart_request=/mosquitto/data/restart.request
    last_hash="$(sha256sum "$CRL_SOURCE" | awk '{print $1}')"
    if [ -f "$restart_request" ]; then
        last_restart_hash="$(sha256sum "$restart_request" | awk '{print $1}')"
    else
        last_restart_hash=""
    fi

    while kill -0 "$MOSQUITTO_PID" 2>/dev/null; do
        sleep 0.25
        current_hash="$(sha256sum "$CRL_SOURCE" | awk '{print $1}')"
        if [ "$current_hash" != "$last_hash" ]; then
            last_hash="$current_hash"
            echo "[broker] CRL updated; reloading TLS configuration"
            kill -HUP "$MOSQUITTO_PID" 2>/dev/null || true
        fi

        if [ -f "$restart_request" ]; then
            current_restart_hash="$(sha256sum "$restart_request" | awk '{print $1}')"
        else
            current_restart_hash=""
        fi
        if [ -n "$current_restart_hash" ] && [ "$current_restart_hash" != "$last_restart_hash" ]; then
            echo "[broker] Security restart requested; forcing all MQTT clients to re-authenticate"
            kill -TERM "$MOSQUITTO_PID" 2>/dev/null || true
            return
        fi
        last_restart_hash="$current_restart_hash"
    done
}

watch_crl &
WATCHER_PID=$!

terminate() {
    kill -TERM "$MOSQUITTO_PID" 2>/dev/null || true
}
trap terminate INT TERM

set +e
wait "$MOSQUITTO_PID"
STATUS=$?
set -e
kill "$WATCHER_PID" 2>/dev/null || true
wait "$WATCHER_PID" 2>/dev/null || true
exit "$STATUS"
