#!/usr/bin/env python3
"""Fail if a public source tree contains common installation/runtime artifacts."""
from __future__ import annotations

import argparse
from pathlib import Path

FORBIDDEN_DIR_NAMES = {".pio", ".factory-build-cache", "__pycache__", ".pytest_cache", ".venv"}
FORBIDDEN_SUFFIXES = {".key", ".crt", ".db", ".sqlite", ".sqlite3", ".log", ".zip"}


def check_tree(root: Path) -> list[str]:
    violations: list[str] = []
    root = root.resolve()
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in FORBIDDEN_DIR_NAMES for part in rel.parts):
            violations.append(str(rel))
            continue
        if not path.is_file():
            continue
        if path.name == ".env":
            violations.append(str(rel))
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(str(rel))
            continue
        if path.name.startswith("metrics") and path.suffix.lower() == ".csv":
            violations.append(str(rel))
            continue

        if rel.parts and rel.parts[0] == "pki" and rel != Path("pki/README.md"):
            violations.append(str(rel))
        elif rel.parts and rel.parts[0] == "simulated_state" and rel != Path("simulated_state/README.md"):
            violations.append(str(rel))
        elif rel.parts and rel.parts[0] == "logs":
            allowed = {Path("logs/README.md"), Path("logs/broker/README.md")}
            if rel not in allowed:
                violations.append(str(rel))
        elif rel.parts and rel.parts[0] == "data":
            allowed = {Path("data/README.md"), Path("data/broker/README.md")}
            if rel not in allowed:
                violations.append(str(rel))
    return sorted(set(violations))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a source tree for generated deployment/runtime artifacts.")
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    violations = check_tree(args.root)
    if violations:
        print("Public-tree check failed. Remove these generated/runtime artifacts:")
        for item in violations:
            print(f"  - {item}")
        return 1
    print("Public-tree check passed: no generated deployment/runtime artifacts found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
