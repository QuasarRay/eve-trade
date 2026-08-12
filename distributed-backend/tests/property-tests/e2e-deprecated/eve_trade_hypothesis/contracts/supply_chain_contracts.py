from __future__ import annotations

"""Executable, structured oracles for the repository-owned release chain."""

import ast
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

import yaml


@dataclass(frozen=True)
class SupplyChainContractSpec:
    oracle: str
    capabilities: tuple[str, ...]


SUPPLY_CHAIN_CONTRACTS: dict[str, SupplyChainContractSpec] = {
    "test_release_container_image_is_referenced_by_digest_in_production_manifest": SupplyChainContractSpec(
        "rendered_release_digest", ("structured_workflow", "release_renderer", "structured_yaml")
    ),
    "test_release_container_digest_matches_digest_produced_by_ci_build_job": SupplyChainContractSpec(
        "publisher_digest_graph", ("structured_workflow", "oci_raw_manifest", "sha256")
    ),
    "test_ci_refuses_to_deploy_image_built_from_different_commit_than_checked_out_source": SupplyChainContractSpec(
        "commit_identity_gate", ("release_context", "structured_workflow")
    ),
    "test_ci_refuses_mutable_latest_tag_as_production_release_identity": SupplyChainContractSpec(
        "mutable_tag_rejection", ("release_renderer", "publisher_digest_graph")
    ),
    "test_container_base_image_update_changes_locked_or_reviewable_provenance_input": SupplyChainContractSpec(
        "base_image_lock_inventory", ("python_ast", "dockerfile_parser", "sha256")
    ),
}


def binding_metadata(spec: SupplyChainContractSpec) -> dict[str, Any]:
    return {
        "family": "release_supply_chain",
        "oracle": spec.oracle,
        "capabilities": list(spec.capabilities),
    }


