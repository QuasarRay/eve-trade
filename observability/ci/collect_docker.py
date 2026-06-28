"""Best-effort Docker and Compose evidence collector."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .redaction import redact_text
from .run_context import RunContext
from .storage import RunStorage


def detect_compose_file(root: Path, *, prefer_integration: bool = False) -> Path | None:
    names = (
        ["docker-compose.integration.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"]
        if prefer_integration
        else ["compose.yaml", "docker-compose.yml", "docker-compose.yaml", "docker-compose.integration.yml"]
    )
    return next((root / name for name in names if (root / name).exists()), None)


def compose_prefix(compose_file: Path | None, *, profile_test: bool = False) -> list[str]:
    result = ["docker", "compose"]
    if compose_file:
        result.extend(["-f", str(compose_file)])
    if profile_test:
        result.extend(["--profile", "test"])
    return result


def compose_services(context: RunContext, compose_file: Path | None, *, profile_test: bool = False) -> list[str]:
    if compose_file is None or not shutil.which("docker"):
        return []
    result = _capture([*compose_prefix(compose_file, profile_test=profile_test), "config", "--services"], context.repo_root)
    return [line.strip() for line in result[1].splitlines() if line.strip()] if result[0] == 0 else []


def collect_docker(
    context: RunContext,
    storage: RunStorage | None = None,
    *,
    compose_file: Path | None = None,
    profile_test: bool = False,
) -> dict[str, Any]:
    storage = storage or RunStorage(context.run_dir)
    compose_file = compose_file or detect_compose_file(context.repo_root)
    docker_dir = Path("docker")
    if not shutil.which("docker"):
        metadata = {"available": False, "error": "docker executable not found"}
        storage.write_json(docker_dir / "metadata.json", metadata)
        return metadata
    prefix = compose_prefix(compose_file, profile_test=profile_test)
    services = compose_services(context, compose_file, profile_test=profile_test)
    commands: dict[str, list[str]] = {
        "docker-version.txt": ["docker", "version"],
        "compose-version.txt": ["docker", "compose", "version"],
        "compose-ps.txt": [*prefix, "ps", "-a"],
        "compose-config.yaml": [*prefix, "config"],
        "compose-logs.txt": [*prefix, "logs", "--no-color", "--timestamps"],
        "compose-images.jsonl": [*prefix, "images", "--format", "json"],
    }
    statuses: dict[str, Any] = {}
    image_digests: list[dict[str, str]] = []
    for filename, argv in commands.items():
        code, output = _capture(argv, context.repo_root, timeout=120)
        safe = redact_text(output)
        storage.write_text(docker_dir / filename, safe)
        statuses[filename] = {"exit_code": code, "command": argv[:3]}
        if filename.endswith("jsonl") and code == 0:
            image_digests.extend(_parse_images(output, services))
    service_logs: dict[str, str] = {}
    for service in services:
        filename = f"docker/logs/{_safe_name(service)}.log"
        code, output = _capture([*prefix, "logs", "--no-color", "--timestamps", "--tail", "5000", service], context.repo_root, timeout=90)
        storage.write_text(filename, redact_text(output))
        statuses[filename] = {"exit_code": code, "service": service}
        service_logs[service] = filename
    config_path = storage.path(docker_dir / "compose-config.yaml")
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.exists() else ""
    metadata = {
        "available": True,
        "compose_file": str(compose_file) if compose_file else "",
        "docker.compose_config_hash": config_hash,
        "images": image_digests,
        "commands": statuses,
        "services": services,
        "service_logs": service_logs,
        "service_urls": _extract_service_urls(storage.path(docker_dir / "compose-config.yaml").read_text(encoding="utf-8", errors="replace")),
    }
    storage.write_json(docker_dir / "metadata.json", metadata)
    return metadata


def _capture(argv: list[str], cwd: Path, *, timeout: float = 30) -> tuple[int, str]:
    try:
        result = subprocess.run(argv, cwd=cwd, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}: {exc}\n"


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "-" for character in value).strip("-") or "service"


def _parse_images(output: str, services: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, Any]] = []
    try:
        value = json.loads(output)
        rows = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    except json.JSONDecodeError:
        for line in output.splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except json.JSONDecodeError:
                continue
    result = []
    for row in rows:
        service = str(row.get("Service", "")) or _service_from_container(str(row.get("ContainerName", "")), services)
        if not service:
            continue
        result.append(
            {
                "service": service,
                "image": str(row.get("Repository", row.get("Image", ""))),
                "tag": str(row.get("Tag", "")),
                "id": str(row.get("ID", "")),
                "docker.compose_service": service,
                "docker.image_digest": str(row.get("ID", "")),
            }
        )
    return result


def _service_from_container(container_name: str, services: list[str]) -> str:
    framed = f"-{container_name}-"
    matches = [service for service in services if f"-{service}-" in framed]
    return max(matches, key=len) if matches else ""


def _extract_service_urls(config: str) -> dict[str, str]:
    result: dict[str, str] = {}
    service = ""
    in_services = False
    in_environment = False
    for line in config.splitlines():
        if line == "services:":
            in_services = True
            continue
        if in_services and line and not line.startswith(" "):
            break
        service_match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if service_match:
            service = service_match.group(1)
            in_environment = False
            continue
        if re.match(r"^    environment:\s*$", line):
            in_environment = True
            continue
        if in_environment:
            value_match = re.match(r"^      ([A-Za-z0-9_]*(?:URL|ENDPOINT)):\s*(.+?)\s*$", line)
            if value_match and service:
                result[f"{service}.{value_match.group(1)}"] = value_match.group(2).strip('"')
            elif line.strip() and not line.startswith("      "):
                in_environment = False
    return result
