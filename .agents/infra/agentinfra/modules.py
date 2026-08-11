from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import os
import re
import shutil
import stat
import sys
import tempfile
import tomllib
from pathlib import Path

from .paths import framework_dir, persistent_dir
from .process import run_process
from .security import confined_path
from .transaction import FileTransaction, Mutation
from .workspace import workspace_fingerprint


class ModuleError(RuntimeError):
    pass


ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
KINDS = {"agent-host", "agent-host-template", "environment", "developer-extension"}
HARD_INVARIANT_ADAPTERS = {"codex", "xonsh", "python-meta"}
TOP_KEYS = {"module", "detect", "install", "python"}
MODULE_KEYS = {"id", "name", "version", "kind", "policy", "requires_framework", "replaces", "dependencies"}
DETECT_KEYS = {"executables", "environment_any", "python_packages_all"}
INSTALL_KEYS = {"command", "verify", "uninstall", "writes"}
PYTHON_KEYS = {"optional_dependencies"}


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int
    # A release sorts after its pre-release.  Tuple comparison cannot express that
    # directly, so requirement checks use _compare_versions below.
    prerelease: tuple[str | int, ...] = ()


def _version(value: str) -> Version:
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", value.strip())
    if not match:
        raise ModuleError(f"unsupported semantic version: {value!r}")
    parts: list[str | int] = []
    if match.group(4):
        for item in match.group(4).split("."):
            if not item or (item.isdigit() and len(item) > 1 and item.startswith("0")):
                raise ModuleError(f"invalid semantic-version prerelease: {value!r}")
            parts.append(int(item) if item.isdigit() else item)
    return Version(int(match.group(1)), int(match.group(2)), int(match.group(3)), tuple(parts))


def _compare_identifiers(left: tuple[str | int, ...], right: tuple[str | int, ...]) -> int:
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for a, b in zip(left, right):
        if a == b:
            continue
        if isinstance(a, int) and isinstance(b, str):
            return -1
        if isinstance(a, str) and isinstance(b, int):
            return 1
        return -1 if a < b else 1
    return (len(left) > len(right)) - (len(left) < len(right))


def _compare_versions(left: Version, right: Version) -> int:
    core_left = (left.major, left.minor, left.patch)
    core_right = (right.major, right.minor, right.patch)
    if core_left != core_right:
        return (core_left > core_right) - (core_left < core_right)
    return _compare_identifiers(left.prerelease, right.prerelease)


def _framework_version(root: Path) -> Version:
    try:
        return _version((framework_dir(root) / "VERSION").read_text(encoding="utf-8").strip())
    except FileNotFoundError as exc:
        raise ModuleError("framework VERSION is missing") from exc


def _requires_ok(current: Version, requirement: str) -> bool:
    if not isinstance(requirement, str) or not requirement.strip():
        raise ModuleError("requires_framework must be a non-empty constraint string")
    for raw in requirement.split(","):
        term = raw.strip()
        match = re.fullmatch(r"(>=|<=|==|>|<)\s*([^\s,]+)", term)
        if not match:
            raise ModuleError(f"unsupported requires_framework constraint: {term!r}")
        operation, wanted = match.group(1), _version(match.group(2))
        comparison = _compare_versions(current, wanted)
        checks = {">=": comparison >= 0, "<=": comparison <= 0, "==": comparison == 0, ">": comparison > 0, "<": comparison < 0}
        if not checks[operation]:
            return False
    return True


