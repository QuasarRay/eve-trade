"""Best-effort concise Sentry issue reporting and release hooks."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from observability.sentry.sentry_config import environment_name, release_name, traces_sample_rate

from .links import sentry_event_url
from .redaction import redact_mapping, redact_text, safe_argv
from .run_context import RunContext
from .storage import RunStorage


class SentryReporter:
    def __init__(self, context: RunContext, *, enabled: bool = True, strict: bool = False) -> None:
        self.context = context
        self.enabled = enabled and bool(os.getenv("SENTRY_DSN"))
        self.strict = strict
        self.storage = RunStorage(context.run_dir, strict=strict)
        self.sdk: Any = None
        self.event_id = ""
        self.initialization_error = ""
        if self.enabled:
            self._initialize()

    def breadcrumb(self, message: str, *, category: str = "pipeline", data: dict[str, Any] | None = None, level: str = "info") -> None:
        if self.sdk is None:
            return
        try:
            self.sdk.add_breadcrumb(category=category, message=redact_text(message), data=redact_mapping(data or {}), level=level)
        except Exception as exc:
            self._record_error("breadcrumb", exc)

    def capture_command_failure(
        self,
        *,
        command_name: str,
        argv: list[str],
        exit_code: int,
        stage: str,
        artifact_path: str,
        failure_family: str = "unclassified",
        failed_test_nodeid: str = "",
        source_links: list[str] | None = None,
        honeycomb_trace_url: str = "",
        github_actions_url: str = "",
    ) -> str:
        if self.sdk is None or self.event_id:
            return self.event_id
        try:
            with self.sdk.push_scope() as scope:
                tags = {
                    "observability.run_id": self.context.run_id,
                    "github.run_id": self.context.github_run_id,
                    "github.sha": self.context.github_sha,
                    "github.ref": self.context.github_ref,
                    "pipeline.command": command_name,
                    "pipeline.stage": stage,
                    "failure_family": failure_family,
                    "failed_test_nodeid": failed_test_nodeid,
                }
                for key, value in tags.items():
                    if value:
                        scope.set_tag(key, value)
                scope.set_context(
                    "observed_command",
                    {
                        "argv": safe_argv(argv),
                        "exit_code": exit_code,
                        "artifact_path": artifact_path,
                        "first_failing_test": failed_test_nodeid,
                        "source_links": source_links or [],
                        "honeycomb_trace_url": honeycomb_trace_url,
                        "github_actions_url": github_actions_url,
                    },
                )
                event_id = self.sdk.capture_message(f"Eve Trade observed command failed: {command_name}", level="error")
                self.event_id = str(event_id or "")
            self.storage.write_json(
                "sentry-event.json",
                {"sentry_event_id": self.event_id, "sentry.event_id": self.event_id, "url": sentry_event_url(self.event_id)},
            )
            return self.event_id
        except Exception as exc:
            self._record_error("capture", exc)
            if self.strict:
                raise
            return ""

    def configure_release_cli(self) -> dict[str, Any]:
        required = ["SENTRY_AUTH_TOKEN", "SENTRY_ORG", "SENTRY_PROJECT"]
        if not shutil.which("sentry-cli") or not all(os.getenv(name) for name in required) or not release_name():
            return {"configured": False, "reason": "sentry-cli, release, or required credentials unavailable"}
        commands = [
            ["sentry-cli", "releases", "new", release_name() or ""],
            ["sentry-cli", "releases", "set-commits", release_name() or "", "--auto", "--ignore-missing"],
            ["sentry-cli", "releases", "finalize", release_name() or ""],
        ]
        results = []
        for argv in commands:
            result = subprocess.run(argv, cwd=self.context.repo_root, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, check=False)
            results.append({"argv": argv[:3], "exit_code": result.returncode, "output": redact_text(result.stdout)[-1000:]})
            if result.returncode and self.strict:
                raise RuntimeError(f"Sentry release command failed: {argv[:3]}")
        self.storage.write_json("sentry-release.json", results)
        return {"configured": all(item["exit_code"] == 0 for item in results), "results": results}

    def run_optional_autofix_hook(self) -> dict[str, Any]:
        """Run only an operator-supplied Seer/Autofix command; no API is assumed."""

        command = os.getenv("SENTRY_AUTOFIX_COMMAND", "").strip()
        if not command or not self.event_id:
            return {"invoked": False, "reason": "SENTRY_AUTOFIX_COMMAND or Sentry event ID unavailable"}
        argv = shlex.split(command, posix=os.name != "nt") + [self.event_id]
        try:
            result = subprocess.run(argv, cwd=self.context.repo_root, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120, check=False)
            metadata = {"invoked": True, "argv": safe_argv(argv), "exit_code": result.returncode, "output": redact_text(result.stdout)[-2000:]}
            self.storage.write_json("sentry-autofix-hook.json", metadata)
            if result.returncode and self.strict:
                raise RuntimeError("operator-supplied Sentry Autofix hook failed")
            return metadata
        except Exception as exc:
            self._record_error("autofix", exc)
            if self.strict:
                raise
            return {"invoked": True, "error": redact_text(str(exc))}

    def flush(self) -> None:
        if self.sdk is not None:
            try:
                self.sdk.flush(timeout=3)
            except Exception as exc:
                self._record_error("flush", exc)

    def _initialize(self) -> None:
        try:
            import sentry_sdk

            options: dict[str, Any] = {
                "dsn": os.getenv("SENTRY_DSN"),
                "environment": environment_name(),
                "release": release_name(),
                "traces_sample_rate": traces_sample_rate(),
                "send_default_pii": False,
                "max_breadcrumbs": 50,
            }
            try:
                sentry_sdk.init(enable_logs=True, **options)
            except TypeError:
                sentry_sdk.init(**options)
            self.sdk = sentry_sdk
        except Exception as exc:
            self._record_error("initialization", exc)
            if self.strict:
                raise

    def _record_error(self, phase: str, exc: BaseException) -> None:
        self.initialization_error = redact_text(str(exc))
        self.storage.write_text(f"sentry-{phase}-error.txt", self.initialization_error + "\n")
