from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import ctypes
import ctypes.wintypes
import json
import os
import socket
import time
import uuid
from pathlib import Path

from .atomic import atomic_write_json


class LockError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process_identity(pid: int) -> str | None:
    """Return a creation identity stronger than PID when the host exposes one."""
    if pid <= 0:
        return None
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            creation = ctypes.wintypes.FILETIME()
            exit_time = ctypes.wintypes.FILETIME()
            kernel = ctypes.wintypes.FILETIME()
            user = ctypes.wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)
            ):
                return None
            value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return f"win-filetime:{value}"
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        # Field 22 is process start time.  The command name may contain spaces inside parentheses.
        tail = stat_path.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return f"proc-start:{tail[19]}"
    except (OSError, IndexError):
        return None


def _pid_alive(pid: int, identity: str | None = None) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ctypes.windll.kernel32.SetLastError(0)
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            alive = True
        else:
            alive = ctypes.windll.kernel32.GetLastError() == 5
    else:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
    if alive and identity is not None:
        current = _process_identity(pid)
        return current is None or current == identity
    return alive


@dataclass
class FileLock:
    """Short-lived process lock authenticated by nonce and process creation identity."""

    path: Path
    purpose: str
    _owner: dict | None = field(default=None, init=False, repr=False)

    def _try_create(self, payload: dict) -> None:
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def acquire(self, *, timeout: float = 10.0) -> dict:
        if self._owner is not None:
            raise LockError("lock object already owns a lease")
        if timeout < 0 or timeout != timeout or timeout == float("inf"):
            raise ValueError("lock timeout must be finite and non-negative")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 2,
            "nonce": uuid.uuid4().hex,
            "pid": os.getpid(),
            "process_identity": _process_identity(os.getpid()),
            "host": socket.gethostname(),
            "created": _now(),
            "purpose": self.purpose,
        }
        deadline = time.monotonic() + timeout
        last_contention: PermissionError | None = None
        while True:
            try:
                self._try_create(payload)
                self._owner = payload
                return dict(payload)
            except FileExistsError:
                pass
            except PermissionError as exc:
                # Windows may report a sharing violation rather than
                # FileExistsError while another thread/process is creating or
                # unlinking the lock.  During that window exists() can also be
                # false, so retry to the explicit deadline instead of leaking
                # a nondeterministic raw OSError from the lock abstraction.
                if os.name != "nt":
                    raise
                last_contention = exc
            info = self.inspect()
            if info.get("exists") and info.get("same_host") and info.get("pid_alive_here") is False:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise LockError(f"lock already exists or remains inaccessible: {self.path} ({info})") from last_contention
            time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))

    def inspect(self) -> dict:
        last_error = None
        for delay in (0.0, 0.002, 0.01, 0.03):
            if delay:
                time.sleep(delay)
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                break
            except FileNotFoundError:
                return {"exists": False}
            except PermissionError as exc:
                last_error = exc
            except Exception as exc:
                return {"exists": True, "corrupt": True, "error": str(exc)}
        else:
            return {"exists": True, "corrupt": True, "error": str(last_error)}
        if not isinstance(data, dict):
            return {"exists": True, "corrupt": True, "error": "lock record is not an object"}
        if not isinstance(data, dict) or not isinstance(data.get("nonce"), str):
            return {"exists": True, "corrupt": True, "error": "missing owner nonce"}
        data["exists"] = True
        data["same_host"] = data.get("host") == socket.gethostname()
        data["pid_alive_here"] = (
            _pid_alive(int(data.get("pid", -1)), data.get("process_identity")) if data["same_host"] else None
        )
        try:
            data["age_seconds"] = max(0.0, time.time() - self.path.stat().st_mtime)
        except OSError:
            data["age_seconds"] = None
        return data

    def release(self, *, nonce: str | None = None) -> None:
        if self._owner is None:
            raise LockError("this lock object does not own the process lock")
        info = self.inspect()
        if not info.get("exists"):
            self._owner = None
            raise LockError("owned process lock disappeared")
        expected = nonce or self._owner["nonce"]
        if (
            info.get("nonce") != expected
            or info.get("nonce") != self._owner.get("nonce")
            or info.get("host") != self._owner.get("host")
            or int(info.get("pid", -1)) != os.getpid()
            or info.get("process_identity") != self._owner.get("process_identity")
        ):
            raise LockError("refusing to release process lock with mismatched owner binding")
        last_error = None
        for delay in (0.0, 0.002, 0.01, 0.03, 0.08):
            if delay:
                time.sleep(delay)
            try:
                self.path.unlink()
                break
            except PermissionError as exc:
                last_error = exc
        else:
            raise LockError(f"owned process lock could not be released after bounded sharing retries: {last_error}")
        self._owner = None


