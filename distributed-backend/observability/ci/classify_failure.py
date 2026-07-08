"""Transparent rule-based failure classification for Eve Trade."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FailureClassification:
    failure_family: str
    suspected_services: list[str]
    confidence: float
    evidence: list[str]
    likely_solution_files: list[str]
    likely_next_commands: list[str]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["test.failure_family"] = self.failure_family
        return value


@dataclass(frozen=True)
class _Rule:
    family: str
    patterns: tuple[str, ...]
    services: tuple[str, ...]
    files: tuple[str, ...]
    commands: tuple[str, ...]


RULES = (
    _Rule(
        "dependency/import-error", (r"modulenotfounderror|importerror|no module named|command not found",),
        ("ci",), ("observability/requirements.txt", "ci-cd/requirements.txt", ".github/workflows/verify.yaml"),
        ("python -m pip check", "python -m compileall observability"),
    ),
    _Rule(
        "runtime-networking", (r"connection refused|temporary failure in name resolution|no such host|network.*not found|quilkin",),
        ("encore-backend", "quilkin", "nsqd"), ("gateway/udp.go", "distributed-backend/orchestration/kubernetes/overlay/prod/networkpolicies.yaml"),
        ("kubectl -n eve-trade get pods,svc", "kubectl -n eve-trade logs deploy/encore-backend --tail=200"),
    ),
    _Rule(
        "service-readiness", (r"not ready|healthcheck|timed out waiting|readiness|deadline exceeded",),
        ("encore-backend", "trade-settlement", "nsqd"), ("gateway/service.go", "market/api.go", "settlementworker/service.go"),
        ("kubectl -n eve-trade get pods", "kubectl -n eve-trade logs deploy/encore-backend --tail=200"),
    ),
    _Rule(
        "migration/schema-drift", (r"migration|schema.*(differ|drift|missing)|undefined table|undefined column|does not exist",),
        ("postgres", "trade-settlement"), ("distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql", "distributed-backend/orchestration/kubernetes/base/migrate.yaml"),
        ("python observability/ci/observed_run.py collect-only", "kubectl -n eve-trade logs job/settlement-db-migrate"),
    ),
    _Rule(
        "generated-code-drift", (r"protobuf|proto.*drift|generated.*differ|buf generate",),
        ("proto", "ci"), ("buf.gen.yaml", "proto"),
        ("buf generate", "git diff -- proto/gen"),
    ),
    _Rule(
        "environment-parity", (r"github actions|works locally|environment|version differs|image digest",),
        ("ci",), (".github/workflows/verify.yaml", "ci-cd/pipeline.py"),
        ("python observability/ci/compare_runs.py --local <local-run> --ci <ci-run>",),
    ),
    _Rule(
        "idempotency", (r"idempot|duplicate|replay|settlement.*twice|double",),
        ("encore-backend", "market", "trade-settlement"), ("gateway/udp.go", "market/handler.go", "settlementworker/service.go", "distributed-backend/src/trade-settlement/src/executor.rs"),
        ("python -m pytest distributed-backend/tests/e2e -k idempotency -vv -s",),
    ),
    _Rule(
        "cancel-lifecycle", (r"cancel|refund|cancelled",),
        ("market", "trade-settlement"), ("internal/gametrade/cancel_trade_instance.go", "market/handler.go", "distributed-backend/src/trade-settlement/src/operations.rs"),
        ("python -m pytest distributed-backend/tests/e2e -k cancel -vv -s --maxfail=1",),
    ),
    _Rule(
        "rollback", (r"rollback|atomic|partial write|transaction.*failed",),
        ("trade-settlement", "postgres"), ("distributed-backend/src/trade-settlement/src/executor.rs", "distributed-backend/src/trade-settlement/src/operations.rs"),
        ("python -m pytest distributed-backend/tests/e2e -k rollback -vv -s --maxfail=1",),
    ),
    _Rule(
        "client-tampering", (r"tamper|wrong owner|wrong station|canonical|not owned|forged",),
        ("market",), ("market/handler.go", "market/repository.go"),
        ("python -m pytest distributed-backend/tests/e2e -k 'owner or station or tamper' -vv -s",),
    ),
    _Rule(
        "accept-validation", (r"accept.*(zero|negative|quantity)|rejects_zero_quantity|quantity_requested|insufficient isk|buyer and seller",),
        ("market", "trade-settlement"), ("internal/gametrade/accept_trade_instance.go", "market/handler.go", "distributed-backend/src/trade-settlement/src/operations.rs"),
        ("python -m pytest distributed-backend/tests/e2e -k accepting_trade -vv -s --maxfail=1",),
    ),
    _Rule(
        "settlement-invariant", (r"settlement.*invariant|escrow.*mismatch|settlement step|batch_state",),
        ("trade-settlement", "settlementworker"), ("distributed-backend/src/trade-settlement/src/executor.rs", "settlementworker/service.go", "distributed-backend/src/trade-settlement/src/operations.rs"),
        ("cargo test --manifest-path distributed-backend/src/trade-settlement/Cargo.toml",),
    ),
    _Rule(
        "db-invariant", (r"constraint|sqlstate|ledger.*mismatch|projection.*invariant|foreign key",),
        ("postgres", "trade-settlement"), ("distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql", "distributed-backend/src/trade-settlement/src/operations.rs"),
        ("python observability/ci/observed_run.py collect-only",),
    ),
)


def classify_failure(
    *,
    nodeid: str = "",
    assertion: str = "",
    logs: str = "",
    db_hints: str = "",
) -> FailureClassification:
    haystack = "\n".join((nodeid, assertion, logs[-30000:], db_hints[-10000:])).lower()
    best: tuple[int, _Rule, list[str]] | None = None
    for rule in RULES:
        matches = [pattern for pattern in rule.patterns if re.search(pattern, haystack, re.IGNORECASE)]
        score = len(matches)
        if score and (best is None or score > best[0]):
            best = (score, rule, matches)
    if best is None:
        return FailureClassification(
            failure_family="unclassified",
            suspected_services=[],
            confidence=0.2,
            evidence=["No transparent classification rule matched the available evidence."],
            likely_solution_files=[],
            likely_next_commands=["Open failure-report.html and inspect the first failing command and service logs."],
        )
    score, rule, matches = best
    confidence = min(0.95, 0.55 + 0.12 * score + (0.08 if nodeid else 0.0))
    evidence = [f"matched /{pattern}/" for pattern in matches]
    if nodeid:
        evidence.insert(0, f"test nodeid: {nodeid}")
    return FailureClassification(rule.family, list(rule.services), confidence, evidence, list(rule.files), list(rule.commands))


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify an Eve Trade failure")
    parser.add_argument("--nodeid", default="")
    parser.add_argument("--assertion", default="")
    parser.add_argument("--logs", type=Path)
    parser.add_argument("--db-hints", default="")
    args = parser.parse_args()
    logs = args.logs.read_text(encoding="utf-8", errors="replace") if args.logs and args.logs.exists() else ""
    print(json.dumps(classify_failure(nodeid=args.nodeid, assertion=args.assertion, logs=logs, db_hints=args.db_hints).to_dict(), indent=2))


if __name__ == "__main__":
    main()
