"""Local-vs-CI evidence comparison and parity diagnosis."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


COMPARE_FIELDS = {
    "git.sha": ("git.json", "sha"),
    "git.dirty": ("git.json", "dirty"),
    "git.branch": ("git.json", "branch"),
    "python.version": ("tool-versions.json", "python"),
    "go.version": ("tool-versions.json", "go"),
    "rust.version": ("tool-versions.json", "rustc"),
    "docker.version": ("tool-versions.json", "docker"),
    "docker.compose.version": ("tool-versions.json", "docker_compose"),
    "os.name": ("tool-versions.json", "os"),
    "docker.compose_config_hash": ("hashes.json", "docker.compose_config_hash"),
    "db.schema_hash": ("db/metadata.json", "db.schema_hash"),
    "db.migration_hash": ("hashes.json", "db.migration_hash"),
    "protobuf.generated_hash": ("hashes.json", "protobuf.generated_hash"),
    "pytest.collected": ("pytest/pytest-summary.json", "collected_count"),
    "pytest.first_failure": ("pytest/pytest-summary.json", "first_failing_test_nodeid"),
}


def compare_runs(local: Path, ci: Path, output_dir: Path | None = None) -> dict[str, Path]:
    output_dir = output_dir or local
    output_dir.mkdir(parents=True, exist_ok=True)
    comparisons: list[dict[str, Any]] = []
    for name, (filename, key) in COMPARE_FIELDS.items():
        local_value = _read(local / filename).get(key)
        ci_value = _read(ci / filename).get(key)
        comparisons.append({"field": name, "local": local_value, "ci": ci_value, "different": local_value != ci_value, "hint": _hint(name, local_value, ci_value) if local_value != ci_value else ""})
    local_hashes = _read(local / "hashes.json")
    ci_hashes = _read(ci / "hashes.json")
    _append_comparison(
        comparisons,
        "db.migration_files",
        _nested(local_hashes, "migrations", "files"),
        _nested(ci_hashes, "migrations", "files"),
        "Migration file lists differ.",
    )
    _append_comparison(
        comparisons,
        "pytest.collected_tests",
        _read(local / "pytest/pytest-summary.json").get("collected_tests", []),
        _read(ci / "pytest/pytest-summary.json").get("collected_tests", []),
        "Pytest collection differs.",
    )
    local_env = _read(local / "env-redacted.json")
    ci_env = _read(ci / "env-redacted.json")
    for key in sorted(set(local_env) | set(ci_env)):
        local_present = key in local_env and local_env[key] not in ("", None, "<redacted:empty>")
        ci_present = key in ci_env and ci_env[key] not in ("", None, "<redacted:empty>")
        if local_present != ci_present:
            comparisons.append({"field": f"env.{key}.present", "local": local_present, "ci": ci_present, "different": True, "hint": "Environment variable presence differs; values remain redacted."})
    local_images = _image_map(_read(local / "docker/metadata.json"))
    ci_images = _image_map(_read(ci / "docker/metadata.json"))
    for service in sorted(set(local_images) | set(ci_images)):
        if local_images.get(service) != ci_images.get(service):
            comparisons.append({"field": f"docker.image_digest.{service}", "local": local_images.get(service), "ci": ci_images.get(service), "different": True, "hint": "Docker image digest differs."})
    local_summary = _read(local / "run-summary.json")
    ci_summary = _read(ci / "run-summary.json")
    _compare_mapping(comparisons, "service.url", local_summary.get("service_urls", {}), ci_summary.get("service_urls", {}), "Service URL differs.")
    _compare_mapping(
        comparisons,
        "service.readiness_ms",
        local_summary.get("service_readiness_ms", {}),
        ci_summary.get("service_readiness_ms", {}),
        "Service readiness duration differs; investigate large regressions with the underlying command log.",
    )
    local_sequence = local_summary.get("command_sequence", [])
    ci_sequence = ci_summary.get("command_sequence", [])
    if local_sequence != ci_sequence:
        comparisons.append(
            {
                "field": "pipeline.command_sequence",
                "local": local_sequence,
                "ci": ci_sequence,
                "different": True,
                "hint": "Observed command sequence differs between local and CI.",
            }
        )
    differences = [item for item in comparisons if item["different"]]
    summary = {
        "local": str(local),
        "ci": str(ci),
        "comparisons": comparisons,
        "differences": differences,
        "likely_causes": list(dict.fromkeys(item["hint"] for item in differences if item["hint"])),
    }
    json_path = output_dir / "parity-diff.json"
    md_path = output_dir / "parity-diff.md"
    html_path = output_dir / "parity-diff.html"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(summary), encoding="utf-8")
    html_path.write_text(_html(summary), encoding="utf-8")
    return {"json": json_path, "markdown": md_path, "html": html_path}


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _image_map(metadata: dict[str, Any]) -> dict[str, str]:
    return {str(item.get("service", "")): str(item.get("id", "")) for item in metadata.get("images", []) if item.get("service")}


def _compare_mapping(
    differences: list[dict[str, Any]],
    prefix: str,
    local: Any,
    ci: Any,
    hint: str,
) -> None:
    local_values = local if isinstance(local, dict) else {}
    ci_values = ci if isinstance(ci, dict) else {}
    for key in sorted(set(local_values) | set(ci_values)):
        if local_values.get(key) != ci_values.get(key):
            differences.append(
                {
                    "field": f"{prefix}.{key}",
                    "local": local_values.get(key),
                    "ci": ci_values.get(key),
                    "different": True,
                    "hint": hint,
                }
            )


def _append_comparison(
    comparisons: list[dict[str, Any]],
    field: str,
    local: Any,
    ci: Any,
    hint: str,
) -> None:
    different = local != ci
    comparisons.append(
        {"field": field, "local": local, "ci": ci, "different": different, "hint": hint if different else ""}
    )


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _hint(field: str, local: Any, ci: Any) -> str:
    if field == "git.dirty" and local:
        return "Local has uncommitted files; CI tests the committed checkout."
    if field == "db.schema_hash":
        return "Local DB schema differs from CI."
    if field == "db.migration_hash":
        return "Migration content/hash differs."
    if field == "protobuf.generated_hash":
        return "Generated protobuf hash differs."
    if field.startswith("docker"):
        return "Docker configuration, version, or image digest differs."
    if field.endswith("version") or field == "os.name":
        return f"Runtime parity differs for {field}."
    if field.startswith("pytest"):
        return "Pytest collection or first failure differs."
    return f"{field} differs between local and CI."


def _markdown(summary: dict[str, Any]) -> str:
    lines = ["# Local vs CI parity diff", "", f"- Local: `{summary['local']}`", f"- CI: `{summary['ci']}`", "", "| Field | Local | CI | Likely impact |", "|---|---|---|---|"]
    for item in summary["differences"]:
        lines.append(f"| {item['field']} | `{item['local']}` | `{item['ci']}` | {item['hint']} |")
    if not summary["differences"]:
        lines.append("| — | — | — | No compared differences found. |")
    return "\n".join(lines) + "\n"


def _html(summary: dict[str, Any]) -> str:
    rows = "".join(f"<tr><td>{html.escape(str(item['field']))}</td><td><code>{html.escape(str(item['local']))}</code></td><td><code>{html.escape(str(item['ci']))}</code></td><td>{html.escape(item['hint'])}</td></tr>" for item in summary["differences"])
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Parity diff</title><style>body{{font:15px system-ui;max-width:1200px;margin:30px auto}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:8px;text-align:left}}tr:nth-child(even){{background:#f6f8fa}}</style></head><body><h1>Local vs CI parity diff</h1><p>Local: <code>{html.escape(summary['local'])}</code><br>CI: <code>{html.escape(summary['ci'])}</code></p><table><thead><tr><th>Field</th><th>Local</th><th>CI</th><th>Likely impact</th></tr></thead><tbody>{rows}</tbody></table></body></html>"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare local and CI observability run artifacts")
    parser.add_argument("--local", required=True, type=Path)
    parser.add_argument("--ci", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    outputs = compare_runs(args.local, args.ci, args.output)
    print(outputs["html"])


if __name__ == "__main__":
    main()
