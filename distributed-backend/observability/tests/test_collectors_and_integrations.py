from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from observability.ci import collect_db, collect_kubernetes
from observability.ci.collect_pytest import _parse_junit, _parse_text, collect_pytest
from observability.ci.honeycomb_tracer import (
    HoneycombTracer,
    TraceSpan,
    _honeycomb_json_request,
    _otel_attributes,
    _parse_headers,
    ensure_triage_board,
    initialize_tracing,
    record_exception,
    span_for_command,
    span_for_stage,
)
from observability.ci.run_context import RunContext
from observability.ci.sentry_reporter import SentryReporter
from observability.ci.storage import RunStorage
from observability.sentry.sentry_config import environment_name, release_name, traces_sample_rate


def context(root: Path) -> RunContext:
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunContext(
        "run-1",
        run_dir,
        root,
        "2026-01-01T00:00:00+00:00",
        "test",
        github_run_id="123",
        github_run_attempt="2",
        github_workflow="verify",
        github_job="tests",
        github_sha="a" * 40,
        github_ref="refs/heads/experimental",
    )


class CollectorCoverageTests(unittest.TestCase):
    def test_database_collector_handles_unavailable_and_complete_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = context(Path(temporary))
            with patch.dict(os.environ, {}, clear=True), patch.object(collect_db.shutil, "which", return_value=None):
                unavailable = collect_db.collect_db(ctx)
            self.assertFalse(unavailable["available"])

            def fake_psql(_url: str, query: str, _cwd: object, **_kwargs: object) -> tuple[int, str]:
                if "pg_catalog.pg_tables" in query:
                    return 0, "trade_instance\nschema_migrations\n"
                if "information_schema.columns" in query:
                    return 0, "trade_instance|id|uuid|NO\n"
                if "schema_migrations" in query:
                    return 1, "TOKEN=secret migration failed"
                if "json_agg" in query:
                    return 0, "[]"
                if query.startswith("SELECT * FROM \"trade_instance\""):
                    return (1, "snapshot failed") if not _kwargs else (0, "rows")
                return 0, ""

            with (
                patch.dict(os.environ, {"DATABASE_URL": "postgresql://secret@localhost/db"}, clear=True),
                patch.object(collect_db.shutil, "which", return_value="psql"),
                patch.object(collect_db, "_psql", side_effect=fake_psql),
            ):
                metadata = collect_db.collect_db(ctx)
            self.assertTrue(metadata["available"])
            self.assertIn("trade_instance", metadata["tables"])
            self.assertRegex(metadata["db.schema_hash"], r"^[0-9a-f]{64}$")
            self.assertIn("snapshot failed for trade_instance", metadata["errors"])
            self.assertIn("migration snapshot failed for schema_migrations", metadata["errors"])

    def test_psql_and_capture_translate_process_errors(self) -> None:
        completed = SimpleNamespace(returncode=3, stdout="failed")
        with patch("subprocess.run", return_value=completed):
            self.assertEqual(collect_db._psql("url", "select 1", Path("."), tuples=True, csv=True), (3, "failed"))
            self.assertEqual(collect_kubernetes._capture(["kubectl"], Path(".")), (3, "failed"))
        with patch("subprocess.run", side_effect=OSError("missing")):
            self.assertEqual(collect_db._psql("url", "select 1", Path("."))[0], 127)
            self.assertEqual(collect_kubernetes._capture(["kubectl"], Path("."))[0], 127)

    def test_kubernetes_collector_handles_missing_context_pods_and_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = context(Path(temporary))
            with patch.object(collect_kubernetes.shutil, "which", return_value=None):
                self.assertIn("not found", collect_kubernetes.collect_kubernetes(ctx)["error"])

            with (
                patch.object(collect_kubernetes.shutil, "which", return_value="kubectl"),
                patch.object(collect_kubernetes, "_capture", return_value=(1, "no context")),
            ):
                self.assertIn("no configured", collect_kubernetes.collect_kubernetes(ctx)["error"])

            pod_json = json.dumps(
                {"items": [{"metadata": {"name": "gateway-1"}, "spec": {"containers": [{"name": "gateway", "image": "example/gateway@sha256:abc"}]}}]}
            )

            def capture(argv: list[str], _cwd: Path, **_kwargs: object) -> tuple[int, str]:
                joined = " ".join(argv)
                if joined.endswith("config current-context"):
                    return 0, "kind-eve\n"
                if joined.endswith("get pods -o name"):
                    return 0, "pod/gateway-1\n"
                if joined.endswith("get pods -o json"):
                    return 0, pod_json
                return 0, "ok"

            with (
                patch.object(collect_kubernetes.shutil, "which", return_value="kubectl"),
                patch.object(collect_kubernetes, "_capture", side_effect=capture),
            ):
                metadata = collect_kubernetes.collect_kubernetes(ctx)
            self.assertEqual(metadata["current_context"], "kind-eve")
            self.assertEqual(metadata["pods"][0]["kubernetes.pod.name"], "gateway-1")
            self.assertEqual(metadata["containers"][0]["kubernetes.container.name"], "gateway")

            with (
                patch.object(collect_kubernetes.shutil, "which", return_value="kubectl"),
                patch.object(collect_kubernetes, "_capture", side_effect=lambda argv, _cwd, **_kwargs: (0, "{" if argv[-1] == "json" else "kind\n")),
            ):
                invalid = collect_kubernetes.collect_kubernetes(ctx)
            self.assertEqual(invalid["commands"]["pods-metadata.json"], 1)

    def test_pytest_collector_merges_junit_text_and_handles_malformed_xml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ctx = context(root)
            junit = root / "junit.xml"
            junit.write_text(
                '<testsuite tests="2" failures="1" errors="0" skipped="0" time="1.5">'
                '<testcase classname="tests.test_api" name="test_ok" file="tests/test_api.py" line="4" />'
                '<testcase classname="tests.test_api" name="test_bad" file="tests/test_api.py" line="9">'
                '<failure message="expected 1">AssertionError: 0 != 1</failure></testcase></testsuite>',
                encoding="utf-8",
            )
            output = "collected 2 items\ntests/test_api.py::test_ok PASSED\ntests/test_api.py::test_bad FAILED\nFAILED tests/test_api.py::test_bad - expected 1\n= 1 passed, 1 failed in 1.5s =\n"
            summary = collect_pytest(ctx, output, junit_path=junit)
            self.assertEqual(summary.failed_count, 1)
            self.assertEqual(summary.passed_count, 1)
            self.assertEqual(summary.source_line, 9)
            self.assertEqual(len(summary.collected_tests), 2)
            self.assertTrue((ctx.run_dir / "pytest/pytest-junit.xml").exists())

            junit.write_text("<broken", encoding="utf-8")
            self.assertEqual(_parse_junit(junit), {})
            self.assertEqual(_parse_junit(root / "missing.xml"), {})
            parsed = _parse_text("sample.py:12: AssertionError\nE assert False\n\ncollected 1 item\n")
            self.assertEqual(parsed["source_line"], 12)


