"""Shared primitives for isolated Dagger CI stages."""
from __future__ import annotations

import json
import os
import base64
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Awaitable, Callable, Iterable, Mapping, Any
from urllib.parse import quote

from _bootstrap import bootstrap

REPO_ROOT = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
OBSERVABILITY_ROOT = REPO_ROOT / "distributed-backend"
if str(OBSERVABILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(OBSERVABILITY_ROOT))

from observability.ci.redaction import is_sensitive_key, redact_text as _repository_redact_text  # noqa: E402


class StageFailure(RuntimeError):
    """A stage failure that carries diagnostics safe for GitHub job output."""
    def __init__(self, message: str, *, payload: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.payload = dict(payload or {})


DEFAULT_EXCLUDES = [
    ".dagger/", ".venv/", ".dagger-ci-venv/", ".dagger-ci-bin/",
    ".dagger-download-*/", "**/__pycache__/",
    "**/.pytest_cache/", "**/target/", "**/node_modules/",
]


def redact_text(value: str) -> str:
    """Apply repository redaction plus exact/encoded current-process secret masking."""
    redacted = _repository_redact_text(value)
    for key, secret in os.environ.items():
        if not is_sensitive_key(key) or len(secret) < 4:
            continue
        variants = {
            secret,
            quote(secret, safe=""),
            base64.b64encode(secret.encode("utf-8")).decode("ascii"),
            base64.urlsafe_b64encode(secret.encode("utf-8")).decode("ascii"),
        }
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                redacted = redacted.replace(variant, "<redacted:present>")
    return redacted


def _sanitize_payload(value: Any, key: str = "") -> Any:
    if key and is_sensitive_key(key):
        return "<redacted:present>" if value not in (None, "") else "<redacted:empty>"
    if isinstance(value, Mapping):
        return {str(item_key): _sanitize_payload(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item, key) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def github_env() -> dict[str, str]:
    names = (
        "CI", "GITHUB_ACTIONS", "GITHUB_ACTOR", "GITHUB_BASE_REF", "GITHUB_HEAD_REF",
        "GITHUB_EVENT_NAME", "GITHUB_REF", "GITHUB_REF_NAME", "GITHUB_REPOSITORY",
        "GITHUB_REPOSITORY_OWNER", "GITHUB_RUN_ATTEMPT", "GITHUB_RUN_ID",
        "GITHUB_RUN_NUMBER", "GITHUB_SERVER_URL", "GITHUB_SHA", "GITHUB_WORKFLOW",
        "GITHUB_WORKSPACE", "RUNNER_ARCH", "RUNNER_NAME", "RUNNER_OS",
    )
    return {name: os.environ[name] for name in names if name in os.environ}


def source(dag, *, include_git: bool = True):
    excludes = list(DEFAULT_EXCLUDES)
    if not include_git:
        excludes.append(".git/")
    return dag.host().directory(str(REPO_ROOT), exclude=excludes)


def with_source(container, dag, *, include_git: bool = True):
    return container.with_directory("/src", source(dag, include_git=include_git)).with_workdir("/src")


def with_env(container, values: Mapping[str, str] | None = None):
    merged = github_env()
    if values:
        merged.update({k: str(v) for k, v in values.items()})
    for key, value in merged.items():
        container = container.with_env_variable(key, value)
    return container


def docker_socket(dag):
    path = os.environ.get("DOCKER_HOST_SOCKET", "/var/run/docker.sock")
    if not Path(path).exists():
        raise RuntimeError(f"Docker socket required by this stage but missing: {path}")
    return dag.host().unix_socket(path)


def with_docker_socket(container, dag, *, target: str = "/var/run/docker.sock"):
    return container.with_unix_socket(target, docker_socket(dag))


def with_secret_env(container, dag, env_name: str, *, required: bool = False):
    value = os.environ.get(env_name)
    if value:
        return container.with_secret_variable(env_name, dag.set_secret(env_name, value))
    if required:
        raise RuntimeError(f"required secret environment variable is not set: {env_name}")
    return container


def sh(container, commands: Iterable[str]):
    script = "\n".join(commands)
    return container.with_exec(["bash", "-euo", "pipefail", "-c", script])


def _write_github_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(f"{key}={value}\n")


def _append_step_summary(title: str, payload: dict[str, Any]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(f"### {title}\n\n```json\n{json.dumps(payload, indent=2, sort_keys=True)}\n```\n")


def _record_stage(name: str, started: datetime, ended: datetime, status: str, payload: dict[str, Any], error: str | None) -> dict[str, Any]:
    sanitized_payload = _sanitize_payload(payload)
    record = {
        "schema_version": "eve-trade.dagger-stage/v2",
        "stage": name,
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": ended.isoformat(),
        "payload": sanitized_payload,
        "error": redact_text(error) if error else None,
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "sha": os.environ.get("GITHUB_SHA", ""),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    }
    path = REPO_ROOT / ".o11y" / "dagger-stage-timing" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def stage(name: str, impl: Callable[[object], Awaitable[Mapping[str, Any] | None]]) -> None:
    """Run one Dagger application and always emit machine-readable job evidence."""
    started = datetime.now(timezone.utc)
    try:
        bootstrap()
    except BaseException as exc:
        error = redact_text(f"{type(exc).__name__}: {exc}")[:4000]
        record = _record_stage(name, started, datetime.now(timezone.utc), "failure", {}, error)
        _write_github_output("evidence", json.dumps(record, separators=(",", ":"), sort_keys=True))
        _append_step_summary(f"Dagger stage: {name}", record)
        raise
    import anyio
    import dagger
    from dagger import dag

    async def _run() -> None:
        print(f"::group::Dagger stage: {name}", flush=True)
        status = "failure"
        payload: dict[str, Any] = {}
        error: str | None = None
        try:
            async with dagger.connection(dagger.Config(log_output=sys.stderr)):
                result = await impl(dag)
                if result:
                    payload = dict(result)
            status = "success"
        except BaseException as exc:
            if isinstance(exc, StageFailure):
                payload = dict(exc.payload)
            error = redact_text(f"{type(exc).__name__}: {exc}")[:4000]
            raise
        finally:
            record = _record_stage(name, started, datetime.now(timezone.utc), status, payload, error)
            compact = json.dumps(record, separators=(",", ":"), sort_keys=True)
            _write_github_output("evidence", compact)
            if status == "success" and payload.get("verified_sha"):
                _write_github_output("verified_sha", str(payload["verified_sha"]))
            _append_step_summary(f"Dagger stage: {name}", record)
            print("::endgroup::", flush=True)

    anyio.run(_run)
