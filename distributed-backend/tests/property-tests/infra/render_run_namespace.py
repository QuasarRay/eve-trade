#!/usr/bin/env python3
"""Retarget rendered local Kustomize resources to one disposable run namespace."""
from __future__ import annotations

import argparse
import re
import sys

import yaml


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", args.namespace):
        raise SystemExit("namespace must be a DNS-safe label")
    documents = [document for document in yaml.safe_load_all(sys.stdin) if document]
    if not documents:
        raise SystemExit("Kustomize produced no resources")
    namespace_resources = 0
    for document in documents:
        metadata = document.setdefault("metadata", {})
        if document.get("kind") == "Namespace" and metadata.get("name") == "eve-trade":
            metadata["name"] = args.namespace
            namespace_resources += 1
        elif metadata.get("namespace") == "eve-trade":
            metadata["namespace"] = args.namespace
    if namespace_resources != 1:
        raise SystemExit(f"expected one eve-trade Namespace resource, found {namespace_resources}")
    yaml.safe_dump_all(documents, sys.stdout, sort_keys=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
