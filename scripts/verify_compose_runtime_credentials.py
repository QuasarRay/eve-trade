#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse


RUNTIME_DATABASE_SERVICES = {"market", "trade-settlement"}


def username(database_url: str) -> str:
    return urlparse(database_url).username or ""


def verify(path: Path) -> list[str]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    services = document.get("services") or {}
    errors: list[str] = []
    runtime_users: set[str] = set()
    for service_name in RUNTIME_DATABASE_SERVICES:
        service = services.get(service_name) or {}
        database_url = str((service.get("environment") or {}).get("DATABASE_URL") or "")
        user = username(database_url)
        if not database_url:
            errors.append(f"{service_name} has no rendered DATABASE_URL")
        elif user in {"", "postgres"}:
            errors.append(f"{service_name} uses privileged or missing database user {user!r}")
        runtime_users.add(user)

    migrate = services.get("migrate") or {}
    migration_url = str((migrate.get("environment") or {}).get("DATABASE_URL") or "")
    migration_user = username(migration_url)
    if not migration_url or not migration_user:
        errors.append("migration service has no explicit migration DATABASE_URL")
    if migration_user in runtime_users:
        errors.append("migration and runtime services share the same database identity")
    command = migrate.get("command")
    command_text = " ".join(command) if isinstance(command, list) else str(command or "")
    if "verify_upgrade.sh" not in command_text:
        errors.append("migration service does not execute the upgrade/role verification harness")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("compose_json", nargs="+", type=Path)
    args = parser.parse_args()
    errors: list[str] = []
    for path in args.compose_json:
        errors.extend(f"{path}: {error}" for error in verify(path))
    for error in errors:
        print(f"Compose credential policy violation: {error}", file=sys.stderr)
    if errors:
        return 1
    print("rendered Compose runtime credential policies passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
