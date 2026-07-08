"""Generate portable Markdown, HTML, and JSON failure triage reports."""

from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import MISSING
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from observability.ci.classify_failure import FailureClassification, classify_failure
from observability.ci.collect_pytest import PytestSummary
from observability.ci.links import github_actions_url, honeycomb_investigation, relative_artifact, sentry_event_url, source_url
from observability.ci.run_command import CommandResult
from observability.ci.run_context import RunContext, load_run_context
from observability.ci.storage import RunStorage


def generate_failure_report(
    context: RunContext,
    command: CommandResult,
    pytest: PytestSummary | None = None,
    classification: FailureClassification | None = None,
    *,
    database: dict[str, Any] | None = None,
    kubernetes: dict[str, Any] | None = None,
    sentry_event_id: str = "",
    trace_id: str = "",
    missing_evidence: list[str] | None = None,
    parity_hints: list[str] | None = None,
    storage: RunStorage | None = None,
) -> dict[str, Path]:
    storage = storage or RunStorage(context.run_dir)
    pytest = pytest or PytestSummary()
    classification = classification or classify_failure(nodeid=pytest.first_failing_test_nodeid, assertion=pytest.assertion_text, logs=command.stderr + "\n" + command.stdout)
    honeycomb = honeycomb_investigation(
        context,
        trace_id=trace_id or command.trace_id,
        test_nodeid=pytest.first_failing_test_nodeid,
        service_name=classification.suspected_services[0] if classification.suspected_services else "",
        failure_family=classification.failure_family,
    )
    report_md = storage.path("failure-report.md")
    report_html = storage.path("failure-report.html")
    report_json = storage.path("failure-summary.json")
    source_links = _solution_links(context, classification.likely_solution_files)
    artifact_links = _artifact_inventory(context.run_dir, report_md)
    summary = {
        "executive_summary": _executive_summary(command, pytest, classification),
        "run": context.to_dict(),
        "failed_command": command.to_dict(),
        "pytest": pytest.to_dict(),
        "classification": classification.to_dict(),
        "github_actions_url": github_actions_url(context),
        "source_links": source_links,
        "honeycomb": honeycomb,
        "sentry": {"event_id": sentry_event_id, "event_url": sentry_event_url(sentry_event_id)},
        "database": database or {},
        "kubernetes": kubernetes or {},
        "artifacts": artifact_links,
        "parity_hints": parity_hints or [],
        "missing_evidence": missing_evidence or [],
    }
    storage.write_json(report_json.relative_to(context.run_dir), summary)
    storage.write_text(report_md.relative_to(context.run_dir), _markdown(summary))
    storage.write_text(report_html.relative_to(context.run_dir), _html(summary))
    return {"markdown": report_md, "html": report_html, "json": report_json}


def _executive_summary(command: CommandResult, pytest: PytestSummary, classification: FailureClassification) -> str:
    if pytest.first_failing_test_nodeid:
        return f"CI failed in {pytest.first_failing_test_nodeid}; likely failure family: {classification.failure_family} ({classification.confidence:.0%} rule confidence)."
    return f"Command {command.name} failed with exit code {command.exit_code}; likely failure family: {classification.failure_family} ({classification.confidence:.0%} rule confidence)."


