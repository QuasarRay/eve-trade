"""Transparent rule-based failure classification for Eve Trade."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .diagnosis import CLASSIFIER_VERSION


@dataclass(frozen=True)
class FailureClassification:
    failure_family: str
    suspected_services: list[str]
    confidence: float
    evidence: list[str]
    likely_solution_files: list[str]
    likely_next_commands: list[str]
    classifier_version: str = CLASSIFIER_VERSION
    unsupported_diagnoses: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["test.failure_family"] = self.failure_family
        if value["unsupported_diagnoses"] is None:
            value["unsupported_diagnoses"] = []
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
        ("ci",), ("distributed-backend/observability/requirements.txt", "distributed-backend/ci-cd/requirements.txt", ".github/workflows/verify.yaml"),
        ("python -m pip check", "python -m compileall distributed-backend/observability"),
    ),
    _Rule(
        "runtime-networking", (r"connection refused|temporary failure in name resolution|no such host|network.*not found|quilkin",),
        ("encore-backend", "quilkin", "nsqd"), ("distributed-backend/src/gateway/udp.go", "distributed-backend/orchestration/kubernetes/overlay/prod/networkpolicies.yaml"),
        ("kubectl -n eve-trade get pods,svc", "kubectl -n eve-trade logs deploy/encore-backend --tail=200"),
    ),
    _Rule(
        "service-readiness", (r"not ready|healthcheck|timed out waiting|readiness|deadline exceeded",),
        ("encore-backend", "trade-settlement", "nsqd"), ("distributed-backend/src/gateway/service.go", "distributed-backend/src/market/api.go", "distributed-backend/src/settlementworker/service.go"),
        ("kubectl -n eve-trade get pods", "kubectl -n eve-trade logs deploy/encore-backend --tail=200"),
    ),
    _Rule(
        "migration/schema-drift", (r"migration|schema.*(differ|drift|missing)|undefined table|undefined column|does not exist",),
        ("postgres", "trade-settlement"), ("distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql", "distributed-backend/orchestration/kubernetes/base/migrate.yaml"),
        ("python distributed-backend/observability/ci/observed_run.py collect-only", "kubectl -n eve-trade logs job/settlement-db-migrate"),
    ),
    _Rule(
        "generated-code-drift", (r"protobuf|proto.*drift|generated.*differ|buf generate",),
        ("proto", "ci"), ("buf.gen.yaml", "proto"),
        ("buf generate", "git diff -- proto/gen"),
    ),
    _Rule(
        "environment-parity", (r"github actions|works locally|environment|version differs|image digest",),
        ("ci",), (".github/workflows/verify.yaml", "ci-cd/pipeline.py"),
        ("python distributed-backend/observability/ci/compare_runs.py --local <local-run> --ci <ci-run>",),
    ),
    _Rule(
        "idempotency", (r"idempot|duplicate|replay|settlement.*twice|double",),
        ("encore-backend", "market", "trade-settlement"), ("distributed-backend/src/gateway/udp.go", "distributed-backend/src/market/handler.go", "distributed-backend/src/settlementworker/service.go", "distributed-backend/src/trade-settlement/src/executor.rs"),
        ("python -m pytest distributed-backend/tests/e2e -k idempotency -vv -s",),
    ),
    _Rule(
        "cancel-lifecycle", (r"cancel|refund|cancelled",),
        ("market", "trade-settlement"), ("gametrade/cancel_trade_instance.go", "distributed-backend/src/market/handler.go", "distributed-backend/src/trade-settlement/src/operations.rs"),
        ("python -m pytest distributed-backend/tests/e2e -k cancel -vv -s --maxfail=1",),
    ),
    _Rule(
        "rollback", (r"rollback|atomic|partial write|transaction.*failed",),
        ("trade-settlement", "postgres"), ("distributed-backend/src/trade-settlement/src/executor.rs", "distributed-backend/src/trade-settlement/src/operations.rs"),
        ("python -m pytest distributed-backend/tests/e2e -k rollback -vv -s --maxfail=1",),
    ),
    _Rule(
        "client-tampering", (r"tamper|wrong owner|wrong station|canonical|not owned|forged",),
        ("market",), ("distributed-backend/src/market/handler.go", "distributed-backend/src/market/repository.go"),
        ("python -m pytest distributed-backend/tests/e2e -k 'owner or station or tamper' -vv -s",),
    ),
    _Rule(
        "accept-validation", (r"accept.*(zero|negative|quantity)|rejects_zero_quantity|quantity_requested|insufficient isk|buyer and seller",),
        ("market", "trade-settlement"), ("gametrade/accept_trade_instance.go", "distributed-backend/src/market/handler.go", "distributed-backend/src/trade-settlement/src/operations.rs"),
        ("python -m pytest distributed-backend/tests/e2e -k accepting_trade -vv -s --maxfail=1",),
    ),
    _Rule(
        "settlement-invariant", (r"settlement.*invariant|escrow.*mismatch|settlement step|batch_state",),
        ("trade-settlement", "settlementworker"), ("distributed-backend/src/trade-settlement/src/executor.rs", "distributed-backend/src/settlementworker/service.go", "distributed-backend/src/trade-settlement/src/operations.rs"),
        ("cargo test --manifest-path distributed-backend/src/trade-settlement/Cargo.toml",),
    ),
    _Rule(
        "db-invariant", (r"constraint|sqlstate|ledger.*mismatch|projection.*invariant|foreign key",),
        ("postgres", "trade-settlement"), ("distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql", "distributed-backend/src/trade-settlement/src/operations.rs"),
        ("python distributed-backend/observability/ci/observed_run.py collect-only",),
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
    high_priority = _classify_causal_signature(haystack, nodeid=nodeid)
    if high_priority:
        return high_priority
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
            unsupported_diagnoses=[],
        )
    score, rule, matches = best
    confidence = min(0.95, 0.55 + 0.12 * score + (0.08 if nodeid else 0.0))
    evidence = [f"matched /{pattern}/" for pattern in matches]
    if nodeid:
        evidence.insert(0, f"test nodeid: {nodeid}")
    return FailureClassification(rule.family, list(rule.services), confidence, evidence, list(rule.files), list(rule.commands), unsupported_diagnoses=[])


def classification_from_diagnosis(diagnosis: dict[str, Any]) -> FailureClassification:
    primary = diagnosis.get("primary_diagnosis", {})
    dimensions = primary.get("category_dimensions", {})
    stage = str(dimensions.get("stage", "UNKNOWN"))
    mechanism = str(dimensions.get("mechanism", "UNKNOWN"))
    external = str(dimensions.get("external_system", ""))
    family = "/".join(item.lower().replace("_", "-") for item in (stage, mechanism) if item and item != "UNKNOWN") or "unclassified"
    evidence = [str(item) for item in primary.get("supporting_evidence", [])]
    evidence.extend(str(item) for item in primary.get("contradicting_evidence", []))
    next_commands = [
        str(item.get("action", item))
        for item in diagnosis.get("recommendations", [])
    ]
    services = [item for item in [str(dimensions.get("component", "")), external] if item]
    return FailureClassification(
        failure_family=family,
        suspected_services=services,
        confidence=float(primary.get("confidence_score", 0.2) or 0.2),
        evidence=evidence or [str(primary.get("summary", "No structured evidence summary available."))],
        likely_solution_files=[],
        likely_next_commands=next_commands or ["Inspect the structured diagnosis JSON and command log."],
        unsupported_diagnoses=[str(item) for item in primary.get("unsupported_diagnoses", [])],
    )


def _classify_causal_signature(haystack: str, *, nodeid: str) -> FailureClassification | None:
    if "go mod download" in haystack and "unexpected eof" in haystack:
        return FailureClassification(
            failure_family="dependency-resolution/network-transport",
            suspected_services=["go", "proxy.golang.org"],
            confidence=0.82,
            evidence=[
                "observed go mod download",
                "observed unexpected EOF during module fetch",
                "observed proxy.golang.org endpoint",
                "Docker, database, Kubernetes, and application E2E root causes are unsupported without additional causal evidence.",
            ],
            likely_solution_files=["go.mod", "go.sum"],
            likely_next_commands=[
                "retry the exact go mod download",
                "verify GOPROXY and proxy.golang.org reachability",
            ],
            unsupported_diagnoses=["docker-networking", "database", "application E2E bug", "Kubernetes"],
        )
    if _looks_like_stale_path(haystack):
        return FailureClassification(
            failure_family="ci-harness/stale-path",
            suspected_services=["ci-harness"],
            confidence=0.9,
            evidence=["observed missing observability path before product tests could run"],
            likely_solution_files=[".github/workflows/verify.yaml", "distributed-backend/observability/ci/observed_run.py"],
            likely_next_commands=["run the current distributed-backend/observability observed command"],
            unsupported_diagnoses=["Python application bug", "E2E product regression", "Docker networking"],
        )
    if "cannot connect to the docker daemon" in haystack or "is the docker daemon running" in haystack:
        return FailureClassification(
            failure_family="container-runtime/docker-daemon-unavailable",
            suspected_services=["docker"],
            confidence=0.96,
            evidence=["observed direct Docker daemon connectivity error"],
            likely_solution_files=["docker-compose.integration.yml"],
            likely_next_commands=["docker version", "docker info"],
            unsupported_diagnoses=["application E2E bug", "database schema drift"],
        )
    if "connection refused" in haystack and ("postgres" in haystack or ":5432" in haystack or "database_url" in haystack):
        return FailureClassification(
            failure_family="database/network-transport",
            suspected_services=["postgres"],
            confidence=0.88,
            evidence=["observed PostgreSQL TCP connection refusal"],
            likely_solution_files=["distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql"],
            likely_next_commands=["inspect PostgreSQL readiness and configured database URL"],
            unsupported_diagnoses=["docker-networking"],
        )
    if nodeid and ("assert" in haystack or "expected" in haystack):
        return None
    return None


def _looks_like_stale_path(haystack: str) -> bool:
    if not any(marker in haystack for marker in ("no such file or directory", "can't open file", "cannot find the path", "enoent")):
        return False
    return bool(re.search(r"(?:^|[\\/\s])observability[\\/][^\s:'\"]+", haystack))


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
