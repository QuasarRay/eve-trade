"""Portable artifact and external investigation links."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from .run_context import RunContext


def github_actions_url(context: RunContext) -> str:
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    return f"{server}/{repository}/actions/runs/{context.github_run_id}" if repository and context.github_run_id else ""


def source_url(context: RunContext, file: str, line: int | None = None) -> str:
    repository = os.getenv("GITHUB_REPOSITORY", "") or _repository_from_remote(context.repo_root)
    if not repository:
        path = (context.repo_root / file).resolve()
        return f"file:///{path.as_posix()}" + (f"#L{line}" if line else "")
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    sha = context.github_sha or _git_sha(context.repo_root) or "HEAD"
    return f"{server}/{repository}/blob/{sha}/{quote(file.replace('\\', '/'), safe='/')}" + (f"#L{line}" if line else "")


def relative_artifact(from_file: Path, target: Path) -> str:
    return Path(os.path.relpath(target, from_file.parent)).as_posix()


def sentry_event_url(event_id: str) -> str:
    org = os.getenv("SENTRY_ORG", "") or os.getenv("SENTRY_ORG_SLUG", "")
    base = os.getenv("SENTRY_URL", "https://sentry.io").rstrip("/")
    if not event_id or not org:
        return ""
    return f"{base}/organizations/{quote(org)}/issues/?query={quote(event_id)}"


def honeycomb_investigation(context: RunContext, *, trace_id: str = "", test_nodeid: str = "", service_name: str = "", failure_family: str = "") -> dict[str, Any]:
    filters = {"observability.run_id": context.run_id}
    if test_nodeid:
        filters["test.nodeid"] = test_nodeid
    if service_name:
        filters["service.name"] = service_name
    if failure_family:
        filters["test.failure_family"] = failure_family
    team = os.getenv("HONEYCOMB_TEAM_SLUG", "")
    environment = os.getenv("HONEYCOMB_ENVIRONMENT_SLUG", "")
    ui_base = os.getenv("HONEYCOMB_UI_BASE_URL", "https://ui.honeycomb.io").rstrip("/")
    trace_url = ""
    if trace_id and team and environment:
        trace_parameters = {"trace_id": trace_id}
        try:
            started = datetime.fromisoformat(context.started_at).astimezone(timezone.utc)
            trace_parameters["trace_start_ts"] = str(max(0, int(started.timestamp()) - 300))
            trace_parameters["trace_end_ts"] = str(int(started.timestamp()) + 7500)
        except ValueError:
            pass
        trace_url = f"{ui_base}/{quote(team)}/environments/{quote(environment)}/trace?{urlencode(trace_parameters)}"
    dataset = os.getenv("HONEYCOMB_DATASET") or os.getenv("HONEYCOMB_SERVICE_NAME") or "eve-trade-ci"
    query_url = ""
    if team and environment:
        query_spec = {
            "calculations": [{"op": "COUNT"}],
            "filters": [{"column": key, "op": "=", "value": value} for key, value in filters.items()],
            "time_range": 7200,
        }
        query = urlencode({"query": json.dumps(query_spec, separators=(",", ":"))})
        query_url = f"{ui_base}/{quote(team)}/environments/{quote(environment)}/datasets/{quote(dataset)}/?{query}"
    return {
        "trace_id": trace_id,
        "trace_url": trace_url,
        "query_url": query_url,
        "filters": filters,
        "suggested_fields": [
            "python.version", "os.name", "docker.image_digest", "db.schema_hash",
            "db.migration_hash", "protobuf.generated_hash", "service.name", "test.nodeid",
            "command.exit_code", "test.failure_family", "github.run_id", "git.dirty",
        ],
        "bubbleup_steps": [
            f"Open or build a Honeycomb query filtered by observability.run_id = {context.run_id}.",
            "Visualize duration with a heatmap or filter spans where error=true / command.exit_code != 0.",
            "Select the failed subset and choose BubbleUp / Compare to Baseline.",
            "Compare against a passing local run or passing CI run using the high-cardinality fields above.",
        ],
    }


def _repository_from_remote(root: Path) -> str:
    try:
        import subprocess

        remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10, check=False).stdout.strip()
    except Exception:
        return ""
    match = re.search(r"github\.com[/:]([^/]+/[^/.]+)(?:\.git)?$", remote)
    return match.group(1) if match else ""


def _git_sha(root: Path) -> str:
    try:
        import subprocess

        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10, check=False).stdout.strip()
    except Exception:
        return ""
