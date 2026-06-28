"""Environment, repository, toolchain, and drift-hash collection."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .redaction import redact_mapping
from .run_context import RunContext, collect_git, collect_tool_versions
from .storage import RunStorage


def sha256_files(paths: list[Path], root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    included: list[str] = []
    for path in sorted({item.resolve() for item in paths if item.is_file()}):
        relative = path.relative_to(root.resolve()).as_posix()
        included.append(relative)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {"sha256": digest.hexdigest(), "files": included, "file_count": len(included)}


def collect_environment(context: RunContext, storage: RunStorage | None = None) -> dict[str, Any]:
    storage = storage or RunStorage(context.run_dir)
    root = context.repo_root
    git = collect_git(root)
    tools = collect_tool_versions(root)
    env = redact_mapping(dict(os.environ))
    migrations = sha256_files(list(root.glob("**/migrations/*.sql")), root)
    protobuf = sha256_files(list(root.glob("distributed-backend/proto/gen/**/*")), root)
    compose = _compose_hash(root)
    metadata = {
        "db.migration_hash": migrations["sha256"],
        "protobuf.generated_hash": protobuf["sha256"],
        "docker.compose_config_hash": compose.get("sha256", ""),
    }
    storage.write_json("git.json", git)
    storage.write_json("tool-versions.json", tools)
    storage.write_json("env-redacted.json", env)
    storage.write_json("hashes.json", {"migrations": migrations, "protobuf_generated": protobuf, "compose": compose, **metadata})
    return {"git": git, "tools": tools, "environment": env, "hashes": metadata}


def _compose_hash(root: Path) -> dict[str, Any]:
    files = [name for name in ("compose.yaml", "docker-compose.yml", "docker-compose.integration.yml") if (root / name).exists()]
    if not files:
        return {"sha256": "", "files": [], "error": "no Compose file found"}
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", files[0], "config"],
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            return {"sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(), "files": [files[0]]}
        return {"sha256": "", "files": [files[0]], "error": result.stdout.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"sha256": "", "files": [files[0]], "error": str(exc)}
