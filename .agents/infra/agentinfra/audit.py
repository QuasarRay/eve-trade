from __future__ import annotations

import hashlib
import os
import shutil
import tomllib
from pathlib import Path

from .bootstrap import verify_installed
from .codex_config import AGENTS, TOP, V2, load_role_specs, verify_managed_source
from .manifest import entries, verify
from .modules import discover
from .paths import framework_dir
from .process import run_process
from .release_source import SOURCE_DIRECTORIES, SOURCE_FILES, validate_version_consistency
from .security import SecurityError, ensure_private_control_file
from .state_machine import ALLOWED, STATES


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _framework_paths(root: Path, framework: Path) -> list[Path]:
    if framework != root:
        return sorted(framework.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    selected: set[Path] = set()
    for relative in SOURCE_FILES:
        path = root / relative
        if path.exists():
            selected.add(path)
    for relative in SOURCE_DIRECTORIES:
        base = root / relative
        if base.is_dir():
            selected.update(base.rglob("*"))
    return sorted(selected, key=lambda item: item.relative_to(root).as_posix())


def audit(root: Path):
    root = root.resolve(strict=True)
    issues: list[str] = []
    agents = framework_dir(root)
    source_mode = agents == root
    required = (agents / "INDEX.md", agents / "framework.toml")
    if not source_mode:
        required = (root / "AGENTS.md", *required)
    for path in required:
        if not path.is_file():
            issues.append(f"missing required file: {path.relative_to(root)}")
    try:
        with (agents / "framework.toml").open("rb") as stream:
            config = tomllib.load(stream)
        if config["reasoning"]["default_effort"] != "max": issues.append("reasoning.default_effort is not max")
        if config["reasoning"].get("allow_silent_downgrade") is not False: issues.append("silent reasoning downgrade not forbidden")
        if config["subagents"]["max_active"] != 1 or not config["subagents"]["sequential_only"]: issues.append("sequential subagent invariant not configured")
        if config["subagents"].get("nested_delegation") is not False: issues.append("nested delegation not disabled")
    except Exception as exc:
        issues.append(f"framework config invalid: {exc}")
    for path in _framework_paths(root, agents):
        if path.is_file() or path.is_symlink():
            try:
                ensure_private_control_file(path)
            except SecurityError as exc:
                issues.append(f"security-sensitive file permissions: {path.relative_to(root)}: {exc}")
        if any(part in {"runtime", "persistent"} for part in path.relative_to(agents).parts):
            continue
        if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}:
            issues.append(f"forbidden generated artifact: {path.relative_to(root)}")
        if path.is_file() and path.suffix == ".py":
            try: compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except Exception as exc: issues.append(f"python syntax: {path.relative_to(root)}: {exc}")
        if path.is_file() and path.suffix == ".toml":
            try:
                with path.open("rb") as stream: tomllib.load(stream)
            except Exception as exc: issues.append(f"TOML syntax: {path.relative_to(root)}: {exc}")
    try:
        modules = discover(root)
        version = (agents / "VERSION").read_text(encoding="utf-8").strip()
        for module_id, info in modules.items():
            module = info["manifest"]["module"]
            if module_id != "example-agent" and module.get("version") != version:
                issues.append(f"module {module_id} version {module.get('version')} differs from framework {version}")
    except Exception as exc:
        issues.append(f"module discovery: {exc}")
    try:
        specs = load_role_specs(root)
        if not (agents / "modules" / "codex" / "roles" / "BASE.md").is_file(): issues.append("Codex shared role invariants missing")
        if not specs: issues.append("Codex role registry empty")
    except Exception as exc:
        issues.append(f"Codex role registry: {exc}")
    source_ok, source_detail = verify_managed_source(root)
    if not source_ok: issues.append(f"Codex managed source: {source_detail}")
    if TOP != {"model": "gpt-5.6-sol", "model_reasoning_effort": "max"}: issues.append("Codex parent model/effort not pinned")
    if AGENTS.get("max_concurrent_threads_per_session") != 1: issues.append("Codex active-child cap not pinned")
    if AGENTS.get("max_depth") != 1: issues.append("Codex nesting defense cap not pinned")
    if V2.get("max_concurrent_threads_per_session") != 2: issues.append("Codex V2 root+one-child cap not pinned")
    if set(ALLOWED) != STATES or any(not targets <= STATES for targets in ALLOWED.values()): issues.append("state transition table is not total over declared states")
    if source_mode:
        try:
            version_report = validate_version_consistency(root)
            if not version_report["ok"]:
                issues.append(f"source version metadata mismatch: {version_report['mismatches']}")
        except Exception as exc:
            issues.append(f"source version validation: {exc}")
    else:
        ok_bootstrap, bootstrap_detail = verify_installed(root)
        if not ok_bootstrap: issues.append(f"root bootstrap: {bootstrap_detail}")
        manifest_ok, manifest_detail = verify(root, require_release_anchor=True)
        if not manifest_ok: issues.append(f"manifest mismatch: {manifest_detail}")
    # Host-supported wrapper validation uses direct argv and bounded execution.
    bash = shutil.which("bash")
    if bash and not (os.name == "nt" and Path(bash).parent.name.casefold() == "system32"):
        for path in sorted((agents / "bin").glob("*.sh")):
            result = run_process([bash, "-n", str(path)], cwd=root, timeout=20, capture_limit=16_000)
            if result.returncode != 0 or result.timed_out: issues.append(f"shell syntax: {path.relative_to(root)}")
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            path = agents / "bin" / "agentctl.ps1"
            parser = "$tokens=$null;$errors=$null;[System.Management.Automation.Language.Parser]::ParseFile($env:AEGIS_PS_PARSE_FILE,[ref]$tokens,[ref]$errors)|Out-Null;if($errors.Count){$errors|ForEach-Object{[Console]::Error.WriteLine($_)};exit 1}"
            result = run_process([powershell, "-NoProfile", "-NonInteractive", "-Command", parser], cwd=root, timeout=20, env={"AEGIS_PS_PARSE_FILE": str(path)}, capture_limit=16_000)
            if result.returncode != 0 or result.timed_out: issues.append(f"PowerShell wrapper invocation: {path.relative_to(root)}")
    xonsh = shutil.which("xonsh")
    if xonsh:
        for path in (agents / "bin" / "agentctl.xsh", agents / "modules" / "xonsh" / "rc.xsh"):
            result = run_process([xonsh, "-n", str(path)], cwd=root, timeout=20, capture_limit=16_000)
            if result.returncode != 0 or result.timed_out: issues.append(f"xonsh syntax: {path.relative_to(root)}")
    return issues
