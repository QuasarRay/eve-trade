from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eve_trade_hypothesis.catalog import load_catalog
from eve_trade_hypothesis.contracts.nsq_contracts import NSQ_CONTRACTS, validate_nsq_contract


ROOT = Path(__file__).resolve().parents[5]
FILES = (
    ".github/dagger/_images.py",
    ".github/dagger/kubernetes.py",
    "scripts/validate_nsq_configuration.py",
    "scripts/verify_nsq_restart_delivery.py",
    "distributed-backend/src/settlement/work.go",
    "distributed-backend/src/settlementworker/service.go",
    "distributed-backend/src/settlementworker/config.go",
    "distributed-backend/src/settlementworker/client.go",
    "distributed-backend/src/settlementworker/regression_lifecycle_test.go",
    "distributed-backend/src/market/settlement_result.go",
    "distributed-backend/src/trade-settlement/config/app.toml",
    "distributed-backend/orchestration/kubernetes/base/nsq.yaml",
    "distributed-backend/orchestration/kubernetes/overlay/local/nsq-local.yaml",
    "distributed-backend/orchestration/kubernetes/overlay/prod/nsq-client-proxy.yaml",
    "infra/encore/self-host.nsq.json",
    "infra/encore/self-host.local.nsq.json",
)


def _copy_subject(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for relative in FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return root


def _replace(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    source = path.read_text(encoding="utf-8")
    assert old in source, f"hostile mutation subject is missing: {old}"
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def test_nsq_registry_is_exactly_the_authoritative_category() -> None:
    catalog = load_catalog()
    assert set(NSQ_CONTRACTS) == set(catalog["categories"]["77"]["names"])


@pytest.mark.parametrize("name", sorted(NSQ_CONTRACTS))
def test_each_nsq_contract_has_a_passing_exact_oracle(name: str) -> None:
    validate_nsq_contract(name, ROOT)


def test_topic_binding_oracle_rejects_changed_go_topic_name(tmp_path: Path) -> None:
    root = _copy_subject(tmp_path)
    _replace(root, "distributed-backend/src/settlement/work.go", '"settlement-work"', '"wrong-work"')
    with pytest.raises(AssertionError):
        validate_nsq_contract(
            "test_settlement_work_topic_name_matches_market_publisher_and_worker_subscriber_configuration",
            root,
        )


def test_restart_oracle_rejects_memory_queue_or_missing_dagger_execution(tmp_path: Path) -> None:
    root = _copy_subject(tmp_path)
    _replace(root, "distributed-backend/orchestration/kubernetes/base/nsq.yaml", "--mem-queue-size=0", "--mem-queue-size=10000")
    with pytest.raises(AssertionError):
        validate_nsq_contract(
            "test_nsqd_restart_preserves_unacknowledged_correctness_critical_messages_under_configured_storage_mode",
            root,
        )

    root = _copy_subject(tmp_path / "second")
    _replace(
        root,
        ".github/dagger/kubernetes.py",
        "python scripts/verify_nsq_restart_delivery.py --nsqd /usr/local/bin/nsqd",
        "python -c 'print(0)'",
    )
    with pytest.raises(AssertionError):
        validate_nsq_contract(
            "test_nsqd_restart_preserves_unacknowledged_correctness_critical_messages_under_configured_storage_mode",
            root,
        )


def test_retry_oracle_rejects_minimum_above_maximum(tmp_path: Path) -> None:
    root = _copy_subject(tmp_path)
    _replace(
        root,
        "distributed-backend/src/settlementworker/service.go",
        "settlementWorkerMinBackoff  = 2 * time.Second",
        "settlementWorkerMinBackoff  = 3 * time.Minute",
    )
    with pytest.raises(AssertionError):
        validate_nsq_contract(
            "test_message_requeue_delay_is_at_least_configured_minimum_retry_backoff",
            root,
        )


def test_timeout_oracle_rejects_broker_timeout_below_worker_deadline(tmp_path: Path) -> None:
    root = _copy_subject(tmp_path)
    _replace(root, "distributed-backend/orchestration/kubernetes/base/nsq.yaml", "--msg-timeout=60s", "--msg-timeout=5s")
    with pytest.raises(AssertionError):
        validate_nsq_contract(
            "test_nsqd_message_timeout_exceeds_normal_worker_processing_deadline",
            root,
        )


def test_capacity_oracle_rejects_worker_budget_without_connection_reserve(tmp_path: Path) -> None:
    root = _copy_subject(tmp_path)
    _replace(root, "distributed-backend/src/trade-settlement/config/app.toml", "max_connections = 10", "max_connections = 9")
    with pytest.raises(AssertionError):
        validate_nsq_contract(
            "test_worker_max_in_flight_does_not_exceed_processing_capacity_that_preserves_database_lock_slo",
            root,
        )


def test_transport_oracle_rejects_broker_identity_verification_removal(tmp_path: Path) -> None:
    root = _copy_subject(tmp_path)
    _replace(
        root,
        "distributed-backend/orchestration/kubernetes/overlay/prod/nsq-client-proxy.yaml",
        "exact: nsqd.eve-trade.svc.cluster.local",
        "exact: attacker.invalid",
    )
    with pytest.raises(AssertionError):
        validate_nsq_contract(
            "test_nsq_auth_or_tls_configuration_is_consistent_between_publishers_consumers_and_broker_when_enabled",
            root,
        )


def test_ephemeral_rejection_oracle_executes_the_validator_negative_path() -> None:
    # These exact contracts synthesize an invalid backend and require the
    # repository validator to reject it; current-name absence is not the oracle.
    validate_nsq_contract(
        "test_nsqd_configuration_rejects_ephemeral_channel_name_for_settlement_work",
        ROOT,
    )
    validate_nsq_contract(
        "test_nsqd_configuration_rejects_ephemeral_channel_name_for_settlement_results",
        ROOT,
    )
