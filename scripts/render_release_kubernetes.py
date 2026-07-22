#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

from verify_rendered_kubernetes import image_reference_error, pod_spec


LOCK_SCHEMA = "eve-trade.image-lock/v1"
SHA = re.compile(r"^[0-9a-f]{40}$")
TARGETS = {
    ("Deployment", "encore-backend", "encore-backend"): "encore-backend",
    ("Deployment", "trade-settlement", "trade-settlement"): "trade-settlement",
    ("Deployment", "quilkin", "quilkin"): "quilkin",
}


def load_lock(path: Path, repository: str, sha: str) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != LOCK_SCHEMA:
        raise ValueError(f"image lock must use schema {LOCK_SCHEMA}")
    if value.get("repository") != repository:
        raise ValueError("image lock repository does not match the release repository")
    if not SHA.fullmatch(sha) or value.get("sha") != sha:
        raise ValueError("image lock SHA does not match the exact release SHA")
    images = value.get("images")
    if not isinstance(images, dict) or set(images) != set(TARGETS.values()):
        raise ValueError(f"image lock must contain exactly {sorted(TARGETS.values())}")
    result = {str(name): str(reference) for name, reference in images.items()}
    for name, reference in result.items():
        if reason := image_reference_error(reference):
            raise ValueError(f"image lock entry {name} uses {reason} reference {reference!r}")
        if not reference.startswith("ghcr.io/"):
            raise ValueError(f"image lock entry {name} must use the protected GHCR registry")
    return result


def render(manifest: Path, images: dict[str, str]) -> list[dict[str, Any]]:
    resources = [row for row in yaml.safe_load_all(manifest.read_text(encoding="utf-8")) if isinstance(row, dict)]
    replaced: set[str] = set()
    for resource in resources:
        kind = str(resource.get("kind") or "")
        name = str(resource.get("metadata", {}).get("name") or "")
        spec = pod_spec(resource)
        for container in [*spec.get("initContainers", []), *spec.get("containers", [])]:
            key = TARGETS.get((kind, name, str(container.get("name") or "")))
            if key is None:
                continue
            if key in replaced:
                raise ValueError(f"release manifest contains duplicate target container {key}")
            container["image"] = images[key]
            replaced.add(key)
    missing = set(images) - replaced
    if missing:
        raise ValueError(f"release manifest is missing target containers {sorted(missing)}")
    return resources


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--image-lock", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    images = load_lock(args.image_lock, args.repository, args.sha)
    resources = render(args.manifest, images)
    args.output.write_text(
        yaml.safe_dump_all(resources, explicit_start=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"rendered release manifest for {args.repository}@{args.sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
