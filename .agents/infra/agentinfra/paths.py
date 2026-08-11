from __future__ import annotations
from pathlib import Path

def _is_source_framework(root: Path) -> bool:
    return all(
        path.exists()
        for path in (
            root / "VERSION",
            root / "framework.toml",
            root / "infra" / "agentinfra",
            root / "laws",
            root / "modules",
        )
    )

def framework_dir(root: Path) -> Path:
    """Return the authoritative read-only framework surface for this project."""
    project = Path(root).resolve(strict=True)
    if _is_source_framework(project):
        return project
    deployed = project / ".agents"
    if deployed.is_dir():
        return deployed
    raise FileNotFoundError("project has neither an Aegis source tree nor deployed .agents governance")

def find_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for candidate in (p, *p.parents):
        if _is_source_framework(candidate) or (candidate / ".agents" / "framework.toml").is_file():
            return candidate
    raise FileNotFoundError("could not find an Aegis source checkout or deployed .agents/framework.toml")

def agents_dir(root: Path) -> Path: return root / ".agents"
def aegis_dir(root: Path) -> Path: return root / ".aegis"
def runtime_dir(root: Path) -> Path: return aegis_dir(root) / "runtime"
def tasks_dir(root: Path) -> Path: return aegis_dir(root) / "tasks"
def persistent_dir(root: Path) -> Path: return aegis_dir(root) / "state"
def leases_dir(root: Path) -> Path: return aegis_dir(root) / "leases"
def evidence_dir(root: Path) -> Path: return aegis_dir(root) / "evidence"
def compiled_policy_dir(root: Path) -> Path: return aegis_dir(root) / "compiled-policy"
def cache_dir(root: Path) -> Path: return aegis_dir(root) / "cache"
def audit_dir(root: Path) -> Path: return aegis_dir(root) / "audit"
def manifests_dir(root: Path) -> Path: return aegis_dir(root) / "manifests"
def install_state_dir(root: Path) -> Path: return persistent_dir(root) / "install-state"
