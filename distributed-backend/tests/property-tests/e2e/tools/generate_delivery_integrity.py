#!/usr/bin/env python3
"""Generate a reproducible digest inventory for the checked-in E2E suite."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


E2E_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = E2E_ROOT / "DELIVERY_INTEGRITY.json"
EXCLUDED_PARTS = {
    ".hypothesis",
    ".pytest_cache",
    "__pycache__",
}


def generate() -> str:
    records: list[dict[str, object]] = []
    for path in sorted(E2E_ROOT.rglob("*")):
        if not path.is_file() or path == OUTPUT:
            continue
        relative = path.relative_to(E2E_ROOT)
        if EXCLUDED_PARTS.intersection(relative.parts) or path.suffix == ".pyc":
            continue
        content = path.read_bytes()
        records.append(
            {
                "bytes": len(content),
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    document = {
        "schema_version": "eve-trade.delivery-integrity/v2",
        "root": "distributed-backend/tests/property-tests/e2e",
        "file_count_before_integrity_manifest": len(records),
        "files": records,
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = generate()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit(f"stale delivery integrity manifest: {OUTPUT}")
    else:
        OUTPUT.write_text(expected, encoding="utf-8")
        print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
