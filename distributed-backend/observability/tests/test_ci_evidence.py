from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from observability.ci.ci_evidence import (
    MAX_EXCERPT_CHARS,
    bounded_excerpt,
    canonical_digest,
    finish_evidence,
    load_evidence_directory,
    run_command_evidence,
    sign_evidence,
    start_evidence,
    structured_diagnostics,
    workflow_definition_digest,
)


class CiEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = {
            "GITHUB_REPOSITORY": "QuasarRay/eve-trade",
            "GITHUB_REF": "refs/heads/experimental",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_WORKFLOW": "verify",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_EVENT_NAME": "pull_request",
            "RUNNER_NAME": "runner",
            "RUNNER_OS": "Linux",
        }
        self.expected = {
            "repository": "QuasarRay/eve-trade",
            "branch_ref": "refs/heads/experimental",
            "commit_sha": "a" * 40,
            "workflow": "verify",
            "run_id": "123",
            "run_attempt": "2",
            "workflow_definition_digest": workflow_definition_digest(),
        }

    def _bundle(self, directory: Path) -> Path:
        start = directory / "start.json"
        output = directory / "evidence" / "go.json"
        start_evidence(start, job_id="go", job_name="go / encore", environment=self.environment)
        finish_evidence(
            start,
            output,
            job_id="go",
            job_name="go / encore",
            step_identity="ci-evidence/finalize",
            command_identity="verify/go",
            status="success",
            dependencies=["proto"],
            environment=self.environment,
        )
        return output

    def test_round_trip_validates_digest_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = self._bundle(Path(temp))

            bundles, errors = load_evidence_directory(output.parent, self.expected)

        self.assertEqual(errors, [])
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0]["artifact_digest"], canonical_digest(bundles[0]))

    def test_rejects_stale_sha_wrong_repository_run_and_attempt(self) -> None:
        fields = {
            "commit_sha": "b" * 40,
            "repository": "other/repository",
            "branch_ref": "refs/heads/main",
            "run_id": "999",
            "run_attempt": "3",
        }
        for field, value in fields.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                output = self._bundle(Path(temp))
                bundle = json.loads(output.read_text(encoding="utf-8"))
                bundle[field] = value
                sign_evidence(bundle)
                output.write_text(json.dumps(bundle), encoding="utf-8")

                bundles, errors = load_evidence_directory(output.parent, self.expected)

                self.assertEqual(bundles, [])
                self.assertTrue(any(f"{field} mismatch" in error for error in errors))

    def test_rejects_altered_digest_and_corrupted_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = self._bundle(Path(temp))
            bundle = json.loads(output.read_text(encoding="utf-8"))
            bundle["job_name"] = "altered"
            output.write_text(json.dumps(bundle), encoding="utf-8")
            _, digest_errors = load_evidence_directory(output.parent, self.expected)
            self.assertTrue(any("artifact digest mismatch" in error for error in digest_errors))

            output.write_text("{not json", encoding="utf-8")
            bundles, corrupt_errors = load_evidence_directory(output.parent, self.expected)
            self.assertEqual(bundles, [])
            self.assertTrue(any("corrupted artifact" in error for error in corrupt_errors))

    def test_command_evidence_preserves_failure_and_redacts_bounded_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "commands" / "failure.json"
            exit_code = run_command_evidence(
                output,
                job_id="go",
                job_name="go / encore",
                step_name="Go vulnerability audit",
                command=(
                    "import sys; sys.stderr.write('TOKEN=secret-value\\nVulnerability #1: GO-2026-5856\\n'"
                    "+ '    Found in: crypto/tls@go1.26.4\\n    Fixed in: crypto/tls@go1.26.5\\n'"
                    "+ '    Example traces found:\\n      #1: gateway calls tls.Conn.Write\\n'); raise SystemExit(3)"
                ),
                working_directory=Path(temp),
                environment=self.environment,
                runner=(sys.executable, "-c"),
            )
            record = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 3)
        self.assertEqual(record["exit_code"], 3)
        self.assertNotIn("secret-value", record["stderr_excerpt"])
        self.assertEqual(record["diagnostics"][0]["vulnerability_id"], "GO-2026-5856")
        self.assertEqual(record["diagnostics"][0]["fixed_version"], "go1.26.5")
        self.assertEqual(record["diagnostics"][0]["package"], "crypto/tls")
        self.assertEqual(record["diagnostics"][0]["module"], "standard-library")
        self.assertIn("tls.Conn.Write", record["diagnostics"][0]["call_traces"][0])

    def test_command_evidence_is_bound_into_final_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            commands = root / "commands"
            run_command_evidence(
                commands / "step.json",
                job_id="go",
                job_name="go / encore",
                step_name="Go version",
                command="print('go version go1.26.5 linux/amd64')",
                working_directory=root,
                environment=self.environment,
                runner=(sys.executable, "-c"),
            )
            start = root / "start.json"
            output = root / "evidence.json"
            start_evidence(start, job_id="go", job_name="go / encore", environment=self.environment)
            bundle = finish_evidence(
                start,
                output,
                job_id="go",
                job_name="go / encore",
                step_identity="ci-evidence/finalize",
                command_identity="verify/go",
                status="success",
                dependencies=["proto"],
                commands_path=commands,
                environment=self.environment,
            )

        self.assertEqual(len(bundle["commands"]), 1)
        self.assertIn("go1.26.5", bundle["commands"][0]["stdout_excerpt"])
        self.assertEqual(bundle["artifact_digest"], canonical_digest(bundle))

    def test_excerpts_and_buf_diagnostics_are_bounded(self) -> None:
        excerpt = bounded_excerpt("a" * (MAX_EXCERPT_CHARS * 2))
        diagnostics = structured_diagnostics(
            "Buf format",
            "buf format --diff --exit-code",
            "diff -u proto/x.proto.orig proto/x.proto\n",
            "",
            100,
        )

        self.assertLessEqual(len(excerpt), MAX_EXCERPT_CHARS)
        self.assertEqual(diagnostics, [{"type": "buf_format", "path": "proto/x.proto"}])


if __name__ == "__main__":
    unittest.main()
