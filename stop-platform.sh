#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 scripts/start_platform.py --stop-platform