def _markdown(data: dict[str, Any]) -> str:
    command = data["failed_command"]
    pytest = data["pytest"]
    classification = data["classification"]
    honeycomb = data["honeycomb"]
    sentry = data["sentry"]
    service_logs = [item for item in data["artifacts"] if item["path"].startswith(("runtime/logs/", "kubernetes/logs/"))]
    database_artifacts = [item for item in data["artifacts"] if item["path"].startswith("db/")]
    lines = [
        "# Eve Trade failure report", "", "## Executive summary", "", data["executive_summary"], "",
        "## Run identity", "", f"- Run: `{data['run']['run_id']}`", f"- Environment: `{data['run']['environment']}`",
        f"- Git SHA: `{data['run'].get('github_sha', '')}`",
    ]
    if data["github_actions_url"]:
        lines.append(f"- [Open GitHub Actions run]({data['github_actions_url']})")
    lines.extend([
        "", "## Failed command", "", f"- Stage: `{command['stage']}`", f"- Command: `{' '.join(command['argv'])}`",
        f"- Exit code: `{command['exit_code']}`", f"- Duration: `{command['duration_ms']} ms`", f"- [Command log]({command['log_path']})",
        "", "## First failing test", "",
        f"- Node ID: `{pytest.get('first_failing_test_nodeid') or 'not extracted'}`",
        f"- Failure: {pytest.get('failure_message') or 'No pytest failure was extracted.'}",
    ])
    if pytest.get("source_url"):
        lines.append(f"- [Open failing source line]({pytest['source_url']})")
    lines.extend([
        "", "## Failure family", "", f"- Family: `{classification['failure_family']}`", f"- Confidence: `{classification['confidence']:.0%}`",
        f"- Suspected services: {', '.join(classification['suspected_services']) or 'unknown'}", "",
        *[f"- {item}" for item in classification["evidence"]],
        "", "## Causal chain", "", f"1. Pipeline stage `{command['stage']}` invoked `{command['name']}`.",
        f"2. The command exited `{command['exit_code']}` after `{command['duration_ms']} ms`.",
        f"3. The first extracted failure was `{pytest.get('first_failing_test_nodeid') or 'not a pytest test'}`.",
        f"4. Transparent rules classified the available evidence as `{classification['failure_family']}`.",
        "", "## Honeycomb and BubbleUp", "",
    ])
    if honeycomb.get("trace_url"):
        lines.append(f"- [Open Honeycomb trace]({honeycomb['trace_url']})")
    if honeycomb.get("query_url"):
        lines.append(f"- [Open suggested Honeycomb query]({honeycomb['query_url']})")
    lines.extend([f"- {step}" for step in honeycomb["bubbleup_steps"]])
    lines.extend(["", "Fields to inspect: " + ", ".join(f"`{field}`" for field in honeycomb["suggested_fields"]), "", "## Sentry / Seer / Autofix", ""])
    if sentry.get("event_url"):
        lines.append(f"- [Open Sentry event]({sentry['event_url']})")
    else:
        lines.append("- No Sentry event was created; configure `SENTRY_DSN` to enable it.")
    lines.extend([
        "- In the Sentry issue, open Seer/Autofix if the organization plan and project support it.",
        "- Provide Seer the linked command log, failing source line, likely solution files, and Honeycomb trace/query. No guaranteed Seer API is assumed.",
        "", "## Related service logs", "",
        *([f"- [{item['path']}]({item['href']})" for item in service_logs] or ["- No per-service log artifacts were collected."]),
        "", "## Database snapshots", "",
        *([f"- [{item['path']}]({item['href']})" for item in database_artifacts] or ["- No database snapshot artifacts were collected."]),
        "", "## Likely solution files", "",
        *[f"- [{item['path']}]({item['url']})" for item in data["source_links"]],
        "", "## Suggested next commands", "", *[f"- `{item}`" for item in classification["likely_next_commands"]],
        "", "## Related artifacts", "", *[f"- [{item['path']}]({item['href']})" for item in data["artifacts"]],
        "", "## Local vs CI parity", "", *([f"- {item}" for item in data["parity_hints"]] or ["- Compare this run with a passing local run using `compare_runs.py`."]),
        "", "## Missing evidence", "", *([f"- {item}" for item in data["missing_evidence"]] or ["- None reported by collectors."]), "",
    ])
    return "\n".join(lines)


def _html(data: dict[str, Any]) -> str:
    classification = data["classification"]
    pytest = data["pytest"]
    command = data["failed_command"]
    def link(label: str, url: str) -> str:
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>' if url else html.escape(label)
    artifacts = "".join(f"<li>{link(item['path'], item['href'])}</li>" for item in data["artifacts"])
    service_logs = "".join(f"<li>{link(item['path'], item['href'])}</li>" for item in data["artifacts"] if item["path"].startswith(("runtime/logs/", "kubernetes/logs/"))) or "<li>No per-service log artifacts were collected.</li>"
    database = "".join(f"<li>{link(item['path'], item['href'])}</li>" for item in data["artifacts"] if item["path"].startswith("db/")) or "<li>No database snapshot artifacts were collected.</li>"
    solutions = "".join(f"<li>{link(item['path'], item['url'])}</li>" for item in data["source_links"])
    bubble = "".join(f"<li>{html.escape(item)}</li>" for item in data["honeycomb"]["bubbleup_steps"])
    next_commands = "".join(f"<li><code>{html.escape(item)}</code></li>" for item in classification["likely_next_commands"])
    missing = "".join(f"<li>{html.escape(item)}</li>" for item in data["missing_evidence"]) or "<li>None reported by collectors.</li>"
    parity = "".join(f"<li>{html.escape(item)}</li>" for item in data["parity_hints"]) or "<li>Compare this run with a passing local run using compare_runs.py.</li>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Eve Trade failure report</title><style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#17212b}}header{{background:#18222d;color:white;padding:24px;border-radius:8px}}section{{border:1px solid #d8dee4;border-radius:8px;padding:18px;margin:18px 0}}code,pre{{background:#f4f6f8;padding:2px 5px;border-radius:4px}}.bad{{color:#b42318}}a{{color:#0969da}}table{{border-collapse:collapse}}td,th{{padding:6px 10px;border:1px solid #d8dee4;text-align:left}}</style></head><body>
