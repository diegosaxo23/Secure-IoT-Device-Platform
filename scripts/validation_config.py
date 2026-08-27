"""Small configuration helpers for validation/benchmark host scripts.

This module deliberately has no pyserial/PlatformIO dependency so software-only
validation tools can run on machines that do not have the factory toolchain.
"""
from __future__ import annotations

from pathlib import Path


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def resolve_api_url(explicit: str | None, env: dict[str, str]) -> str:
    if explicit:
        return explicit.rstrip("/")
    host = env.get("API_PUBLIC_HOST", "").strip()
    port = env.get("API_PUBLIC_PORT", "8443").strip() or "8443"
    if not host:
        raise ValueError("API_PUBLIC_HOST is missing from .env; provide --api-url")
    return f"https://{host}:{port}"