def _workflow(root: Path) -> dict[str, Any]:
    value = yaml.safe_load((root / ".github/workflows/verify.yaml").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _release_job(root: Path) -> dict[str, Any]:
    job = (_workflow(root).get("jobs") or {}).get("release-verification")
    assert isinstance(job, dict), "workflow has no release-verification job"
    steps = job.get("steps")
    assert isinstance(steps, list) and steps
    return job


def _release_steps(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    steps = _release_job(root)["steps"]

    def one(predicate) -> tuple[int, dict[str, Any]]:
        matches = [(index, step) for index, step in enumerate(steps) if isinstance(step, dict) and predicate(step)]
        assert len(matches) == 1, matches
        return matches[0]

    build_index, build = one(
        lambda step: str(step.get("run", "")).endswith(".github/dagger/release.py build")
    )
    publisher_index, publisher = one(lambda step: "GHCR_TOKEN" in (step.get("env") or {}))
    verify_index, verify = one(
        lambda step: str(step.get("run", "")).endswith(".github/dagger/release.py verify")
    )
    assert build_index < publisher_index < verify_index
    assert publisher.get("env", {}).get("GHCR_TOKEN") == "${{ secrets.GITHUB_TOKEN }}"
    assert "GHCR_TOKEN" not in (build.get("env") or {})
    assert "GHCR_TOKEN" not in (verify.get("env") or {})
    return build, publisher, verify


IMAGE_ROWS = {
    "encore": ("encore_ref", "encore-backend", "eve-trade-encore-backend"),
    "settlement": ("settlement_ref", "trade-settlement", "eve-trade-trade-settlement"),
    "quilkin": ("quilkin_ref", "quilkin", "eve-trade-quilkin"),
}


def _publisher_model(root: Path) -> dict[str, dict[str, str]]:
    _, publisher, _ = _release_steps(root)
    script = publisher.get("run")
    assert isinstance(script, str)
    assert script.index('test "$VERIFIED_SHA" = "$GITHUB_SHA"') < script.index("docker run")
    assert "unset GHCR_TOKEN" in script

    model: dict[str, dict[str, str]] = {}
    for short, (variable, lock_name, repository_name) in IMAGE_ROWS.items():
        reference = f"ghcr.io/${{RELEASE_OWNER}}/{repository_name}:${{GITHUB_SHA}}"
        assignment = re.findall(
            rf'^\s*{re.escape(variable)}="([^"]+)"\s*$', script, flags=re.MULTILINE
        )
        assert assignment == [reference], (variable, assignment)
        for command in (
            f'docker image inspect "${variable}" >/dev/null',
            f'docker push "${variable}"',
            f'docker buildx imagetools inspect "${variable}" --raw > "$manifest_dir/{short}"',
        ):
            assert sum(line.strip() == command for line in script.splitlines()) == 1, command
        digest_lines = [
            line.strip()
            for line in script.splitlines()
            if re.fullmatch(
                rf'checksum="\$\(sha256sum "\$manifest_dir/{short}"\)"; '
                rf'{short}_digest="\$\{{checksum%% \*\}}"',
                line.strip(),
            )
        ]
        assert len(digest_lines) == 1, (short, digest_lines)
        model[lock_name] = {
            "tag_reference": reference,
            "digest_variable": f"{short}_digest",
            "repository_name": repository_name,
        }

    lines = script.splitlines()
    start = next(
        index for index, line in enumerate(lines)
        if line.strip() == "cat > /out/release-image-lock.json <<JSON"
    )
    end = next(index for index in range(start + 1, len(lines)) if lines[index].strip() == "JSON")
    document = "\n".join(lines[start + 1 : end])
    substitutions = {
        "${GITHUB_REPOSITORY}": "example/eve-trade",
        "${GITHUB_SHA}": "1" * 40,
        "${GITHUB_RUN_ATTEMPT}": "2",
        "${GITHUB_RUN_ID}": "3",
        "${RELEASE_OWNER}": "example",
        "${encore_digest}": "a" * 64,
        "${settlement_digest}": "b" * 64,
        "${quilkin_digest}": "c" * 64,
    }
    for source, target in substitutions.items():
        document = document.replace(source, target)
    lock = json.loads(document)
    assert lock["schema_version"] == "eve-trade.image-lock/v1"
    assert lock["repository"] == "example/eve-trade"
    assert lock["sha"] == "1" * 40
    assert set(lock["images"]) == set(model)
    expected_digests = {"encore-backend": "a", "trade-settlement": "b", "quilkin": "c"}
    for lock_name, row in model.items():
        assert lock["images"][lock_name] == (
            f"ghcr.io/example/{row['repository_name']}@sha256:"
            f"{expected_digests[lock_name] * 64}"
        )
    return model


def _function_node(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _release_context_function(root: Path):
    node = _function_node(root / ".github/dagger/release.py", "_release_context")
    assert isinstance(node, ast.FunctionDef)
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    namespace: dict[str, Any] = {"os": os}
    exec(compile(module, "release_context", "exec"), namespace)
    return namespace["_release_context"]


@contextmanager
def _release_environment(**updates: str) -> Iterator[None]:
    names = {
        "GITHUB_EVENT_NAME", "GITHUB_REF", "GITHUB_SHA", "VERIFIED_SHA",
        "GITHUB_REPOSITORY_OWNER",
    }
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        os.environ.update(updates)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _script_module(path: Path, name: str) -> ModuleType:
    scripts = str(path.parent)
    inserted = scripts not in sys.path
    if inserted:
        sys.path.insert(0, scripts)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if inserted:
            sys.path.remove(scripts)


def _release_renderer(root: Path) -> ModuleType:
    return _script_module(root / "scripts/render_release_kubernetes.py", "eve_trade_release_renderer")


def _verify_release_commands(root: Path) -> None:
    verify = _function_node(root / ".github/dagger/release.py", "verify")
    strings = [
        node.value for node in ast.walk(verify)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    joined = "\n".join(strings)
    commands = (
        "kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod",
        "python scripts/render_release_kubernetes.py",
        "python scripts/verify_rendered_kubernetes.py release-kubernetes.yaml",
    )
    positions = [joined.index(command) for command in commands]
    assert positions == sorted(positions)
    _, _, workflow_verify = _release_steps(root)
    assert workflow_verify.get("env", {}).get("VERIFIED_SHA") == (
        "${{ needs.o11y-aggregate.outputs.verified_sha }}"
    )
    upload = next(
        step for step in _release_job(root)["steps"]
        if isinstance(step, dict) and str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    paths = str((upload.get("with") or {}).get("path", "")).splitlines()
    assert "release-kubernetes.yaml" in {path.strip() for path in paths}
    assert "release-image-lock.json" in {path.strip() for path in paths}


def _validate_rendered_release_digest(root: Path) -> None:
    _verify_release_commands(root)
    renderer = _release_renderer(root)
    sha = "1" * 40
    repository = "example/eve-trade"
    images = {
        "encore-backend": f"ghcr.io/example/eve-trade-encore-backend@sha256:{hashlib.sha256(b'encore').hexdigest()}",
        "quilkin": f"ghcr.io/example/eve-trade-quilkin@sha256:{hashlib.sha256(b'quilkin').hexdigest()}",
        "trade-settlement": f"ghcr.io/example/eve-trade-trade-settlement@sha256:{hashlib.sha256(b'settlement').hexdigest()}",
    }
    resources = []
    for (kind, name, container), lock_name in renderer.TARGETS.items():
        resources.append({
            "apiVersion": "apps/v1",
            "kind": kind,
            "metadata": {"name": name},
            "spec": {"template": {"spec": {"containers": [{"name": container, "image": "template.invalid/image:template"}]}}},
        })
    with tempfile.TemporaryDirectory(prefix="eve-trade-release-contract-") as temporary:
        temporary_root = Path(temporary)
        manifest = temporary_root / "manifest.yaml"
        lock = temporary_root / "lock.json"
        manifest.write_text(yaml.safe_dump_all(resources), encoding="utf-8")
        lock.write_text(json.dumps({
            "schema_version": renderer.LOCK_SCHEMA,
            "repository": repository,
            "sha": sha,
            "images": images,
        }), encoding="utf-8")
        loaded = renderer.load_lock(lock, repository, sha)
        rendered = renderer.render(manifest, loaded)
    observed: dict[str, str] = {}
    for resource in rendered:
        spec = resource["spec"]["template"]["spec"]
        for container in spec["containers"]:
            key = renderer.TARGETS[(resource["kind"], resource["metadata"]["name"], container["name"])]
            observed[key] = container["image"]
    assert observed == images
    assert all("@sha256:" in reference and ":latest" not in reference for reference in observed.values())


def _validate_publisher_digest_graph(root: Path) -> None:
    model = _publisher_model(root)
    build = _function_node(root / ".github/dagger/release.py", "build")
    assignments: dict[str, ast.JoinedStr] = {}
    for node in ast.walk(build):
        if (
            isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.JoinedStr)
        ):
            assignments[node.targets[0].id] = node.value
    for _, (variable, lock_name, repository_name) in IMAGE_ROWS.items():
        value = assignments.get(variable)
        assert value is not None
        constants = "".join(
            part.value for part in value.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
        names = [
            part.value.id for part in value.values
            if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
        ]
        assert constants == f"ghcr.io//{repository_name}:"
        assert names == ["owner", "sha"]
        assert model[lock_name]["repository_name"] == repository_name
    _verify_release_commands(root)


def _validate_commit_identity_gate(root: Path) -> None:
    context = _release_context_function(root)
    sha = "1" * 40
    base = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY_OWNER": "Example",
    }
    with _release_environment(**base, GITHUB_SHA=sha, VERIFIED_SHA=sha):
        assert context() == (sha, sha, "example")
    with _release_environment(**base, GITHUB_SHA=sha, VERIFIED_SHA="2" * 40):
        try:
            context()
        except RuntimeError as error:
            assert "provenance mismatch" in str(error)
        else:
            raise AssertionError("release context accepted a build from a different commit")
    job = _release_job(root)
    condition = str(job.get("if", ""))
    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/main'" in condition
    assert "needs.o11y-aggregate.outputs.verified_sha == github.sha" in condition
    for step in _release_steps(root):
        if str(step.get("run", "")).endswith(("release.py build", "release.py verify")):
            assert (step.get("env") or {}).get("VERIFIED_SHA") == (
                "${{ needs.o11y-aggregate.outputs.verified_sha }}"
            )


def _validate_mutable_tag_rejection(root: Path) -> None:
    _publisher_model(root)
    _verify_release_commands(root)
    renderer = _release_renderer(root)
    with tempfile.TemporaryDirectory(prefix="eve-trade-release-tag-") as temporary:
        lock = Path(temporary) / "lock.json"
        lock.write_text(json.dumps({
            "schema_version": renderer.LOCK_SCHEMA,
            "repository": "example/eve-trade",
            "sha": "1" * 40,
            "images": {
                "encore-backend": "ghcr.io/example/eve-trade-encore-backend:latest",
                "quilkin": f"ghcr.io/example/eve-trade-quilkin@sha256:{hashlib.sha256(b'quilkin').hexdigest()}",
                "trade-settlement": f"ghcr.io/example/eve-trade-trade-settlement@sha256:{hashlib.sha256(b'settlement').hexdigest()}",
            },
        }), encoding="utf-8")
        try:
            renderer.load_lock(lock, "example/eve-trade", "1" * 40)
        except ValueError as error:
            assert "mutable" in str(error)
        else:
            raise AssertionError("release renderer accepted a mutable latest tag")


DIGEST_REFERENCE = re.compile(r"^[^\s@]+(?::[^\s@]+)?@sha256:[0-9a-f]{64}$")


def _validate_base_image_lock_inventory(root: Path) -> None:
    images_path = root / ".github/dagger/_images.py"
    images_tree = ast.parse(images_path.read_text(encoding="utf-8"), filename=str(images_path))
    values = {
        node.targets[0].id: node.value.value
        for node in images_tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id.endswith("_IMAGE")
        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    }
    assert values and all(DIGEST_REFERENCE.fullmatch(value) for value in values.values())
    for path in (root / ".github/dagger").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "from_"):
                continue
            assert len(node.args) == 1 and isinstance(node.args[0], ast.Name)
            assert node.args[0].id in values, (path.name, node.lineno)

    release_source = (root / ".github/dagger/release.py").read_text(encoding="utf-8")
    dockerfiles = set(re.findall(r"--file ([^\s]+\.Dockerfile)", release_source))
    assert dockerfiles == {
        "distributed-backend/docker/trade-settlement.Dockerfile",
        "distributed-backend/docker/quilkin.Dockerfile",
    }
    for relative in dockerfiles:
        from_lines = [
            line.strip().split()[1]
            for line in (root / relative).read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("FROM ")
        ]
        assert from_lines and all(DIGEST_REFERENCE.fullmatch(value) for value in from_lines), (
            relative, from_lines
        )
    release_tree = ast.parse(release_source)
    constants = {
        node.targets[0].id: node.value.value
        for node in release_tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    }
    assert re.fullmatch(r"\d+\.\d+\.\d+", constants.get("ENCORE_VERSION", ""))
    assert re.fullmatch(r"[0-9a-f]{64}", constants.get("ENCORE_SHA256", ""))


def validate_supply_chain_contract(name: str, root: Path) -> None:
    assert name in SUPPLY_CHAIN_CONTRACTS, f"unknown supply-chain contract: {name}"
    oracle = SUPPLY_CHAIN_CONTRACTS[name].oracle
    if oracle == "rendered_release_digest":
        _validate_rendered_release_digest(root)
        return
    if oracle == "publisher_digest_graph":
        _validate_publisher_digest_graph(root)
        return
    if oracle == "commit_identity_gate":
        _validate_commit_identity_gate(root)
        return
    if oracle == "mutable_tag_rejection":
        _validate_mutable_tag_rejection(root)
        return
    if oracle == "base_image_lock_inventory":
        _validate_base_image_lock_inventory(root)
        return
    raise AssertionError(f"unhandled supply-chain oracle: {oracle}")
