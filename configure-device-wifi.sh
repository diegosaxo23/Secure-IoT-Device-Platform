#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 scripts/configure_device_wifi.py
