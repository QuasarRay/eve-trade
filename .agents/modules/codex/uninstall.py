from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/".agents"/"infra"))
from agentinfra.codex_config import uninstall, ConfigError
apply="--apply" in sys.argv
try:
    print(uninstall(ROOT,dry_run=not apply))
except ConfigError as e:
    print(f"error: {e}",file=sys.stderr);raise SystemExit(2)
