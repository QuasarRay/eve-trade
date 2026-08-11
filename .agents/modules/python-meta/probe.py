from __future__ import annotations

import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".agents" / "infra"))


PACKAGES = ("mcpyrate", "unpythonic")
RANGES = {"mcpyrate": ((4, 0, 0), (5, 0, 0)), "unpythonic": ((2, 0, 0), (3, 0, 0))}


def installed(name: str) -> tuple[bool, str | None]:
    try:
        return True, importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False, None


def compatible(name: str, version: str | None) -> bool:
    if version is None:
        return False
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return False
    parsed = tuple(map(int, match.groups()))
    low, high = RANGES[name]
    return low <= parsed < high


def main() -> int:
    capabilities = {}
    for name in PACKAGES:
        available, version = installed(name)
        capabilities[name] = {"available": available, "version": version, "compatible": compatible(name, version)}
    if not all(value["available"] and value["compatible"] for value in capabilities.values()):
        # Absence is an explicit optional capability outcome.  Prove that the
        # recovery-critical core still imports in a scrubbed interpreter.
        code = "import agentinfra.atomic,agentinfra.state_store,agentinfra.evidence;print('core-ok')"
        result = subprocess.run([sys.executable, "-I", "-c", code], text=True, capture_output=True, check=False)
        # -I intentionally removes the project path, so use a separate direct import
        # probe in this interpreter as the portable core oracle.
        try:
            import agentinfra.atomic  # noqa: F401
            import agentinfra.evidence  # noqa: F401
            import agentinfra.state_store  # noqa: F401
            core_ok = True
        except Exception:
            core_ok = False
        payload = {
            "ok": False,
            "outcome": "UNAVAILABLE" if core_ok else "FAIL",
            "capability_status": "MISSING",
            "extension_enabled": False,
            "core_without_extensions": core_ok,
            "packages": capabilities,
        }
        print(json.dumps(payload, sort_keys=True))
        return 2 if core_ok else 1
    probes = {
        "mcpyrate": "import mcpyrate; print(mcpyrate.__name__)",
        "unpythonic": "import unpythonic; print(unpythonic.__name__)",
    }
    results = {}
    for name, code in probes.items():
        result = subprocess.run([sys.executable, "-I", "-c", code], text=True, capture_output=True, check=False, timeout=20)
        results[name] = {"exit": result.returncode, "functional": result.returncode == 0 and name in result.stdout}
    ok = all(result["functional"] for result in results.values())
    print(json.dumps({"ok": ok, "outcome": "PASS" if ok else "FAIL", "capability_status": "AVAILABLE", "extension_enabled": True, "packages": capabilities, "functional_probes": results}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
