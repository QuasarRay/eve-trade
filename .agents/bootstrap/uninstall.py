from pathlib import Path
import argparse, json, sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agents" / "infra"))
from agentinfra.bootstrap import uninstall
p=argparse.ArgumentParser(description="Safely remove only the managed Aegis block from repository-root AGENTS.md")
p.add_argument("--apply", action="store_true")
p.add_argument("--force-managed-block", action="store_true")
a=p.parse_args()
print(json.dumps(uninstall(ROOT, apply=a.apply, force_managed_block=a.force_managed_block), indent=2, sort_keys=True))
