#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml


DIGEST_IMAGE = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job"}


def nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def pod_spec(resource: dict[str, Any]) -> dict[str, Any]:
    if resource.get("kind") == "CronJob":
        return nested(resource, "spec", "jobTemplate", "spec", "template", "spec") or {}
    return nested(resource, "spec", "template", "spec") or {}


def secret_references(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"secretRef", "secretKeyRef"} and isinstance(child, dict) and child.get("name"):
                found.add(str(child["name"]))
            found.update(secret_references(child))
    elif isinstance(value, list):
        for child in value:
            found.update(secret_references(child))
    return found


def verify(path: Path) -> list[str]:
    resources = [row for row in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(row, dict)]
    errors: list[str] = []
    by_identity = {
        (str(row.get("kind")), str(nested(row, "metadata", "name") or "")): row
        for row in resources
    }

    for resource in resources:
        kind = str(resource.get("kind") or "")
        name = str(nested(resource, "metadata", "name") or "")
        labels = nested(resource, "metadata", "labels") or {}
        if name == "simulator" or labels.get("app.kubernetes.io/name") == "simulator":
            errors.append(f"production render contains simulator resource {kind}/{name}")
        if kind in WORKLOAD_KINDS or kind == "CronJob":
            containers = [*pod_spec(resource).get("initContainers", []), *pod_spec(resource).get("containers", [])]
            if not containers:
                errors.append(f"workload {kind}/{name} has no containers")
            for container in containers:
                image = str(container.get("image") or "")
                if not DIGEST_IMAGE.fullmatch(image):
                    errors.append(f"workload {kind}/{name} container {container.get('name')} uses mutable or invalid image {image!r}")

    required_secret_contracts = {
        ("Deployment", "encore-backend"): {"gateway-edge-auth", "market-database"},
        ("Deployment", "trade-settlement"): {"trade-settlement-database"},
        ("Job", "settlement-db-migrate"): {"trade-settlement-migration-database"},
    }
    for identity, required in required_secret_contracts.items():
        resource = by_identity.get(identity)
        if resource is None:
            errors.append(f"production render is missing {identity[0]}/{identity[1]}")
            continue
        actual = secret_references(pod_spec(resource))
        missing = required - actual
        if missing:
            errors.append(f"{identity[0]}/{identity[1]} is missing secret references {sorted(missing)}")
        if identity == ("Job", "settlement-db-migrate") and "trade-settlement-database" in actual:
            errors.append("migration job uses the runtime database credential")
        if identity == ("Deployment", "encore-backend") and "trade-settlement-database" in actual:
            errors.append("encore-backend uses the settlement writer database credential")
        if identity[0] == "Deployment" and "trade-settlement-migration-database" in actual:
            errors.append(f"runtime workload {identity[1]} uses the migration database credential")

    for (kind, name), resource in by_identity.items():
        if kind != "Service" or name == "quilkin":
            continue
        service_type = str(nested(resource, "spec", "type") or "ClusterIP")
        if service_type in {"NodePort", "LoadBalancer", "ExternalName"}:
            errors.append(f"internal service {name} is exposed as {service_type}")

    default_deny = by_identity.get(("NetworkPolicy", "default-deny"))
    policy_types = set(nested(default_deny or {}, "spec", "policyTypes") or [])
    if policy_types != {"Ingress", "Egress"}:
        errors.append("production default-deny NetworkPolicy must deny both ingress and egress")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    errors = verify(args.manifest)
    for error in errors:
        print(f"rendered Kubernetes policy violation: {error}", file=sys.stderr)
    if errors:
        return 1
    print("rendered Kubernetes reliability policies passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
