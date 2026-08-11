from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ctypes
import ctypes.wintypes
import os
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Iterable, Mapping

from .security import minimal_subprocess_env, redact_text


if os.name == "nt":
    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]


    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.wintypes.DWORD),
            ("SchedulingClass", ctypes.wintypes.DWORD),
        ]


    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _assign_kill_job(proc: subprocess.Popen):
    if os.name != "nt":
        return None
    kernel = ctypes.windll.kernel32
    kernel.CreateJobObjectW.restype = ctypes.wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.wintypes.LPCWSTR)
    kernel.SetInformationJobObject.argtypes = (ctypes.wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.wintypes.DWORD)
    kernel.AssignProcessToJobObject.argtypes = (ctypes.wintypes.HANDLE, ctypes.wintypes.HANDLE)
    kernel.CloseHandle.argtypes = (ctypes.wintypes.HANDLE,)
    kernel.TerminateJobObject.argtypes = (ctypes.wintypes.HANDLE, ctypes.wintypes.UINT)
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel.CloseHandle(job)
        return None
    if not kernel.AssignProcessToJobObject(job, ctypes.wintypes.HANDLE(int(proc._handle))):
        kernel.CloseHandle(job)
        return None
    return job


def _close_job(job) -> None:
    if job is not None and os.name == "nt":
        ctypes.windll.kernel32.CloseHandle(job)


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    stdout_sha256: str
    stderr_sha256: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    started_at: str
    finished_at: str
    duration_seconds: float


class _Capture:
    def __init__(self, limit: int):
        self.limit = limit
        self.buffer = bytearray()
        self.digest = hashlib.sha256()
        self.size = 0

    def consume(self, stream) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            self.digest.update(chunk)
            self.size += len(chunk)
            if len(self.buffer) < self.limit:
                self.buffer.extend(chunk[: self.limit - len(self.buffer)])

    def text(self) -> str:
        return redact_text(bytes(self.buffer).decode("utf-8", "replace"))


def _kill_tree(proc: subprocess.Popen, job=None) -> None:
    if proc.poll() is not None:
        if job is not None and os.name == "nt":
            ctypes.windll.kernel32.TerminateJobObject(job, 1)
        return
    if os.name == "nt":
        if job is not None and ctypes.windll.kernel32.TerminateJobObject(job, 1):
            return
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=minimal_subprocess_env(),
        )
        if proc.poll() is None:
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_process(
    argv: Iterable[str],
    *,
    cwd: Path,
    timeout: float = 60.0,
    env: Mapping[str, str] | None = None,
    capture_limit: int = 1_000_000,
) -> ProcessResult:
    command = tuple(argv)
    if not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("argv must be a non-empty sequence of non-empty strings")
    if timeout <= 0 or timeout != timeout or timeout == float("inf"):
        raise ValueError("timeout must be finite and positive")
    if capture_limit < 1024:
        raise ValueError("capture_limit must be at least 1024 bytes")
    started_wall = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="aegis-process-home-") as isolated_directory:
        isolated_home = Path(isolated_directory)
        child_env = minimal_subprocess_env(env)
        defaults = {
            "HOME": str(isolated_home),
            "USERPROFILE": str(isolated_home),
            "HOMEDRIVE": isolated_home.drive or "",
            "HOMEPATH": str(isolated_home)[len(isolated_home.drive) :] if isolated_home.drive else str(isolated_home),
            "APPDATA": str(isolated_home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(isolated_home / "AppData" / "Local"),
            "XDG_CACHE_HOME": str(isolated_home / ".cache"),
            "XONSH_DATA_DIR": str(isolated_home / ".local" / "share" / "xonsh"),
            "XONSH_CACHE_DIR": str(isolated_home / ".cache" / "xonsh"),
        }
        for key, value in defaults.items():
            child_env.setdefault(key, value)
        # Hypothesis defaults to ``Path.cwd() / ".hypothesis"``.  A property
        # suite executed against a sealed deployment must never make its
        # working governance tree dirty, and a caller-provided environment
        # must not be able to re-enable that write path.
        child_env["HYPOTHESIS_STORAGE_DIRECTORY"] = str(isolated_home / ".hypothesis")
        for key in (
            "APPDATA",
            "LOCALAPPDATA",
            "XDG_CACHE_HOME",
            "XONSH_DATA_DIR",
            "XONSH_CACHE_DIR",
            "HYPOTHESIS_STORAGE_DIRECTORY",
        ):
            Path(child_env[key]).mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=child_env,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
        job = _assign_kill_job(proc)
        stdout_capture = _Capture(capture_limit)
        stderr_capture = _Capture(capture_limit)
        stdout_thread = threading.Thread(target=stdout_capture.consume, args=(proc.stdout,), daemon=True)
        stderr_thread = threading.Thread(target=stderr_capture.consume, args=(proc.stderr,), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc, job)
            proc.wait(timeout=10)
        finally:
            # Closing a kill-on-close job also removes descendants that kept
            # running after a normally exiting direct child.
            _close_job(job)
            job = None
            stdout_thread.join(timeout=10)
            stderr_thread.join(timeout=10)
            if stdout_thread.is_alive() or stderr_thread.is_alive():
                _kill_tree(proc, job)
                raise RuntimeError("subprocess output reader did not terminate")
            proc.stdout.close()
            proc.stderr.close()
        finished = time.monotonic()
        result = ProcessResult(
            argv=command,
            cwd=str(cwd.resolve()),
            returncode=int(proc.returncode),
            stdout=stdout_capture.text(),
            stderr=stderr_capture.text(),
            stdout_sha256=stdout_capture.digest.hexdigest(),
            stderr_sha256=stderr_capture.digest.hexdigest(),
            stdout_bytes=stdout_capture.size,
            stderr_bytes=stderr_capture.size,
            stdout_truncated=stdout_capture.size > capture_limit,
            stderr_truncated=stderr_capture.size > capture_limit,
            timed_out=timed_out,
            started_at=started_wall,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=finished - started,
        )
    return result