<header><h1>Eve Trade failure report</h1><p>{html.escape(data['executive_summary'])}</p></header>
<section><h2>Run identity</h2><p><code>{html.escape(data['run']['run_id'])}</code> | {html.escape(data['run']['environment'])} | {link('GitHub Actions', data['github_actions_url'])}</p></section>
<section><h2>Failure</h2><p class="bad"><strong>{html.escape(command['name'])}</strong> exited {command['exit_code']}</p><p>Test: <code>{html.escape(pytest.get('first_failing_test_nodeid') or 'not extracted')}</code></p><p>{link('Open failing source', pytest.get('source_url',''))} | {link('Command log', command['log_path'])}</p><pre>{html.escape(pytest.get('failure_message') or command.get('stderr','')[:2000])}</pre></section>
<section><h2>Classification</h2><p><strong>{html.escape(classification['failure_family'])}</strong> ({classification['confidence']:.0%} rule confidence)</p><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in classification['evidence'])}</ul></section>
<section><h2>Honeycomb BubbleUp</h2><p>{link('Open trace', data['honeycomb'].get('trace_url',''))} | {link('Open suggested query', data['honeycomb'].get('query_url',''))}</p><ol>{bubble}</ol></section>
<section><h2>Sentry / Seer / Autofix</h2><p>{link('Open Sentry event', data['sentry'].get('event_url',''))}</p><p>Use Seer/Autofix from the generated Sentry issue when supported; attach the command log, trace, and likely solution links.</p></section>
<section><h2>Related service logs</h2><ul>{service_logs}</ul></section><section><h2>Database snapshots</h2><ul>{database}</ul></section>
<section><h2>Likely solution files</h2><ul>{solutions}</ul><h3>Suggested next commands</h3><ul>{next_commands}</ul></section>
<section><h2>Local vs CI parity</h2><ul>{parity}</ul></section><section><h2>Missing evidence</h2><ul>{missing}</ul></section>
<section><h2>All artifacts</h2><ul>{artifacts}</ul></section>
</body></html>"""


def _solution_links(context: RunContext, files: list[str]) -> list[dict[str, str]]:
    return [{"path": item, "url": source_url(context, item)} for item in files]


def _artifact_inventory(run_dir: Path, report_path: Path) -> list[dict[str, str]]:
    result = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and path != report_path and path.name not in {"failure-report.html", "failure-summary.json"}:
            result.append({"path": path.relative_to(run_dir).as_posix(), "href": relative_artifact(report_path, path)})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate a failure report from an observed run")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    context = load_run_context(args.run_dir)
    command_files = sorted(args.run_dir.glob("commands/**/command.json"))
    if not command_files:
        raise SystemExit("run contains no command metadata")
    command_values = [json.loads(path.read_text(encoding="utf-8")) for path in command_files]
    value = next((item for item in command_values if int(item.get("exit_code", 0)) != 0), command_values[-1])
    command = CommandResult(
        name=str(value.get("name", "unknown-command")),
        stage=str(value.get("stage", "unknown")),
        argv=[str(item) for item in value.get("argv", [])],
        exit_code=int(value.get("exit_code", 1)),
        started_at=str(value.get("started_at", "")),
        ended_at=str(value.get("ended_at", "")),
        duration_ms=float(value.get("duration_ms", 0.0)),
        stdout="",
        stderr="",
        metadata_path=str(value.get("metadata_path", command_files[-1].relative_to(args.run_dir).as_posix())),
        log_path=str(value.get("log_path", "")),
        trace_id=str(value.get("trace_id", "")),
        timed_out=bool(value.get("timed_out", False)),
    )
    pytest_path = args.run_dir / "pytest" / "pytest-summary.json"
    pytest_summary = None
    if pytest_path.exists():
        pytest_value = json.loads(pytest_path.read_text(encoding="utf-8"))
        values = {}
        for key, dataclass_field in PytestSummary.__dataclass_fields__.items():
            if key in pytest_value:
                values[key] = pytest_value[key]
            elif dataclass_field.default is not MISSING:
                values[key] = dataclass_field.default
            else:
                values[key] = dataclass_field.default_factory()
        pytest_summary = PytestSummary(**values)
    outputs = generate_failure_report(context, command, pytest_summary)
    print(outputs["html"])


if __name__ == "__main__":
    main()
