"""Typed command execution with durable logs, spans, and Sentry failure events."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .honeycomb_tracer import HoneycombTracer
from .links import github_actions_url
from .redaction import redact_mapping, redact_text, safe_argv
from .run_context import RunContext
from .sentry_reporter import SentryReporter
from .storage import RunStorage


@dataclass(frozen=True)
class CommandResult:
    name: str
    stage: str
    argv: list[str]
    exit_code: int
    started_at: str
    ended_at: str
    duration_ms: float
    stdout: str
    stderr: str
    metadata_path: str
    log_path: str
    trace_id: str = ""
    timed_out: bool = False
    command_id: str = ""
    stage_id: str = ""
    cwd: str = ""
    expected_artifacts: list[str] | None = None
    dependencies: list[str] | None = None
    timeout_seconds: float | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    def to_dict(self, *, include_output: bool = False) -> dict[str, object]:
        value = asdict(self)
        if not include_output:
            value.pop("stdout", None)
            value.pop("stderr", None)
        value.update(
            {
                "pipeline.command": self.name,
                "pipeline.stage": self.stage,
                "command.argv": self.argv,
                "command.exit_code": self.exit_code,
                "command.duration_ms": self.duration_ms,
                "artifact.path": self.log_path,
            }
        )
        return value


def run_command(
    context: RunContext,
    argv: Sequence[str],
    *,
    name: str,
    stage: str,
    storage: RunStorage | None = None,
    tracer: HoneycombTracer | None = None,
    sentry: SentryReporter | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    report_failure_to_sentry: bool = True,
) -> CommandResult:
    storage = storage or RunStorage(context.run_dir)
    command_dir = Path("commands") / _safe_name(stage) / _safe_name(name)
    metadata_path = storage.path(command_dir / "command.json")
    log_path = storage.path(command_dir / "command.log")
    safe_command = safe_argv(list(argv))
    command_id = _safe_name(name)
    stage_id = _safe_name(stage)
    started_wall = datetime.now(timezone.utc)
    started = time.perf_counter()
    stdout = ""
    stderr = ""
    exit_code = 127
    timed_out = False
    trace_id = ""
    attributes = {
        "pipeline.command": name,
        "pipeline.stage": stage,
        "pipeline.step": name,
        "command.argv": safe_command,
        "service.name": "eve-trade-ci",
        "service.language": "python",
    }
    manifest = {
        "schema_version": "o11y.command-manifest.v1",
        "command_id": command_id,
        "stage_id": stage_id,
        "name": name,
        "stage": stage,
        "argv": safe_command,
        "working_directory": str((cwd or context.repo_root).resolve()),
        "expected_artifacts": [],
        "dependencies": [],
        "timeout_seconds": timeout,
        "declared_at": started_wall.isoformat(),
    }
    storage.write_json(command_dir / "command-manifest.json", manifest)
    span_context = tracer.span(f"pipeline.command.{name}", attributes) if tracer else _NullSpan()
    with span_context as span:
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd or context.repo_root,
                env={**os.environ, **dict(env or {})},
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout = _decode_timeout(exc.stdout)
            stderr = _decode_timeout(exc.stderr) + f"\ncommand timed out after {timeout}s\n"
            if hasattr(span, "record_exception"):
                span.record_exception(exc)
        except OSError as exc:
            stderr = f"{type(exc).__name__}: {exc}\n"
            if hasattr(span, "record_exception"):
                span.record_exception(exc)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        if hasattr(span, "set_attribute"):
            span.set_attribute("command.exit_code", exit_code)
            span.set_attribute("command.duration_ms", duration_ms)
            span.set_attribute("error", exit_code != 0)
            trace_id = getattr(span, "trace_id", "")

    ended = datetime.now(timezone.utc)
    safe_stdout = redact_text(stdout)
    safe_stderr = redact_text(stderr)
    log_body = (
        f"command: {' '.join(safe_command)}\n"
        f"cwd: {(cwd or context.repo_root).resolve()}\n"
        f"exit_code: {exit_code}\n"
        f"duration_ms: {duration_ms}\n"
        "\n--- stdout ---\n"
        f"{safe_stdout}"
        "\n--- stderr ---\n"
        f"{safe_stderr}"
    )
    storage.write_text(log_path.relative_to(context.run_dir), log_body)
    result = CommandResult(
        name=name,
        stage=stage,
        argv=safe_command,
        exit_code=exit_code,
        started_at=started_wall.isoformat(),
        ended_at=ended.isoformat(),
        duration_ms=duration_ms,
        stdout=safe_stdout,
        stderr=safe_stderr,
        metadata_path=metadata_path.relative_to(context.run_dir).as_posix(),
        log_path=log_path.relative_to(context.run_dir).as_posix(),
        trace_id=trace_id,
        timed_out=timed_out,
        command_id=command_id,
        stage_id=stage_id,
        cwd=str((cwd or context.repo_root).resolve()),
        expected_artifacts=[],
        dependencies=[],
        timeout_seconds=timeout,
    )
    storage.write_json(metadata_path.relative_to(context.run_dir), result.to_dict())
    if sentry:
        sentry.breadcrumb(f"command {name} exited {exit_code}", category="command", data=result.to_dict(), level="error" if exit_code else "info")
        if exit_code and report_failure_to_sentry:
            sentry.capture_command_failure(
                command_name=name,
                argv=list(argv),
                exit_code=exit_code,
                stage=stage,
                artifact_path=result.log_path,
                github_actions_url=github_actions_url(context),
            )
    return result


class _NullSpan:
    trace_id = ""

    def __enter__(self) -> "_NullSpan":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def set_attribute(self, *_: object) -> None:
        return None

    def record_exception(self, *_: object) -> None:
        return None


def _decode_timeout(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "-" for character in value).strip("-") or "command"
