from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import re
import shutil

from .process import run_process


PURPOSES = {"interactive", "python-mixed", "oneshot", "direct", "windows", "posix"}
XONSH_MINIMUM = (0, 19, 0)
XONSH_MAXIMUM = (1, 0, 0)


def _xonsh_compatible(executable: str) -> bool:
    try:
        result = run_process([executable, "--no-rc", "--version"], cwd=Path(executable).resolve().parent, timeout=5, capture_limit=4096)
    except (OSError, RuntimeError):
        return False
    match = re.search(r"(?:xonsh/)?(\d+)\.(\d+)\.(\d+)", result.stdout + "\n" + result.stderr)
    return bool(
        not result.timed_out
        and result.returncode == 0
        and match
        and XONSH_MINIMUM <= tuple(map(int, match.groups())) < XONSH_MAXIMUM
    )


@lru_cache(maxsize=1)
def available() -> dict[str, str]:
    found: dict[str, str] = {}
    for name in ("xonsh", "pwsh", "powershell", "bash", "sh"):
        executable = shutil.which(name)
        if not executable:
            continue
        # System32 bash.exe is a WSL launcher, not proof that a usable POSIX
        # distro exists.  On unconfigured hosts it can block indefinitely.
        if os.name == "nt" and name in {"bash", "sh"} and Path(executable).parent.name.casefold() == "system32":
            continue
        if name == "xonsh" and not _xonsh_compatible(executable):
            continue
        found[name] = executable
    return found


def choose(purpose: str = "interactive", *, required_shell: str | None = None) -> dict[str, str]:
    if not isinstance(purpose, str) or purpose.lower() not in PURPOSES:
        raise ValueError(f"unknown environment-selection purpose: {purpose!r}")
    requested = purpose.lower()
    if requested in {"oneshot", "direct"}:
        return {"shell": "direct", "reason": "one deterministic command avoids shell translation"}
    shells = available()
    if required_shell is not None:
        if not isinstance(required_shell, str):
            raise ValueError(f"unknown required native shell: {required_shell!r}")
        required_shell = required_shell.casefold()
        if required_shell not in {"xonsh", "pwsh", "powershell", "bash", "sh"}:
            raise ValueError(f"unknown required native shell: {required_shell!r}")
        if required_shell not in shells:
            raise RuntimeError(f"required native shell is unavailable: {required_shell}")
        return {"shell": required_shell, "path": shells[required_shell], "reason": "project-declared native shell requirement takes precedence"}
    if requested in {"python-mixed", "interactive"} and "xonsh" in shells:
        return {"shell": "xonsh", "path": shells["xonsh"], "reason": "Python-native control flow plus subprocesses matches the declared mixed workload"}
    if requested == "windows" or (os.name == "nt" and requested == "interactive"):
        for name in ("pwsh", "powershell"):
            if name in shells:
                return {"shell": name, "path": shells[name], "reason": "Windows-native semantics match the declared workload"}
        return {"shell": "direct", "reason": "no validated Windows shell capability is available"}
    if requested == "posix" or requested == "interactive":
        for name in ("bash", "sh"):
            if name in shells:
                return {"shell": name, "path": shells[name], "reason": "POSIX-native semantics match the declared workload"}
        return {"shell": "direct", "reason": "no validated POSIX shell capability is available"}
    if "xonsh" in shells:
        return {"shell": "xonsh", "path": shells["xonsh"], "reason": "validated Xonsh is the available mixed environment"}
    return {"shell": "direct", "reason": "no suitable validated interactive shell capability is available"}
