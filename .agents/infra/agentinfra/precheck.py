from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from .atomic import atomic_write_json
from .constitution import constitutional_contract
from .discovery import discover_repository, write_discovery_artifact
from .governance import capture_governance
from .policy import classify_instruction_source, compile_contract, write_compiled_policy
from .scope import compile_write_scope
from .workspace import workspace_fingerprint


class PrecheckError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _artifact(kind: str, payload: object) -> dict:
    body = {"schema": 1, "kind": kind, "payload": payload}
    body["digest"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _test_law_baseline(root: Path) -> dict:
    entries: list[dict] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or ".aegis" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root)
        parts = {part.casefold() for part in relative.parts}
        selected = (
            path.name.startswith("test_") and path.suffix == ".py"
        ) or bool(parts & {"tests", "law_tests", "tests-to-impl", "laws"})
        if selected:
            entries.append(
                {
                    "path": relative.as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha_file(path),
                }
            )
    return _artifact("test-law-baseline", {"entries": entries, "count": len(entries)})


def _instruction_provenance(root: Path, discovery: dict) -> dict:
    records: list[dict] = []
    for relative in discovery.get("instruction_hierarchy", []):
        path = root / relative
        records.append(
            classify_instruction_source(
                kind="project-governance",
                source=relative,
                content=path.read_text(encoding="utf-8"),
            )
        )
    # Deployed constitutional policy is authoritative input but never writable.
    for relative in discovery.get("governance_roots", []):
        records.append(
            classify_instruction_source(
                kind="constitution",
                source=relative,
                content="deployed immutable Aegis governance root",
            )
        )
    return _artifact("instruction-provenance", {"records": records})


def build_precheck(
    root: Path,
    *,
    project_contract: dict,
    declared_classes: Iterable[str],
    changed_paths: Iterable[str],
    risk: str,
    test_contract_digest: str,
    oracle_digest: str,
    prior_classes: Iterable[str] = (),
) -> dict:
    project = Path(root).resolve(strict=True)
    if not all(
        isinstance(value, str) and len(value) == 64 and value == value.casefold()
        for value in (test_contract_digest, oracle_digest)
    ):
        raise PrecheckError("precheck requires canonical test and oracle digests")
    governance = capture_governance(project)
    constitution = constitutional_contract()
    discovery = discover_repository(project)
    write_discovery_artifact(project, discovery)
    workspace = workspace_fingerprint(project)
    if workspace.get("available") is not True:
        raise PrecheckError("workspace snapshot is unavailable: " + str(workspace.get("reason")))
    compiled = compile_contract(
        project_contract,
        declared_classes=declared_classes,
        changed_paths=changed_paths,
        risk=risk,
        prior_classes=prior_classes,
    )
    write_compiled_policy(project, compiled)

    scope_data = compiled["scope"]
    write_scope = compile_write_scope(
        allow=scope_data["allow"],
        deny=scope_data["deny"],
        test_paths=["tests/**", "infra/tests/**", "infra/property_tests/**", "infra/law_tests/**"],
        production_paths=project_contract.get("boundaries", {}).get("source", []),
        generated_paths=scope_data["generated"],
        reference_paths=scope_data["reference"],
        user_dirty=discovery.get("dirty_tracked", []),
        nested_repositories=discovery.get("nested_repositories", []),
        governance_digest=governance["digest"],
    )
    artifacts = {
        "governance_snapshot": governance,
        "constitution": constitution,
        "instruction_provenance": _instruction_provenance(project, discovery),
        "repository_discovery": discovery,
        "workspace_snapshot": _artifact("workspace-snapshot", workspace),
        "test_law_baseline": _test_law_baseline(project),
        "tdd_plan": _artifact(
            "tdd-plan",
            {
                "mode": compiled["task"]["tdd_mode"],
                "test_contract_digest": test_contract_digest,
                "oracle_digest": oracle_digest,
                "property_first": any(
                    pack in {"python-control-plane", "agent-framework", "distributed-system", "rust-concurrency"}
                    for pack in compiled["policy_packs"]
                ),
                "implementation_authorized": False,
            },
        ),
        "compiled_policy": compiled,
        "mandatory_gates": _artifact("mandatory-gates", compiled["gates"]),
        "write_scope": write_scope,
        "budgets": _artifact(
            "budgets",
            {"change": compiled["change_budget"], "semantic": compiled["semantic_budget"]},
        ),
        "command_matrix": _artifact("command-matrix", compiled["commands"]),
        "review_requirements": _artifact("review-requirements", compiled["review"]),
    }
    if any(not isinstance(value.get("digest"), str) or len(value["digest"]) != 64 for value in artifacts.values()):
        raise PrecheckError("precheck produced an unbound artifact")
    body = {
        "schema": 1,
        "project_root": str(project),
        "artifacts": artifacts,
        "compiled_policy_digest": compiled["digest"],
    }
    body["digest"] = hashlib.sha256(_canonical(body)).hexdigest()
    destination = project / ".aegis" / "audit" / f"precheck-{body['digest']}.json"
    atomic_write_json(destination, body, root=project, mode=0o600)
    return {**body, "path": str(destination)}
