from __future__ import annotations

import unittest

from observability.ci.observed_run import _is_transient_command_failure
from observability.ci.run_command import CommandResult


def command_result(*, exit_code: int, stderr: str = "", timed_out: bool = False) -> CommandResult:
    return CommandResult(
        name="test-command",
        stage="test",
        argv=["test-command"],
        exit_code=exit_code,
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        duration_ms=1000,
        stdout="",
        stderr=stderr,
        metadata_path="commands/test/command.json",
        log_path="commands/test/command.log",
        timed_out=timed_out,
    )


class TransientCommandFailureTests(unittest.TestCase):
    def test_classifies_network_timeout_as_transient(self) -> None:
        result = command_result(exit_code=1, stderr="net/http: TLS handshake timeout")

        self.assertTrue(_is_transient_command_failure(result))

    def test_classifies_git_tls_termination_as_transient(self) -> None:
        result = command_result(exit_code=1, stderr="gnutls_handshake() failed: The TLS connection was non-properly terminated")

        self.assertTrue(_is_transient_command_failure(result))

    def test_classifies_process_timeout_as_transient(self) -> None:
        self.assertTrue(_is_transient_command_failure(command_result(exit_code=124, timed_out=True)))

    def test_does_not_retry_deterministic_build_failure(self) -> None:
        result = command_result(exit_code=1, stderr="compile error: undefined symbol")

        self.assertFalse(_is_transient_command_failure(result))

    def test_does_not_retry_success(self) -> None:
        self.assertFalse(_is_transient_command_failure(command_result(exit_code=0)))


if __name__ == "__main__":
    unittest.main()
