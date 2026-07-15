"""Environment, repository, toolchain, and drift-hash collection."""

from __future__ import annotations

import hashlib
import json
import os
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
    protobuf = sha256_files(list(root.glob("proto/gen/**/*")), root)
    encore = sha256_files(
        [
            root / "encore.app",
            root / "infra" / "encore" / "self-host.nsq.json",
        ],
        root,
    )
    kubernetes = sha256_files(list((root / "distributed-backend" / "orchestration" / "kubernetes").glob("**/*.yaml")), root)
    metadata = {
        "db.migration_hash": migrations["sha256"],
        "protobuf.generated_hash": protobuf["sha256"],
        "encore.config_hash": encore["sha256"],
        "kubernetes.manifest_hash": kubernetes["sha256"],
    }
    storage.write_json("git.json", git)
    storage.write_json("tool-versions.json", tools)
    storage.write_json("env-redacted.json", env)
    storage.write_json(
        "hashes.json",
        {
            "migrations": migrations,
            "protobuf_generated": protobuf,
            "encore": encore,
            "kubernetes": kubernetes,
            **metadata,
        },
    )
    return {"git": git, "tools": tools, "environment": env, "hashes": metadata}
