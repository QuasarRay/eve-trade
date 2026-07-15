#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "distributed-backend" / "src" / "trade-settlement" / "migrations" / "0001_settlement_schema.sql"
RENDER_COPY = ROOT / "distributed-backend" / "orchestration" / "kubernetes" / "base" / "migrations" / "0001_settlement_schema.sql"


def main() -> int:
    canonical = CANONICAL.read_bytes()
    rendered = RENDER_COPY.read_bytes()
    if canonical != rendered:
        print(
            "schema ownership violation: Kubernetes migration copy differs from canonical Rust migration",
            file=sys.stderr,
        )
        return 1
    digest = hashlib.sha256(canonical).hexdigest()
    print(f"canonical settlement schema verified: sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
