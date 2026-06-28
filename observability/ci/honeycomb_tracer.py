"""OpenTelemetry tracing with an always-on local JSON span journal."""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .redaction import redact_mapping, redact_text
from .run_context import RunContext
from .storage import RunStorage


class TraceSpan:
    def __init__(self, tracer: "HoneycombTracer", name: str, attributes: dict[str, Any]) -> None:
        self.tracer = tracer
        self.name = name
        self.attributes = redact_mapping(attributes)
        self.started = time.perf_counter()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.exceptions: list[dict[str, str]] = []
        self._otel_context: Any = None
        self._otel_span: Any = None

    def __enter__(self) -> "TraceSpan":
        if self.tracer.otel_tracer is not None:
            self._otel_context = self.tracer.otel_tracer.start_as_current_span(self.name, attributes=_otel_attributes(self.attributes))
            self._otel_span = self._otel_context.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> None:
        if exc is not None:
            self.record_exception(exc)
        duration_ms = round((time.perf_counter() - self.started) * 1000, 3)
        trace_id = self.trace_id
        self.tracer.write_local_span(
            {
                "name": self.name,
                "start_time": self.started_at,
                "end_time": datetime.now(timezone.utc).isoformat(),
                "duration_ms": duration_ms,
                "status": "error" if exc or self.attributes.get("error") is True else "ok",
                "trace_id": trace_id,
                "attributes": self.attributes,
                "exceptions": self.exceptions,
            }
        )
        if self._otel_context is not None:
            self._otel_context.__exit__(exc_type, exc, tb)

    @property
    def trace_id(self) -> str:
        if self._otel_span is None:
            return ""
        try:
            value = self._otel_span.get_span_context().trace_id
            return f"{value:032x}" if value else ""
        except Exception:
            return ""

    def set_attribute(self, name: str, value: Any) -> None:
        safe = redact_mapping({name: value})[name]
        self.attributes[name] = safe
        if self._otel_span is not None and safe is not None:
            self._otel_span.set_attribute(name, safe)

    def record_exception(self, exc: BaseException) -> None:
        event = {
            "type": type(exc).__name__,
            "message": redact_text(str(exc))[:1000],
            "stack": redact_text("".join(traceback.format_exception_only(type(exc), exc))).strip()[:2000],
        }
        self.exceptions.append(event)
        if self._otel_span is not None:
            self._otel_span.record_exception(exc)


class HoneycombTracer:
    def __init__(self, context: RunContext, *, enabled: bool = True, strict: bool = False) -> None:
        self.context = context
        self.enabled = enabled
        self.strict = strict
        self.storage = RunStorage(context.run_dir, strict=strict)
        self.span_path = self.storage.path("telemetry/local-spans.jsonl")
        self.otel_tracer: Any = None
        self.provider: Any = None
        self.initialization_error = ""
        self._lock = threading.Lock()
        if enabled:
            self._initialize_otel()

    def span(self, name: str, attributes: dict[str, Any] | None = None) -> TraceSpan:
        common = {
            "observability.run_id": self.context.run_id,
            "github.run_id": self.context.github_run_id,
            "github.run_attempt": self.context.github_run_attempt,
            "github.workflow": self.context.github_workflow,
            "github.job": self.context.github_job,
            "github.sha": self.context.github_sha,
            "github.ref": self.context.github_ref,
        }
        common.update(attributes or {})
        return TraceSpan(self, name, {key: value for key, value in common.items() if value not in (None, "")})

    def write_local_span(self, value: dict[str, Any]) -> None:
        line = json.dumps(value, sort_keys=True, default=str) + "\n"
        with self._lock:
            with self.span_path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def shutdown(self) -> None:
        if self.provider is not None:
            try:
                self.provider.shutdown()
            except Exception as exc:
                self.storage.write_text("telemetry/shutdown-error.txt", redact_text(str(exc)) + "\n")
                if self.strict:
                    raise

    def _initialize_otel(self) -> None:
        traces_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
        endpoint = traces_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        api_key = os.getenv("HONEYCOMB_API_KEY", "").strip()
        if not endpoint and api_key:
            endpoint = "https://api.honeycomb.io"
        if not endpoint:
            return
        if not traces_endpoint:
            endpoint = endpoint.rstrip("/") + "/v1/traces"
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            headers = _parse_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS", ""))
            if api_key:
                headers.setdefault("x-honeycomb-team", api_key)
            dataset = os.getenv("HONEYCOMB_DATASET") or os.getenv("HONEYCOMB_SERVICE_NAME")
            if dataset:
                headers.setdefault("x-honeycomb-dataset", dataset)
            resource = Resource.create(
                {
                    "service.name": "eve-trade-ci",
                    "service.version": self.context.github_sha[:12] or "local",
                    "service.language": "python",
                    "deployment.environment": self.context.environment,
                    "observability.run_id": self.context.run_id,
                    "github.run_id": self.context.github_run_id,
                    "github.sha": self.context.github_sha,
                }
            )
            self.provider = TracerProvider(resource=resource)
            self.provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers)))
            trace.set_tracer_provider(self.provider)
            self.otel_tracer = trace.get_tracer("eve-trade-ci")
        except Exception as exc:
            self.initialization_error = redact_text(str(exc))
            self.storage.write_text("telemetry/initialization-error.txt", self.initialization_error + "\n")
            if self.strict:
                raise


