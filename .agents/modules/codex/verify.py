from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/".agents"/"infra"))

from agentinfra.codex_config import verify_managed_source, verify_static


source_ok,detail=verify_managed_source(ROOT)
installed_path=ROOT/".codex"/"config.toml"
if installed_path.exists():
    installed_ok,installed_detail=verify_static(ROOT)
    installed={"status":"INSTALLED","ok":installed_ok,**installed_detail}
else:
    installed_ok=True
    installed={"status":"NOT_INSTALLED","ok":None,"detail":"project-local Codex configuration is not installed; source/static schema was still verified"}
payload={"ok":source_ok and installed_ok,"static_source":{"ok":source_ok,**detail},"project_installation":installed,"live_effective":detail["live_effective"]}
print(json.dumps(payload,indent=2,sort_keys=True))
raise SystemExit(0 if payload["ok"] else 1)
