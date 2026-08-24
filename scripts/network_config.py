#!/usr/bin/env python3
"""Detect the device-facing IPv4 address of the host Wi-Fi adapter.

The ESP32 devices must reach the API and MQTT broker through the same Wi-Fi
network used by the computer. This module deliberately ignores Ethernet,
Docker, WSL, VPN, Hyper-V, and other virtual interfaces. A default gateway is
NOT required: isolated/local Wi-Fi networks (for example an ESP32 access point)
are valid as long as the physical Wi-Fi adapter is up and has a usable private
IPv4 address.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class IPv4Candidate:
    address: str
    interface: str = ""


def _valid_candidate(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        isinstance(ip, ipaddress.IPv4Address)
        and ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_unspecified
    )


def _dedupe(items: list[IPv4Candidate]) -> list[IPv4Candidate]:
    seen: set[str] = set()
    result: list[IPv4Candidate] = []
    for item in items:
        if item.address in seen or not _valid_candidate(item.address):
            continue
        seen.add(item.address)
        result.append(item)
    return result


def _windows_wifi_candidates() -> list[IPv4Candidate]:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return []

    # NdisPhysicalMedium 9 is native 802.11/Wi-Fi. The name/description tests
    # provide a fallback for adapters/drivers that do not expose that value.
    script = r'''
$items = @()
Get-NetIPConfiguration -ErrorAction SilentlyContinue | ForEach-Object {
    $cfg = $_
    # A default gateway is intentionally NOT required here. An isolated ESP32
    # access point can provide a perfectly valid local subnet without Internet
    # routing or a Windows default gateway.
    if ($cfg.IPv4Address) {
        $adapter = Get-NetAdapter -InterfaceIndex $cfg.InterfaceIndex -ErrorAction SilentlyContinue
        if ($adapter -and $adapter.Status -eq 'Up') {
            $name = [string]$cfg.InterfaceAlias
            $desc = [string]$adapter.InterfaceDescription
            $isWifi = ($adapter.NdisPhysicalMedium -eq 9) -or
                      ($name -match '(?i)wi-?fi|wlan|wireless') -or
                      ($desc -match '(?i)wi-?fi|wlan|wireless|802\.11')
            if ($isWifi) {
                foreach ($ip in $cfg.IPv4Address) {
                    $items += [PSCustomObject]@{Address=$ip.IPAddress; Interface=$name}
                }
            }
        }
    }
}
$items | ConvertTo-Json -Compress
'''.strip()
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []

    result: list[IPv4Candidate] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        address = str(item.get("Address", "")).strip()
        interface = str(item.get("Interface", "")).strip()
        if address:
            result.append(IPv4Candidate(address, interface))
    return result


def _linux_wifi_interfaces() -> list[str]:
    interfaces: list[str] = []
    iw = shutil.which("iw")
    if iw:
        completed = subprocess.run(
            [iw, "dev"], check=False, shell=False, capture_output=True, text=True, timeout=5
        )
        if completed.returncode == 0:
            for line in completed.stdout.splitlines():
                match = re.match(r"\s*Interface\s+(\S+)", line)
                if match:
                    interfaces.append(match.group(1))
    if not interfaces:
        sys_class = "/sys/class/net"
        try:
            names = os.listdir(sys_class)
        except OSError:
            names = []
        interfaces.extend(name for name in names if re.match(r"^(wl|wlan)", name, re.I))
    return list(dict.fromkeys(interfaces))


def _posix_wifi_candidates() -> list[IPv4Candidate]:
    ip_cmd = shutil.which("ip")
    if not ip_cmd:
        return []
    wifi_interfaces = set(_linux_wifi_interfaces())
    if not wifi_interfaces:
        return []

    completed = subprocess.run(
        [ip_cmd, "-4", "-o", "addr", "show", "scope", "global"],
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if completed.returncode != 0:
        return []

    result: list[IPv4Candidate] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        interface = parts[1]
        if interface not in wifi_interfaces:
            continue
        try:
            inet_index = parts.index("inet")
            address = parts[inet_index + 1].split("/", 1)[0]
        except (ValueError, IndexError):
            continue
        result.append(IPv4Candidate(address, interface))
    return result


def collect_wifi_ipv4_candidates() -> list[IPv4Candidate]:
    if os.name == "nt":
        return _dedupe(_windows_wifi_candidates())
    return _dedupe(_posix_wifi_candidates())


def select_wifi_ipv4() -> tuple[str, list[IPv4Candidate]]:
    """Return the IPv4 of the active physical Wi-Fi adapter.

    Multiple candidates are unusual. When present, prefer 192.168.x.x, then
    10.x.x.x, then 172.16/12, while keeping the result deterministic.
    """
    candidates = collect_wifi_ipv4_candidates()
    if not candidates:
        raise RuntimeError(
            "No active Wi-Fi adapter with a usable private IPv4 address was detected. "
            "A default gateway is not required. Connect the computer to the IoT Wi-Fi/AP, "
            "assign a valid IPv4 address to that Wi-Fi adapter if the AP does not provide DHCP, "
            "and start the platform again."
        )

    def rank(candidate: IPv4Candidate) -> tuple[int, str]:
        ip = ipaddress.ip_address(candidate.address)
        if ip in ipaddress.ip_network("192.168.0.0/16"):
            family = 0
        elif ip in ipaddress.ip_network("10.0.0.0/8"):
            family = 1
        elif ip in ipaddress.ip_network("172.16.0.0/12"):
            family = 2
        else:
            family = 3
        return family, candidate.address

    selected = min(candidates, key=rank)
    return selected.address, candidates


def describe_candidates(candidates: list[IPv4Candidate]) -> str:
    return ", ".join(
        f"{item.address}{f' ({item.interface})' if item.interface else ''}" for item in candidates
    )


def detect_active_wifi_ssid() -> str | None:
    """Best-effort SSID detection for operator guidance.

    Address selection never depends on this value; it is used only to make the
    startup Wi-Fi prompt safer and more convenient.
    """
    if os.name == "nt":
        netsh = shutil.which("netsh")
        if not netsh:
            return None
        completed = subprocess.run(
            [netsh, "wlan", "show", "interfaces"],
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            timeout=8,
            errors="replace",
        )
        if completed.returncode != 0:
            return None
        for line in completed.stdout.splitlines():
            match = re.match(r"^\s*SSID\s*:\s*(.+?)\s*$", line, re.I)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
        return None

    iw = shutil.which("iw")
    if not iw:
        return None
    for interface in _linux_wifi_interfaces():
        completed = subprocess.run(
            [iw, "dev", interface, "link"],
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode != 0:
            continue
        for line in completed.stdout.splitlines():
            match = re.match(r"\s*SSID:\s*(.+?)\s*$", line)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
    return None
