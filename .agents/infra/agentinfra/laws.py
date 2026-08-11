from __future__ import annotations

import hashlib
import json
import operator
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .process import ProcessResult, run_process
from .security import confined_path
from .workspace import workspace_fingerprint


OPS = {"eq": operator.eq, "ne": operator.ne, "lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge}
KINDS = {"file_exists", "file_contains", "file_not_contains", "regex", "command", "json_command", "command_sequence", "differential_json"}
SEVERITIES = {"hard", "soft"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
MAX_REGEX = 4096
MAX_CAPTURE = 1_000_000


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dotted(obj, path):
    current = obj
    for part in path.split("."):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def _safe_regex(pattern: str) -> re.Pattern:
    if not isinstance(pattern, str) or len(pattern) > MAX_REGEX:
        raise ValueError("regex must be a bounded string")
    # Reject common nested-quantifier shapes that can cause catastrophic backtracking.
    if re.search(r"\([^)]*[+*][^)]*\)[+*{]", pattern) or re.search(r"(?:\.\*){2,}|(?:\.\+){2,}", pattern):
        raise ValueError("potentially catastrophic regex is not allowed")
    return re.compile(pattern, re.MULTILINE)


def _run(command, root: Path, timeout=60, *, capture_limit=MAX_CAPTURE) -> ProcessResult:
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("law command must be a non-empty argv string array")
    command = list(command)
    if command[0] == "{python}":
        command[0] = sys.executable
    return run_process(command, cwd=root, timeout=float(timeout), capture_limit=capture_limit)


@dataclass
class LawResult:
    id: str
    passed: bool
    detail: str
    severity: str = "hard"
    outcome: str = "PASS"
    capability_status: str = "AVAILABLE"
    oracle_count: int = 1
    metadata: dict = field(default_factory=dict)


class _DefinitionWatcher:
    def __init__(self, paths: list[Path]):
        self.paths = paths
        self.before = self._snapshot()
        self.changed = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.native_handles: list[object] = []
        self.native_threads: list[threading.Thread] = []

    def _snapshot(self):
        out = {}
        for path in self.paths:
            if not path.is_file():
                out[str(path)] = None
                continue
            info = path.stat(follow_symlinks=False)
            out[str(path)] = (digest(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode))
        return out

    def _watch(self):
        while not self.stop.wait(0.005):
            if self._snapshot() != self.before:
                self.changed.set()

    def _watch_windows_directory(self, handle) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        read_changes = kernel32.ReadDirectoryChangesW
        read_changes.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p,
        ]
        read_changes.restype = wintypes.BOOL
        notify_filter = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000008 | 0x00000010 | 0x00000040
        buffer = ctypes.create_string_buffer(16 * 1024)
        returned = wintypes.DWORD()
        while not self.stop.is_set():
            ok = read_changes(handle, buffer, len(buffer), False, notify_filter, ctypes.byref(returned), None, None)
            if not ok:
                error = ctypes.get_last_error()
                if self.stop.is_set() or error in {6, 995}:  # invalid handle / operation cancelled
                    return
                self.changed.set()
                return
            if returned.value:
                self.changed.set()

    def _start_windows_watchers(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        invalid = ctypes.c_void_p(-1).value
        for directory in sorted({path.parent.resolve() for path in self.paths if path.parent.is_dir()}, key=str):
            handle = create_file(
                str(directory),
                0x0001,  # FILE_LIST_DIRECTORY
                0x00000001 | 0x00000002 | 0x00000004,  # share read/write/delete
                None,
                3,  # OPEN_EXISTING
                0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
                None,
            )
            if handle == invalid:
                raise OSError(ctypes.get_last_error(), f"cannot watch protected definition directory {directory}")
            self.native_handles.append(handle)
            thread = threading.Thread(target=self._watch_windows_directory, args=(handle,), daemon=True)
            self.native_threads.append(thread)
            thread.start()

    def _stop_windows_watchers(self) -> None:
        if not self.native_handles:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel32.CancelIoEx.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        for handle in self.native_handles:
            kernel32.CancelIoEx(handle, None)
        for thread in self.native_threads:
            thread.join(timeout=2)
        for handle in self.native_handles:
            kernel32.CloseHandle(handle)
        self.native_handles.clear()
        self.native_threads.clear()

    def __enter__(self):
        if os.name == "nt":
            try:
                self._start_windows_watchers()
            except OSError:
                self._stop_windows_watchers()
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self._stop_windows_watchers()
        self.thread.join(timeout=2)
        if self._snapshot() != self.before:
            self.changed.set()


class LawRunner:
    """Fail-closed production-boundary law runner with bounded isolated subprocesses."""

    def __init__(self, root: Path, *, acceptance_required: bool = True):
        self.root = root.resolve(strict=True)
        self.acceptance_required = acceptance_required
        self.source_mode = all(
            path.is_dir()
            for path in (
                self.root / "infra" / "tests",
                self.root / "infra" / "laws",
                self.root / "infra" / "law_tests",
                self.root / "tests-to-impl",
            )
        )

    def _portable_framework_value(self, value: str | Path) -> str | Path:
        if not self.source_mode or Path(value).is_absolute():
            return value
        path = Path(value)
        if path.parts and path.parts[0].casefold() == ".agents":
            return Path(*path.parts[1:])
        return value

    def _law_path(self, value: str | Path) -> Path:
        return confined_path(
            self.root,
            self._portable_framework_value(value),
            must_exist=True,
            reject_symlinks=True,
        )

    def _expand_command(self, command) -> list[str]:
        expanded = []
        for argument in command:
            if argument == "{python}":
                expanded.append(argument)
                continue
            translated = self._portable_framework_value(argument)
            expanded.append(translated.as_posix() if isinstance(translated, Path) else translated)
        return expanded

    def _validate_command(self, value, law_id: str) -> None:
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
            raise ValueError(f"law {law_id} command must be a non-empty argv string array")
        root_text = str(self.root).replace("\\", "/").casefold()
        for argument in value[1:]:
            normalized = argument.replace("\\", "/").casefold()
            if root_text and root_text in normalized:
                raise ValueError(f"law {law_id} embeds the authoritative workspace path in a subprocess argument")

    def _validate_law(self, law: dict) -> None:
        law_id = law.get("id")
        if not isinstance(law_id, str) or not ID_RE.fullmatch(law_id):
            raise ValueError("all laws require a canonical non-empty id")
        if not isinstance(law.get("description"), str) or not law["description"].strip():
            raise ValueError(f"law {law_id} requires a description")
        kind = law.get("kind")
        if kind not in KINDS:
            raise ValueError(f"law {law_id} has unsupported kind {kind!r}")
        if law.get("severity", "hard") not in SEVERITIES:
            raise ValueError(f"law {law_id} has invalid severity")
        timeout = law.get("timeout_seconds", 60)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0 or timeout != timeout or timeout == float("inf"):
            raise ValueError(f"law {law_id} has invalid timeout")
        if kind.startswith("file_") or kind == "regex":
            if not isinstance(law.get("path"), str) or not law["path"]:
                raise ValueError(f"law {law_id} requires a path")
            self._law_path(law["path"])
        if kind in {"file_contains", "file_not_contains"} and not isinstance(law.get("text"), str):
            raise ValueError(f"law {law_id} requires text")
        if kind == "regex":
            _safe_regex(law.get("pattern"))
        if kind in {"command", "json_command"}:
            self._validate_command(law.get("command"), law_id)
        if kind == "json_command":
            if not isinstance(law.get("json_path"), str) or not law["json_path"] or "value" not in law:
                raise ValueError(f"law {law_id} json_command requires json_path and value")
            if law.get("operator", "eq") not in OPS:
                raise ValueError(f"law {law_id} has unknown JSON comparison operator")
        if kind == "command_sequence":
            steps = law.get("steps")
            if not isinstance(steps, list) or not steps:
                raise ValueError(f"law {law_id} command_sequence requires at least one step")
            for step in steps:
                if not isinstance(step, dict):
                    raise ValueError(f"law {law_id} sequence step must be an object")
                self._validate_command(step.get("command"), law_id)
        if kind == "differential_json":
            self._validate_command(law.get("left_command"), law_id)
            self._validate_command(law.get("right_command"), law_id)
            paths = law.get("json_paths", [])
            if not isinstance(paths, list) or not all(isinstance(path, str) and path for path in paths):
                raise ValueError(f"law {law_id} json_paths must be a string array")
        for key in ("stdout_regex", "stderr_regex"):
            if key in law:
                _safe_regex(law[key])

    def load(self, files: list[Path]):
        if self.acceptance_required and not files:
            raise ValueError("acceptance law collection must not be empty")
        laws = []
        canonical_files = []
        for raw_path in files:
            path = self._law_path(raw_path)
            canonical_files.append(path)
            try:
                with path.open("rb") as stream:
                    data = tomllib.load(stream)
            except Exception as exc:
                raise ValueError(f"malformed law definition {path}: {exc}") from exc
            if data.get("schema", 1) != 1:
                raise ValueError(f"unsupported law schema in {path}")
            block = data.get("law", [])
            if not isinstance(block, list):
                raise ValueError(f"law collection in {path} must be an array")
            for item in block:
                if not isinstance(item, dict):
                    raise ValueError(f"law entry in {path} must be a table")
                law = dict(item)
                law["_file"] = str(path)
                self._validate_law(law)
                framework_law_root = self.root / "infra" / "laws" if self.source_mode else self.root / ".agents" / "infra" / "laws"
                try:
                    is_framework_law = path.is_relative_to(framework_law_root)
                except AttributeError:
                    is_framework_law = False
                if is_framework_law and law.get("severity", "hard") != "hard":
                    raise ValueError(f"framework acceptance law {law['id']} cannot be downgraded from hard")
                laws.append(law)
        if self.acceptance_required and not laws:
            raise ValueError("acceptance law collection must contain at least one executable law")
        ids = [law["id"] for law in laws]
        if len(ids) != len(set(ids)):
            raise ValueError("law ids must be unique across all loaded files")
        return laws

    def _protected_paths(self, law_files: list[Path]) -> list[Path]:
        protected = list(law_files)
        patterns = (
            ("tests-to-impl/*.md", "infra/tests/test_*.py", "infra/property_tests/test_*.py", "infra/law_tests/*.py")
            if self.source_mode
            else (".agents/tests-to-impl/*.md", ".agents/infra/tests/test_*.py", ".agents/infra/law_tests/*.py")
        )
        for pattern in patterns:
            protected.extend(path for path in self.root.glob(pattern) if path.is_file())
        unique = {str(path.resolve()): path.resolve() for path in protected}
        return [unique[key] for key in sorted(unique)]

    def run(self, files: list[Path]):
        if not files and self.acceptance_required:
            return [LawResult("framework.laws.nonempty", False, "acceptance law collection is empty", outcome="ERROR", oracle_count=1)]
        canonical_files = [self._law_path(path) for path in files]
        before = {str(path): digest(path) for path in canonical_files}
        try:
            laws = self.load(canonical_files)
        except Exception as exc:
            return [LawResult("framework.laws.definition_integrity", False, f"definition error: {exc}", outcome="ERROR", oracle_count=1)]
        protected = self._protected_paths(canonical_files)
        modes = {path: stat.S_IMODE(path.stat().st_mode) for path in canonical_files}
        for path, mode in modes.items():
            try:
                path.chmod(mode & ~0o222)
            except OSError:
                pass
        out = []
        try:
            with _DefinitionWatcher(protected) as watcher:
                for law in laws:
                    try:
                        out.append(self._one(law))
                    except Exception as exc:
                        out.append(LawResult(law.get("id", "<missing>"), False, f"runner error: {type(exc).__name__}: {exc}", law.get("severity", "hard"), outcome="ERROR", oracle_count=1))
            after = {str(path): digest(path) for path in canonical_files}
            files_by_id = {law["id"]: law["_file"] for law in laws}
            for result in out:
                source = files_by_id.get(result.id)
                if source is not None:
                    result.metadata["definition_file"] = source
                    result.metadata["definition_sha256_before"] = before.get(source)
                    result.metadata["definition_sha256_after"] = after.get(source)
            if before != after or watcher.changed.is_set():
                out.append(LawResult("framework.laws.immutable_during_run", False, "law or protected acceptance definition changed during execution", outcome="FAIL", oracle_count=1))
        finally:
            for path, mode in modes.items():
                try:
                    path.chmod(mode)
                except OSError:
                    pass
        return out

    def _assert_json(self, data, assertion):
        actual = dotted(data, assertion["json_path"])
        expected = assertion["value"]
        operation = assertion.get("operator", "eq")
        if operation not in OPS:
            raise ValueError(f"unknown operator {operation}")
        return OPS[operation](actual, expected), f"{assertion['json_path']}={actual!r} {operation} {expected!r}"

    def _root_path(self, value):
        return confined_path(
            self.root,
            self._portable_framework_value(value),
            must_exist=False,
            reject_symlinks=True,
        )

    @contextmanager
    def _isolated_root(self):
        with tempfile.TemporaryDirectory(prefix="aegis-law-sandbox-") as directory:
            destination = Path(directory) / "workspace"
            ignore = shutil.ignore_patterns(".git", ".aegis", "runtime", "persistent", "__pycache__", "*.pyc", "*.pyo", ".pytest_cache")
            shutil.copytree(self.root, destination, ignore=ignore)
            yield destination

    @staticmethod
    def _definition_state(path: Path):
        if not path.is_file():
            return None
        info = path.stat(follow_symlinks=False)
        return (digest(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode))

    def _command_result(self, law, command, *, root=None, timeout=None):
        if root is None:
            with self._isolated_root() as isolated:
                return self._command_result(law, command, root=isolated, timeout=timeout)
        workspace_before = workspace_fingerprint(root) if law.get("read_only", True) else None
        source_definition = Path(law["_file"])
        try:
            relative_definition = source_definition.relative_to(self.root)
            isolated_definition = root / relative_definition
        except ValueError:
            isolated_definition = None
        definition_before = self._definition_state(isolated_definition) if isolated_definition else None
        result = _run(self._expand_command(command), root, timeout or law.get("timeout_seconds", 60), capture_limit=int(law.get("capture_limit", MAX_CAPTURE)))
        definition_after = self._definition_state(isolated_definition) if isolated_definition else None
        if definition_before != definition_after:
            raise RuntimeError("law definition mutation was detected in the isolated execution workspace")
        if law.get("read_only", True):
            workspace_after = workspace_fingerprint(root)
            if (
                not workspace_before.get("available")
                or not workspace_after.get("available")
                or workspace_before.get("sha256") != workspace_after.get("sha256")
            ):
                raise RuntimeError("read-only law command changed workspace or fingerprint became unavailable")
        return result

    def _process_detail(self, result: ProcessResult, expected: int) -> str:
        return (
            f"exit={result.returncode} expected={expected}; timeout={result.timed_out}; "
            f"stdout={result.stdout_bytes}B/{result.stdout_sha256}; stderr={result.stderr_bytes}B/{result.stderr_sha256}"
        )

    def _one(self, law):
        law_id = law["id"]
        kind = law["kind"]
        severity = law.get("severity", "hard")
        if kind == "file_exists":
            path = self._root_path(law["path"])
            ok = path.exists()
            return LawResult(law_id, ok, str(path), severity, outcome="PASS" if ok else "FAIL")
        if kind in {"file_contains", "file_not_contains", "regex"}:
            path = self._root_path(law["path"])
            text = path.read_text(encoding="utf-8")
            if kind == "file_contains":
                ok = law["text"] in text
            elif kind == "file_not_contains":
                ok = law["text"] not in text
            else:
                ok = bool(_safe_regex(law["pattern"]).search(text))
            return LawResult(law_id, ok, f"checked {path}", severity, outcome="PASS" if ok else "FAIL")
        if kind in {"command", "json_command"}:
            result = self._command_result(law, law["command"])
            expected = int(law.get("expected_exit", 0))
            ok = not result.timed_out and result.returncode == expected
            detail = self._process_detail(result, expected)
            if ok and law.get("stdout_regex"):
                ok = bool(_safe_regex(law["stdout_regex"]).search(result.stdout))
                detail += f"; stdout_regex={ok}"
            if ok and law.get("stderr_regex"):
                ok = bool(_safe_regex(law["stderr_regex"]).search(result.stderr))
                detail += f"; stderr_regex={ok}"
            counterexample = None
            if kind == "json_command" and ok:
                if result.stdout_truncated:
                    raise ValueError("JSON law stdout was truncated")
                data = json.loads(result.stdout)
                ok, message = self._assert_json(data, law)
                detail += "; " + message
                if not ok:
                    counterexample = {
                        "json_path": law["json_path"],
                        "actual": dotted(data, law["json_path"]),
                        "expected": law["value"],
                        "operator": law.get("operator", "eq"),
                    }
            metadata={"seed": law.get("seed"), "stdout_sha256": result.stdout_sha256, "stderr_sha256": result.stderr_sha256}
            if counterexample is not None:
                metadata["counterexample"] = counterexample
            return LawResult(law_id, ok, detail, severity, outcome="PASS" if ok else ("ERROR" if result.timed_out else "FAIL"), metadata=metadata)
        if kind == "command_sequence":
            details = []
            with self._isolated_root() as isolated:
                for index, step in enumerate(law["steps"], 1):
                    result = self._command_result(law, step["command"], root=isolated, timeout=step.get("timeout_seconds", law.get("timeout_seconds", 60)))
                    expected = int(step.get("expected_exit", 0))
                    ok = not result.timed_out and result.returncode == expected
                    details.append(f"step{index}:{self._process_detail(result, expected)}")
                    if ok and step.get("stdout_regex"):
                        ok = bool(_safe_regex(step["stdout_regex"]).search(result.stdout))
                    if not ok:
                        return LawResult(law_id, False, "; ".join(details), severity, outcome="ERROR" if result.timed_out else "FAIL", oracle_count=index)
            return LawResult(law_id, True, "; ".join(details), severity, oracle_count=len(law["steps"]))
        if kind == "differential_json":
            with tempfile.TemporaryDirectory(prefix="aegis-law-diff-") as td:
                base = Path(td)
                ignore = shutil.ignore_patterns(".git", ".aegis", "runtime", "persistent", "__pycache__", "*.pyc")
                left_root = base / "left"
                right_root = base / "right"
                shutil.copytree(self.root, left_root, ignore=ignore)
                shutil.copytree(self.root, right_root, ignore=ignore)
                left = _run(self._expand_command(law["left_command"]), left_root, law.get("timeout_seconds", 60))
                right = _run(self._expand_command(law["right_command"]), right_root, law.get("timeout_seconds", 60))
            if left.timed_out or right.timed_out or left.returncode or right.returncode:
                return LawResult(law_id, False, f"oracle exits left={left.returncode}/{left.timed_out} right={right.returncode}/{right.timed_out}", severity, outcome="ERROR", oracle_count=2)
            if left.stdout_truncated or right.stdout_truncated:
                raise ValueError("differential JSON output was truncated")
            first = json.loads(left.stdout)
            second = json.loads(right.stdout)
            paths = law.get("json_paths", [])
            ok = first == second if not paths else all(dotted(first, path) == dotted(second, path) for path in paths)
            return LawResult(law_id, ok, "full JSON equal" if not paths else f"compared {len(paths)} JSON path(s)", severity, outcome="PASS" if ok else "FAIL", oracle_count=max(1, len(paths)))
        raise ValueError(f"unsupported law kind {kind}")
