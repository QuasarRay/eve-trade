from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/".agents"/"infra"))
from agentinfra.manifest import regenerate

parser=argparse.ArgumentParser(description="Authorized atomic Aegis manifest regeneration")
parser.add_argument("--maintenance-authorized",action="store_true",help="acknowledge reviewed maintenance; does not update RELEASE.json")
args=parser.parse_args()
try:
    result=regenerate(root,maintenance_authorized=args.maintenance_authorized)
except Exception as exc:
    print(json.dumps({"ok":False,"error":str(exc)},sort_keys=True),file=sys.stderr)
    raise SystemExit(2)
print(json.dumps({"ok":True,**result},sort_keys=True))