@dataclass
class LeaseLock:
    """Long-lived logical lease for sequential subagents across CLI invocations."""

    path: Path
    purpose: str

    def acquire(self, *, task_id: str, role: str, parent_id: str | None = None) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 2,
            "lease_id": "L-" + uuid.uuid4().hex,
            "owner_nonce": uuid.uuid4().hex,
            "task_id": task_id,
            "role": role,
            "parent_id": parent_id,
            "opened_by_pid": os.getpid(),
            "process_identity": _process_identity(os.getpid()),
            "host": socket.gethostname(),
            "created": _now(),
            "purpose": self.purpose,
        }
        raw = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise LockError(f"subagent lease already exists: {self.inspect()}") from exc
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return payload

    def inspect(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"exists": False}
        except Exception as exc:
            return {"exists": True, "corrupt": True, "error": str(exc)}
        required = ("lease_id", "owner_nonce", "task_id", "role", "host")
        if not isinstance(data, dict) or any(not isinstance(data.get(key), str) or not data[key] for key in required):
            return {"exists": True, "corrupt": True, "error": "invalid lease schema"}
        data["exists"] = True
        try:
            data["age_seconds"] = max(0.0, time.time() - self.path.stat().st_mtime)
        except OSError:
            data["age_seconds"] = None
        return data

    def release(
        self,
        lease_id: str,
        *,
        owner_nonce: str | None = None,
        task_id: str | None = None,
        role: str | None = None,
    ) -> None:
        info = self.inspect()
        if not info.get("exists"):
            raise LockError("subagent lease does not exist")
        if info.get("corrupt"):
            raise LockError("subagent lease is corrupt")
        checks = {
            "lease_id": lease_id,
            "owner_nonce": owner_nonce,
            "task_id": task_id,
            "role": role,
        }
        for key, expected in checks.items():
            if expected is not None and info.get(key) != expected:
                raise LockError(f"subagent lease {key} mismatch")
        self.path.unlink()

    def force_clear(self, *, reason: str, expected_task_id: str | None = None) -> dict:
        if not reason.strip():
            raise LockError("force-clear requires a non-empty reason")
        info = self.inspect()
        if not info.get("exists"):
            return info
        if info.get("corrupt"):
            raise LockError("corrupt lease requires forensic recovery; automatic force-clear refused")
        if expected_task_id is not None and info.get("task_id") != expected_task_id:
            raise LockError("refusing to clear another task's lease")
        self.path.unlink()
        return info

    def restore(self, payload: dict) -> None:
        """Restore an exactly captured lease after a surrounding transaction aborts.

        This is intentionally narrower than acquire: it accepts only a complete schema-2
        payload and never overwrites another owner's lease.
        """
        required = {"schema", "lease_id", "owner_nonce", "task_id", "role", "host", "purpose"}
        if not isinstance(payload, dict) or payload.get("schema") != 2 or not required <= set(payload):
            raise LockError("refusing to restore an invalid lease payload")
        if payload.get("purpose") != self.purpose:
            raise LockError("refusing to restore lease for a different purpose")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps({key: value for key, value in payload.items() if key not in {"exists", "age_seconds"}}, sort_keys=True) + "\n").encode("utf-8")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise LockError("cannot restore lease because another lease now exists") from exc
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