class StorageAndTelemetryCoverageTests(unittest.TestCase):
    def test_storage_bundle_paths_upload_and_strict_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = RunStorage(root / "run")
            storage.write_json("a/data.json", {"ok": True})
            source = root / "source.txt"
            source.write_text("value", encoding="utf-8")
            self.assertEqual(storage.copy(source, "copied.txt").read_text(encoding="utf-8"), "value")
            self.assertTrue(storage.bundle().is_file())
            with self.assertRaises(ValueError):
                storage.path("../escape")
            with patch.dict(os.environ, {"OBS_STORAGE_BACKEND": "unsupported"}, clear=True):
                self.assertIsNone(storage.upload_optional())
            with patch.dict(os.environ, {"OBS_STORAGE_BACKEND": "s3"}, clear=True):
                self.assertIsNone(storage.upload_optional())
            strict = RunStorage(root / "strict", strict=True)
            with self.assertRaises(RuntimeError):
                strict._fail_or_none("TOKEN=secret")

            fake_client = MagicMock()
            fake_boto3 = SimpleNamespace(client=lambda *_args, **_kwargs: fake_client)
            with (
                patch.dict(sys.modules, {"boto3": fake_boto3}),
                patch.dict(os.environ, {"OBS_STORAGE_BACKEND": "s3", "OBS_S3_BUCKET": "bucket", "OBS_S3_PREFIX": "prefix"}, clear=True),
            ):
                location = storage.upload_optional(storage.bundle(root / "artifact.zip"))
            self.assertEqual(location, "s3://bucket/prefix/artifact.zip")
            fake_client.upload_file.assert_called_once()

    def test_sentry_config_handles_environment_release_and_invalid_sampling(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(environment_name(), "local")
            self.assertIsNone(release_name())
            self.assertEqual(traces_sample_rate(), 0.1)
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_SHA": "abc", "SENTRY_TRACES_SAMPLE_RATE": "bad"}, clear=True):
            self.assertEqual(environment_name(), "github-actions")
            self.assertEqual(release_name(), "abc")
            self.assertEqual(traces_sample_rate(), 0.1)

    def test_trace_span_records_attributes_exceptions_and_shutdown_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = context(Path(temporary))
            tracer = HoneycombTracer(ctx, enabled=False)
            with tracer.span("stage", {"TOKEN": "secret", "nested": {"a": 1}}) as span:
                span.set_attribute("count", 2)
                span.record_exception(ValueError("TOKEN=secret"))
            record = json.loads(tracer.span_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["name"], "stage")
            self.assertEqual(record["exceptions"][0]["type"], "ValueError")

            fake_span = MagicMock()
            fake_span.get_span_context.return_value = SimpleNamespace(trace_id=15)
            fake_context = MagicMock()
            fake_context.__enter__.return_value = fake_span
            tracer.otel_tracer = MagicMock()
            tracer.otel_tracer.start_as_current_span.return_value = fake_context
            with self.assertRaises(RuntimeError):
                with TraceSpan(tracer, "otel", {}):
                    raise RuntimeError("failed")
            self.assertEqual(fake_span.get_span_context().trace_id, 15)

            tracer.provider = MagicMock()
            tracer.provider.shutdown.side_effect = RuntimeError("shutdown")
            tracer.shutdown()
            self.assertTrue((ctx.run_dir / "telemetry/shutdown-error.txt").exists())
            tracer.strict = True
            with self.assertRaises(RuntimeError):
                tracer.shutdown()

    def test_global_span_helpers_headers_attributes_and_board_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = context(Path(temporary))
            with patch("observability.ci.honeycomb_tracer._ACTIVE_TRACER", None):
                with span_for_stage("none") as span:
                    self.assertIsNone(span)
                with span_for_command("none") as span:
                    self.assertIsNone(span)
                record_exception(None, RuntimeError("ignored"))
            tracer = initialize_tracing(ctx, enabled=False)
            with span_for_stage("build") as stage_span:
                self.assertIsNotNone(stage_span)
            with span_for_command("test") as command_span:
                self.assertIsNotNone(command_span)
            self.assertEqual(_parse_headers("a=1, blank, b = two"), {"a": "1", "b": "two"})
            self.assertEqual(_otel_attributes({"a": 1, "b": ["x"], "c": {"x": 1}})["c"], '{"x": 1}')

            with patch.dict(os.environ, {}, clear=True):
                self.assertFalse(ensure_triage_board(ctx)["configured"])
            with (
                patch.dict(os.environ, {"HONEYCOMB_CONFIGURATION_KEY": "key"}, clear=True),
                patch("observability.ci.honeycomb_tracer._honeycomb_json_request", return_value=[{"id": "board-1", "name": "Eve Trade CI/CD Failure Triage"}]),
            ):
                self.assertEqual(ensure_triage_board(ctx)["action"], "existing")
            with (
                patch.dict(os.environ, {"HONEYCOMB_CONFIGURATION_KEY": "key"}, clear=True),
                patch("observability.ci.honeycomb_tracer._honeycomb_json_request", side_effect=[[], {"id": "board-2"}]),
            ):
                self.assertEqual(ensure_triage_board(ctx)["action"], "created")
            with (
                patch.dict(os.environ, {"HONEYCOMB_CONFIGURATION_KEY": "key"}, clear=True),
                patch("observability.ci.honeycomb_tracer._honeycomb_json_request", side_effect=OSError("offline")),
            ):
                self.assertFalse(ensure_triage_board(ctx)["configured"])
                with self.assertRaises(OSError):
                    ensure_triage_board(ctx, strict=True)

    def test_honeycomb_json_request_supports_get_and_post(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual(_honeycomb_json_request("https://example.test", headers={}), {"ok": True})
            self.assertEqual(_honeycomb_json_request("https://example.test", headers={}, payload={"x": 1}), {"ok": True})
        self.assertEqual(urlopen.call_count, 2)


class SentryCoverageTests(unittest.TestCase):
    def test_reporter_noops_without_sdk_and_exercises_sdk_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = context(Path(temporary))
            with patch.dict(os.environ, {}, clear=True):
                reporter = SentryReporter(ctx)
            reporter.breadcrumb("ignored")
            self.assertEqual(reporter.capture_command_failure(command_name="go", argv=["go", "test"], exit_code=1, stage="test", artifact_path="x"), "")
            self.assertFalse(reporter.configure_release_cli()["configured"])
            self.assertFalse(reporter.run_optional_autofix_hook()["invoked"])

            scope = MagicMock()
            scope_context = MagicMock()
            scope_context.__enter__.return_value = scope
            sdk = MagicMock()
            sdk.push_scope.return_value = scope_context
            sdk.capture_message.return_value = "event-1"
            reporter.sdk = sdk
            reporter.breadcrumb("TOKEN=secret", data={"password": "hidden"})
            event_id = reporter.capture_command_failure(
                command_name="go-test",
                argv=["go", "test", "TOKEN=secret"],
                exit_code=1,
                stage="test",
                artifact_path="commands/go.log",
                failed_test_nodeid="pkg.TestFailure",
                source_links=["https://example.test/source"],
            )
            self.assertEqual(event_id, "event-1")
            self.assertEqual(reporter.capture_command_failure(command_name="again", argv=[], exit_code=1, stage="test", artifact_path="x"), "event-1")
            reporter.flush()
            sdk.flush.assert_called_once()

    def test_release_and_autofix_commands_handle_success_failure_and_strictness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ctx = context(Path(temporary))
            environment = {
                "SENTRY_DSN": "dsn",
                "SENTRY_AUTH_TOKEN": "token",
                "SENTRY_ORG": "org",
                "SENTRY_PROJECT": "project",
                "GITHUB_SHA": "abc123",
                "SENTRY_AUTOFIX_COMMAND": "autofix --event",
            }
            with patch.dict(os.environ, environment, clear=True), patch.object(SentryReporter, "_initialize"):
                reporter = SentryReporter(ctx)
                reporter.event_id = "event-1"
                completed = SimpleNamespace(returncode=0, stdout="ok")
                with patch("shutil.which", return_value="sentry-cli"), patch("subprocess.run", return_value=completed):
                    self.assertTrue(reporter.configure_release_cli()["configured"])
                    self.assertTrue(reporter.run_optional_autofix_hook()["invoked"])

                reporter.strict = True
                failed = SimpleNamespace(returncode=1, stdout="failed")
                with patch("shutil.which", return_value="sentry-cli"), patch("subprocess.run", return_value=failed):
                    with self.assertRaises(RuntimeError):
                        reporter.configure_release_cli()
                    with self.assertRaises(RuntimeError):
                        reporter.run_optional_autofix_hook()

                reporter.strict = False
                with patch("subprocess.run", side_effect=OSError("missing")):
                    self.assertIn("error", reporter.run_optional_autofix_hook())


if __name__ == "__main__":
    unittest.main()
