from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]


def run_contract(command: Sequence[str], *, cwd: Path = ROOT, timeout: int = 180) -> None:
    environment = os.environ.copy()
    environment.setdefault("ENCORERUNTIME_NOPANIC", "1")
    result = subprocess.run(
        list(command),
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        output = "\n".join(
            part for part in (result.stdout.strip(), result.stderr.strip()) if part
        )
        raise AssertionError(
            f"contract command exited {result.returncode}: {' '.join(command)}\n{output}"
        )


def go_test(*arguments: str, race: bool = True) -> None:
    go_arguments = ["test"]
    if race:
        go_arguments.append("-race")
    go_arguments.extend(["-count=1", "-timeout=120s", *arguments])
    if sys.platform == "win32":
        image = os.environ.get("EVE_TRADE_GO_RACE_IMAGE", "golang:1.26.5-bookworm")
        command = [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{ROOT}:/workspace",
            "--workdir",
            "/workspace",
            "--env",
            "ENCORERUNTIME_NOPANIC=1",
            "--env",
            "GOTOOLCHAIN=local",
            "--mount",
            "type=volume,source=eve-trade-go-build-1-26-5,target=/root/.cache/go-build",
            image,
            "sh",
            "-euc",
            (
                'version="$(go version)"; echo "$version"; '
                'case "$version" in "go version go1.26.5 "*) ;; '
                '*) echo "expected go1.26.5 race toolchain" >&2; exit 1 ;; esac; '
                'exec go "$@"'
            ),
            "go",
            *go_arguments,
        ]
        run_contract(command, timeout=300)
        return
    run_contract(["go", *go_arguments])


class RaceAndCrashContracts(unittest.TestCase):
    def test_gateway_replay_cache_full_suite_passes_under_race_detector(self) -> None:
        go_test(
            "./distributed-backend/src/gateway",
            "-run=^TestCanonicalReplayCacheRegressions$",
        )

    def test_gateway_rate_limiter_full_suite_passes_under_race_detector(self) -> None:
        go_test(
            "./distributed-backend/src/gateway",
            "-run=^TestCanonicalRateLimiterRegressions$",
        )

    def test_gateway_udp_listener_lifecycle_passes_under_race_detector(self) -> None:
        go_test(
            "./distributed-backend/src/gateway",
            "-run=^TestCanonicalGatewayListenerRegressions$",
        )

    def test_udp_session_pool_full_suite_passes_under_race_detector(self) -> None:
        # The production pool is Python, so CPython development mode supplies
        # runtime misuse checks while its deterministic thread tests exercise
        # the actual synchronization boundary. Go race checks cover Go pools.
        run_contract(
            [
                sys.executable,
                "-X",
                "dev",
                "manage.py",
                "test",
                "trade_gui.test_udp_regressions.UdpSessionPoolRegressionTests",
                "-v",
                "2",
            ],
            cwd=ROOT / "simulator",
        )

    def test_settlement_worker_state_transitions_pass_under_race_detector(self) -> None:
        go_test(
            "./distributed-backend/src/settlementworker",
            "-run=^(TestCanonicalWorkerDeliveryRegressions|TestCanonicalWorkerLifecycleRegressions)$",
        )

    def test_settlement_result_projection_passes_under_race_detector(self) -> None:
        go_test(
            "./distributed-backend/src/market",
            "-run=^(TestDuplicateSettlementResultProjectionIsHarmless|TestSettlementResultMustMatchDurableTerminalState)$",
        )

    def test_pubsub_duplicate_delivery_passes_under_race_detector(self) -> None:
        go_test(
            "./distributed-backend/src/settlementworker",
            "-run=^(TestHandleSettlementWorkAcknowledgesDuplicateAfterResultPublished|TestHandleSettlementWorkRecoversCrashAfterResultPublication|TestCanonicalWorkerDeliveryRegressions/test_duplicate_worker_delivery_does_not_execute_settlement_concurrently)$",
        )

    def test_market_crash_between_operation_and_publish_is_recovered(self) -> None:
        run_contract(
            [
                "cargo",
                "test",
                "--locked",
                "service::tests::test_outbox_dispatcher_recovers_after_process_crash_before_publish",
                "--",
                "--nocapture",
            ],
            cwd=ROOT / "distributed-backend" / "src" / "trade-settlement",
        )

    def test_worker_crash_after_processing_transition_is_recovered(self) -> None:
        go_test(
            "./distributed-backend/src/settlementworker",
            "-run=^TestCanonicalWorkerDeliveryRegressions/test_stale_processing_operation_is_recovered_after_worker_crash$",
            race=False,
        )

    def test_worker_crash_after_settlement_before_result_publish_is_recovered(self) -> None:
        go_test(
            "./distributed-backend/src/settlementworker",
            "-run=^TestHandleSettlementWorkRecoversCommittedUnpublishedOperationWithoutExecution$",
            race=False,
        )


if __name__ == "__main__":
    unittest.main()
