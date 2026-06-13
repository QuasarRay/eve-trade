from __future__ import annotations

from pathlib import Path


E2E_ROOT = Path(__file__).resolve().parents[1]
DISTRIBUTED_BACKEND = E2E_ROOT.parents[1]
REPO_ROOT = DISTRIBUTED_BACKEND.parent
PROTO_ROOT = DISTRIBUTED_BACKEND / "proto"
POSTGRES_MIGRATIONS = DISTRIBUTED_BACKEND / "migrations" / "postgresql"