def _parse_headers(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in raw.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            if key.strip():
                result[key.strip()] = value.strip()
    return result


def _otel_attributes(values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, (str, bool, int, float)):
            result[key] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(item, (str, bool, int, float)) for item in value):
            result[key] = list(value)
        else:
            result[key] = json.dumps(value, sort_keys=True, default=str)
    return result


_ACTIVE_TRACER: HoneycombTracer | None = None


def initialize_tracing(context: RunContext, *, enabled: bool = True, strict: bool = False) -> HoneycombTracer:
    global _ACTIVE_TRACER
    _ACTIVE_TRACER = HoneycombTracer(context, enabled=enabled, strict=strict)
    return _ACTIVE_TRACER


@contextlib.contextmanager
def span_for_stage(stage_name: str, attrs: dict[str, Any] | None = None) -> Iterator[TraceSpan | None]:
    if _ACTIVE_TRACER is None:
        yield None
        return
    with _ACTIVE_TRACER.span(f"pipeline.stage.{stage_name}", {"pipeline.stage": stage_name, **(attrs or {})}) as span:
        yield span


@contextlib.contextmanager
def span_for_command(command_name: str, attrs: dict[str, Any] | None = None) -> Iterator[TraceSpan | None]:
    if _ACTIVE_TRACER is None:
        yield None
        return
    with _ACTIVE_TRACER.span(f"pipeline.command.{command_name}", {"pipeline.command": command_name, **(attrs or {})}) as span:
        yield span


def record_exception(span: TraceSpan | None, exc: BaseException) -> None:
    if span is not None:
        span.record_exception(exc)


def ensure_triage_board(context: RunContext, *, strict: bool = False) -> dict[str, Any]:
    """Best-effort board creation using an explicit Honeycomb Configuration Key."""

    storage = RunStorage(context.run_dir, strict=strict)
    configuration_key = os.getenv("HONEYCOMB_CONFIGURATION_KEY", "").strip()
    if not configuration_key:
        return {"configured": False, "reason": "HONEYCOMB_CONFIGURATION_KEY unavailable"}
    api_base = os.getenv("HONEYCOMB_API_BASE_URL", "https://api.honeycomb.io").rstrip("/")
    board_name = "Eve Trade CI/CD Failure Triage"
    headers = {"X-Honeycomb-Team": configuration_key, "Content-Type": "application/json"}
    try:
        existing = _honeycomb_json_request(f"{api_base}/1/boards", headers=headers)
        boards = existing if isinstance(existing, list) else existing.get("boards", []) if isinstance(existing, dict) else []
        board = next((item for item in boards if isinstance(item, dict) and item.get("name") == board_name), None)
        if board is None:
            payload = {
                "name": board_name,
                "description": "Entry point for Eve Trade observed-run traces, failures, and BubbleUp investigations.",
                "type": "flexible",
                "layout_generation": "manual",
                "preset_filters": [
                    {"column": "observability.run_id", "alias": "Run ID"},
                    {"column": "service.name", "alias": "Service"},
                    {"column": "test.failure_family", "alias": "Failure family"},
                ],
                "panels": [
                    {
                        "type": "text",
                        "text_panel": {
                            "content": "# Eve Trade failure triage\nFilter by `observability.run_id`, select failed or slow spans, then run BubbleUp against the baseline."
                        },
                        "position": {"x_coordinate": 0, "y_coordinate": 0, "height": 4, "width": 12},
                    }
                ],
                "tags": [{"key": "project", "value": "eve-trade"}, {"key": "purpose", "value": "ci-failure-triage"}],
            }
            board = _honeycomb_json_request(f"{api_base}/1/boards", headers=headers, payload=payload)
            action = "created"
        else:
            action = "existing"
        result = {"configured": True, "action": action, "board_id": board.get("id", ""), "board_name": board_name}
        storage.write_json("honeycomb-board.json", result)
        return result
    except Exception as exc:
        result = {"configured": False, "error": redact_text(f"{type(exc).__name__}: {exc}")}
        storage.write_json("honeycomb-board.json", result)
        if strict:
            raise
        return result


def _honeycomb_json_request(url: str, *, headers: dict[str, str], payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))
