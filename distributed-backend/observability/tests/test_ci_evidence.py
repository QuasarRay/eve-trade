from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from observability.ci.ci_evidence import (
    canonical_digest,
    finish_evidence,
    load_evidence_directory,
    start_evidence,
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
                bundle["artifact_digest"] = canonical_digest(bundle)
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


if __name__ == "__main__":
    unittest.main()
