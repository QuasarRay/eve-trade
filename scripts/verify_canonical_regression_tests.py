#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "regression" / "canonical_tests.json"
GO_NAME_RE = re.compile(r'"(test_[A-Za-z0-9_]+)"')
RUST_NAME_RE = re.compile(r"(?m)^\s*(?:async\s+)?fn\s+(test_[A-Za-z0-9_]+)\s*\(")
SKIP_RE = re.compile(
    r"(?i)(?:---\s+SKIP:|\bskipped\b|\bpending\b|\bquarantined\b|"
    r"\bxfail(?:ed)?\b|\bexpected failure\b|\bdeselected\b|\bnot[ -]run\b|\btodo\b)"
)


def manifest_names(manifest: dict[str, object]) -> list[str]:
    groups = manifest.get("groups")
    if not isinstance(groups, list):
        raise ValueError("manifest groups must be a list")
    names: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("each manifest group must be an object")
        required = {
            "id",
            "source",
            "level",
            "boundary",
            "expected_failure_at_audited_sha",
            "command",
            "names",
        }
        missing = required.difference(group)
        if missing:
            raise ValueError(f"group {group.get('id')!r} lacks {sorted(missing)}")
        group_names = group["names"]
        if not isinstance(group_names, list) or not group_names:
            raise ValueError(f"group {group['id']!r} names must be a non-empty list")
        names.extend(str(name) for name in group_names)
    return names


def python_test_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def source_test_names(path: Path) -> list[str]:
    if not path.exists():
        return []
    if path.suffix == ".py":
        return python_test_names(path)
    if path.suffix == ".go":
        # Go commonly declares table-driven subtest names in struct literals and
        # passes each value to t.Run. Canonical string literals remain statically
        # auditable while allowing that normal test organization.
        return GO_NAME_RE.findall(path.read_text(encoding="utf-8"))
    if path.suffix == ".rs":
        return RUST_NAME_RE.findall(path.read_text(encoding="utf-8"))
    raise ValueError(f"unsupported canonical test source: {path}")


def output_names(paths: Iterable[Path], expected: set[str]) -> tuple[set[str], set[str]]:
    observed: set[str] = set()
    skipped: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            found = expected.intersection(re.findall(r"test_[A-Za-z0-9_]+", line))
            observed.update(found)
            if found and SKIP_RE.search(line):
                skipped.update(found)
    return observed, skipped


def verify(outputs: list[Path], inventory_only: bool) -> list[str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = manifest_names(manifest)
    errors: list[str] = []
    counts = Counter(expected)
    duplicates = sorted(name for name, count in counts.items() if count != 1)
    if duplicates:
        errors.append(f"manifest duplicate canonical names: {duplicates}")
    if len(expected) != 200:
        errors.append(f"manifest contains {len(expected)} names; expected exactly 200")
    expected_digest = manifest.get("canonical_names_sha256")
    actual_digest = hashlib.sha256(("\n".join(expected) + "\n").encode("utf-8")).hexdigest()
    if expected_digest != actual_digest:
        errors.append(
            "manifest canonical-name digest mismatch: "
            f"got {actual_digest}, expected {expected_digest}"
        )

    definitions: Counter[str] = Counter()
    for group in manifest["groups"]:
        path = ROOT / group["source"]
        if not path.is_file():
            errors.append(f"canonical source is missing: {group['source']}")
            continue
        for name in source_test_names(path):
            if name in counts:
                definitions[name] += 1
    missing = sorted(name for name in expected if definitions[name] == 0)
    duplicate_definitions = sorted(name for name in expected if definitions[name] > 1)
    if missing:
        errors.append(f"canonical tests missing executable definitions ({len(missing)}): {missing}")
    if duplicate_definitions:
        errors.append(f"canonical tests have duplicate executable definitions: {duplicate_definitions}")

    if not inventory_only:
        if not outputs:
            errors.append("test-runner output is required (pass one or more --output paths)")
        else:
            observed, skipped = output_names(outputs, set(expected))
            absent = sorted(set(expected).difference(observed))
            if absent:
                errors.append(f"canonical names absent from test-runner output ({len(absent)}): {absent}")
            if skipped:
                errors.append(f"canonical tests were skipped/pending/quarantined: {sorted(skipped)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the exact 200-test regression inventory and runner output")
    parser.add_argument("--inventory-only", action="store_true", help="validate definitions without accepting this as execution proof")
    parser.add_argument("--output", action="append", default=[], type=Path, help="test-runner output; repeat for each runner")
    args = parser.parse_args()
    errors = verify(args.output, args.inventory_only)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    mode = "inventory" if args.inventory_only else "inventory and execution"
    print(f"canonical regression {mode} verified: 200 unique tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
