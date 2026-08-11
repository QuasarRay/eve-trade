from pathlib import Path
import argparse, json, sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agents" / "infra"))
from agentinfra.bootstrap import install
p=argparse.ArgumentParser(description="Safely merge the Aegis bootstrap into repository-root AGENTS.md")
p.add_argument("--apply", action="store_true")
p.add_argument("--replace-managed-block", action="store_true")
a=p.parse_args()
print(json.dumps(install(ROOT, apply=a.apply, replace_managed_block=a.replace_managed_block), indent=2, sort_keys=True))