def _string_array(value, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ModuleError(f"{label} must be an array of non-empty strings")
    if not allow_empty and not value:
        raise ModuleError(f"{label} must not be empty")
    return value


def _only_keys(table: dict, allowed: set[str], label: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ModuleError(f"unknown {label} field(s): {', '.join(unknown)}")


def _read_manifest(path: Path) -> dict:
    if path.is_symlink():
        raise ModuleError(f"module manifest must not be a symlink: {path}")
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ModuleError(f"invalid module manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ModuleError(f"invalid module manifest: {path}")
    _only_keys(data, TOP_KEYS, "top-level module manifest")
    module = data.get("module")
    if not isinstance(module, dict):
        raise ModuleError(f"module table is required: {path}")
    _only_keys(module, MODULE_KEYS, "module")
    required = {"id", "name", "version", "kind", "policy"}
    missing = sorted(required - set(module))
    if missing:
        raise ModuleError(f"module manifest missing field(s): {', '.join(missing)}")
    if not isinstance(module["id"], str) or not ID_RE.fullmatch(module["id"]):
        raise ModuleError("module id must be canonical lowercase letters/digits/hyphens")
    if not isinstance(module["name"], str) or not module["name"].strip():
        raise ModuleError("module name must be a non-empty string")
    _version(module["version"])
    if module["kind"] not in KINDS:
        raise ModuleError(f"unknown module kind: {module['kind']!r}")
    _string_array(module["policy"], "module.policy", allow_empty=False)
    if "replaces" in module and (not isinstance(module["replaces"], str) or not ID_RE.fullmatch(module["replaces"])):
        raise ModuleError("module.replaces must be a canonical module id")
    if "dependencies" in module:
        dependencies = _string_array(module["dependencies"], "module.dependencies")
        if any(not ID_RE.fullmatch(item) for item in dependencies) or len(dependencies) != len(set(dependencies)):
            raise ModuleError("module.dependencies contains invalid or duplicate ids")
    for table_name, allowed in (("detect", DETECT_KEYS), ("install", INSTALL_KEYS), ("python", PYTHON_KEYS)):
        table = data.get(table_name, {})
        if not isinstance(table, dict):
            raise ModuleError(f"{table_name} must be a table")
        _only_keys(table, allowed, table_name)
        for key, value in table.items():
            _string_array(value, f"{table_name}.{key}", allow_empty=False)
    for package in data.get("detect", {}).get("python_packages_all", []) + data.get("python", {}).get("optional_dependencies", []):
        if not PACKAGE_RE.fullmatch(package):
            raise ModuleError(f"unsafe Python package name: {package!r}")
    return data


def _validate_module_paths(info: dict) -> None:
    module_root = info["path"].resolve(strict=True)
    for value in info["manifest"]["module"]["policy"]:
        policy = confined_path(module_root, value, must_exist=True, reject_symlinks=True)
        if not policy.is_file():
            raise ModuleError(f"module policy is not a regular file: {value}")
    for action, argv in info["manifest"].get("install", {}).items():
        if action == "writes":
            continue
        if not argv:
            raise ModuleError(f"module {action} action is empty")
        first = argv[0]
        if first in {"python", "{python}"}:
            if len(argv) < 2:
                raise ModuleError(f"module {action} Python action has no script")
            script = Path(argv[1])
            if script.is_absolute() or ".." in script.parts:
                raise ModuleError(f"unsafe module action script path: {argv[1]}")
            if script.parts and script.parts[0] == ".agents" and framework_dir(info["root"]) == info["root"]:
                script = Path(*script.parts[1:])
            target = confined_path(info["root"], script, must_exist=True, reject_symlinks=True)
            try:
                target.relative_to(module_root)
            except ValueError as exc:
                raise ModuleError(f"module action script escapes its module: {argv[1]}") from exc
            if not target.is_file():
                raise ModuleError(f"module action script is not a file: {argv[1]}")
        elif Path(first).is_absolute() or any(separator in first for separator in ("/", "\\")) or first.lower() in {"sh", "bash", "cmd", "cmd.exe", "powershell", "pwsh"}:
            raise ModuleError(f"unsafe module action executable: {first!r}")


def _check_dependency_graph(found: dict[str, dict]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module_id: str, stack: list[str]) -> None:
        if module_id in visiting:
            raise ModuleError("module dependency cycle: " + " -> ".join(stack + [module_id]))
        if module_id in visited:
            return
        visiting.add(module_id)
        for dependency in found[module_id]["manifest"]["module"].get("dependencies", []):
            if dependency not in found:
                raise ModuleError(f"module {module_id!r} has missing dependency {dependency!r}")
            visit(dependency, stack + [module_id])
        visiting.remove(module_id)
        visited.add(module_id)

    for module_id in sorted(found):
        visit(module_id, [])


def discover(root: Path) -> dict[str, dict]:
    root = root.resolve(strict=True)
    found: dict[str, dict] = {}
    framework = framework_dir(root)
    bases = [(framework / "modules", "built-in"), (framework / "local-modules", "local")]
    for base, source in bases:
        if not base.exists():
            continue
        if base.is_symlink():
            raise ModuleError(f"module root must not be a symlink: {base}")
        for path in sorted(base.glob("*/module.toml")):
            data = _read_manifest(path)
            module_id = data["module"]["id"]
            if path.parent.name != module_id:
                raise ModuleError(f"module directory {path.parent.name!r} does not match manifest id {module_id!r}")
            requirement = data["module"].get("requires_framework")
            if requirement and not _requires_ok(_framework_version(root), requirement):
                current_text = (framework / "VERSION").read_text(encoding="utf-8").strip()
                raise ModuleError(f"module {module_id!r} requires framework {requirement}, current version is {current_text}")
            info = {"root": root, "path": path.parent, "manifest": data, "source": source}
            _validate_module_paths(info)
            if module_id in found:
                previous = found[module_id]
                replaces = data["module"].get("replaces")
                if source != "local" or previous["source"] != "built-in" or replaces != module_id:
                    raise ModuleError(f"duplicate module id {module_id!r}; local replacement must explicitly replace that built-in id")
                if module_id in HARD_INVARIANT_ADAPTERS:
                    raise ModuleError(f"local replacement of protected hard-invariant adapter {module_id!r} is forbidden")
                info["replaced"] = previous
                found[module_id] = info
            elif data["module"].get("replaces"):
                raise ModuleError(f"module {module_id!r} declares replacement but no built-in module exists")
            else:
                found[module_id] = info
    _check_dependency_graph(found)
    return found


def _package_available(name: str) -> bool:
    try:
        importlib.metadata.version(name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def detected(info: dict) -> bool:
    detection = info["manifest"].get("detect", {})
    executables = detection.get("executables", [])
    environments = detection.get("environment_any", [])
    packages = detection.get("python_packages_all", [])
    exe_ok = any(shutil.which(name) for name in executables) if executables else False
    env_ok = any(os.environ.get(name) for name in environments) if environments else False
    package_ok = bool(packages) and all(_package_available(name) for name in packages)
    return exe_ok or env_ok or package_ok


def optional_python_packages() -> dict[str, bool]:
    return {name: _package_available(name) for name in ("mcpyrate", "unpythonic", "xonsh")}


def action_command(info: dict, action: str) -> list[str]:
    install = info["manifest"].get("install", {})
    key = "command" if action == "install" else action
    command = install.get(key)
    if not command:
        raise ModuleError(f"module {info['manifest']['module']['id']} has no {action!r} action")
    # Discovery already validated this, but retain a boundary check for callers that
    # construct info objects directly.
    _string_array(command, "module action", allow_empty=False)
    return list(command)


def _declared_write_roots(root: Path, info: dict) -> list[Path]:
    declared = info["manifest"].get("install", {}).get("writes", [])
    _string_array(declared, "install.writes")
    module_id = info["manifest"]["module"]["id"]
    trusted_codex = info.get("source") == "built-in" and module_id == "codex"
    protected_top = {
        ".agents", ".git", "AGENTS.md", "INDEX.md", "framework.toml", "VERSION",
        "MANIFEST.sha256", "RELEASE.json", "bin", "bootstrap", "core", "infra",
        "laws", "modules", "protocols", "scripts", "templates", "tests-to-impl",
    }
    out: list[Path] = []
    for value in declared:
        raw = Path(value)
        if raw.is_absolute() or ".." in raw.parts or not raw.parts or raw == Path("."):
            raise ModuleError(f"unsafe declared module write root: {value!r}")
        relative = Path(*raw.parts)
        first = relative.parts[0]
        trusted_internal_state = (
            trusted_codex
            and relative.parts[:4] == (".agents", "persistent", "install-state", "codex")
        )
        if first in protected_top and not trusted_internal_state:
            raise ModuleError(f"module write root targets protected framework/user policy: {value!r}")
        if first == ".codex" and not trusted_codex:
            raise ModuleError("only the protected built-in Codex adapter may mutate .codex")
        out.append(confined_path(root, relative, reject_symlinks=True))
    return out


def _mutation_is_declared(path: Path, roots: list[Path]) -> bool:
    for allowed in roots:
        if path == allowed:
            return True
        try:
            path.relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


def _action_snapshot(root: Path) -> tuple[dict[str, tuple[bytes, int]], dict[str, str]]:
    files: dict[str, tuple[bytes, int]] = {}
    links: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if (
            relative == ".git" or relative.startswith(".git/")
            or relative == ".agents/runtime" or relative.startswith(".agents/runtime/")
            or relative == ".agents/persistent/transactions" or relative.startswith(".agents/persistent/transactions/")
            or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        if path.is_symlink():
            links[relative] = str(path.readlink())
        elif path.is_file():
            files[relative] = (path.read_bytes(), stat.S_IMODE(path.stat(follow_symlinks=False).st_mode))
    return files, links


def run_action(root: Path, info: dict, action: str, *, apply: bool = False, timeout: float = 120.0) -> dict:
    if action not in {"install", "verify", "uninstall"}:
        raise ModuleError(f"unsupported module action: {action!r}")
    root = root.resolve(strict=True)
    if info.get("root", root).resolve() != root:
        raise ModuleError("module metadata belongs to a different framework root")
    _validate_module_paths({**info, "root": root})
    command = action_command(info, action)
    if framework_dir(root) == root:
        command = [
            str(Path(*Path(item).parts[1:]))
            if Path(item).parts and Path(item).parts[0] == ".agents"
            else item
            for item in command
        ]
    declared_write_roots = _declared_write_roots(root, info)
    if command[0] in {"python", "{python}"}:
        command[0] = sys.executable
    if action in {"install", "uninstall"} and apply:
        command.append("--apply")
    must_be_read_only = action == "verify" or (action in {"install", "uninstall"} and not apply)
    source_before = _action_snapshot(root)
    with tempfile.TemporaryDirectory(prefix="aegis-module-action-") as directory:
        isolated = Path(directory) / "workspace"
        shutil.copytree(
            root,
            isolated,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "runtime", "transactions", "__pycache__", "*.pyc", "*.pyo"),
        )
        isolated_before = _action_snapshot(isolated)
        result = run_process(command, cwd=isolated, timeout=timeout, capture_limit=64_000)
        isolated_after = _action_snapshot(isolated)
        stable = isolated_before == isolated_after
        transactional = False
        if not must_be_read_only and not result.timed_out and result.returncode == 0:
            before_files, before_links = source_before
            after_files, after_links = isolated_after
            if before_links != after_links:
                raise ModuleError("module action attempted unsupported symlink topology mutation")
            mutations = []
            for relative in sorted(set(before_files) | set(after_files)):
                prior = before_files.get(relative)
                updated = after_files.get(relative)
                if prior == updated:
                    continue
                destination = confined_path(root, relative, reject_symlinks=True)
                if not _mutation_is_declared(destination, declared_write_roots):
                    raise ModuleError(f"module action mutated undeclared or protected path: {relative}")
                mutations.append(
                    Mutation(
                        destination,
                        None if updated is None else updated[0],
                        expected_sha256=None if prior is None else hashlib.sha256(prior[0]).hexdigest(),
                        expected_exists=prior is not None,
                        mode=None if updated is None else updated[1],
                    )
                )
            if mutations:
                FileTransaction(
                    root,
                    mutations,
                    state_dir=persistent_dir(root) / "transactions",
                    name=f"module-{info['manifest']['module']['id']}-{action}",
                ).commit(retain=False)
            transactional = True
    source_after = _action_snapshot(root)
    source_stable = source_before == source_after
    effective_exit = result.returncode
    if must_be_read_only and not stable:
        effective_exit = 70
    if must_be_read_only and not source_stable:
        raise ModuleError("isolated read-only module action unexpectedly mutated the source workspace")
    return {
        "command": list(result.argv),
        "exit": effective_exit,
        "process_exit": result.returncode,
        "timed_out": result.timed_out,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_sha256": result.stdout_sha256,
        "stderr_sha256": result.stderr_sha256,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "read_only_required": must_be_read_only,
        "workspace_stable": stable,
        "source_workspace_stable": source_stable,
        "transactional_commit": transactional,
        "declared_write_roots": [path.relative_to(root).as_posix() for path in declared_write_roots],
    }


def scaffold(root: Path, module_id: str, kind: str = "agent-host") -> dict:
    root = root.resolve(strict=True)
    if not isinstance(module_id, str) or not ID_RE.fullmatch(module_id) or len(module_id) < 2:
        raise ModuleError("module id must be 2-64 lowercase letters/digits/hyphens")
    if kind not in KINDS:
        raise ModuleError(f"unknown module kind: {kind!r}")
    # Local module source belongs beside the authoritative framework surface:
    # ``root/local-modules`` in a source checkout, and the immutable deployed
    # ``.agents/local-modules`` surface otherwise.  The latter remains guarded
    # by FileTransaction and therefore fails closed for ordinary callers.
    base = framework_dir(root) / "local-modules"
    base.mkdir(parents=True, exist_ok=True)
    destination = confined_path(base.resolve(), module_id, reject_symlinks=True)
    if destination.exists():
        raise ModuleError(f"module already exists: {destination}")
    destination.mkdir()
    manifest = (
        "[module]\n"
        f'id = "{module_id}"\n'
        f'name = "{module_id}"\n'
        'version = "0.1.0"\n'
        f'kind = "{kind}"\n'
        'policy = ["POLICY.md"]\n'
        'requires_framework = ">=4.0.0,<5.0.0"\n'
    ).encode("utf-8")
    policy = f"# {module_id} module policy\n\nAdd host-specific strengthening here. Never weaken root Aegis invariants.\n".encode("utf-8")
    try:
        FileTransaction(
            root,
            [Mutation(destination / "module.toml", manifest, expected_exists=False, mode=0o644), Mutation(destination / "POLICY.md", policy, expected_exists=False, mode=0o644)],
            state_dir=persistent_dir(root) / "transactions",
            name="module-scaffold",
        ).commit(retain=False)
    except BaseException:
        if destination.exists() and not any(destination.iterdir()):
            destination.rmdir()
        raise
    return {"id": module_id, "path": str(destination), "kind": kind}
