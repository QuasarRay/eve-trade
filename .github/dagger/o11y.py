"""Always-run observability aggregate driven by actual GitHub needs results."""
from __future__ import annotations
import json, os
from _common import REPO_ROOT, StageFailure, sh, stage, with_env, with_source
from _images import PYTHON_SLIM_IMAGE

async def run(dag):
    raw=os.environ.get("OBS_CI_NEEDS_JSON")
    if not raw: raise RuntimeError("OBS_CI_NEEDS_JSON is required")
    needs=json.loads(raw)
    ctr=with_source(dag.container().from_(PYTHON_SLIM_IMAGE),dag)
    ctr=with_env(ctr,{"OBS_CI_NEEDS_JSON":json.dumps(needs,separators=(",",":")),"OBSERVABILITY_ENV":"github-actions"})
    ctr=sh(ctr,[
        "mkdir -p .o11y",
        "set +e",
        "python .github/dagger/_validate_evidence.py .o11y/producer-artifacts; envelope=$?",
        "python distributed-backend/observability/ci/ci_aggregate.py; agg=$?",
        "python scripts/verify_repository.py validate-ci --current-gate o11y --output .o11y/verification-profile.json; verify=$?",
        "set -e",
        "printf '%s\\n' \"$envelope\" > .o11y/envelope-exit-code",
        "printf '%s\\n' \"$agg\" > .o11y/aggregate-exit-code",
        "printf '%s\\n' \"$verify\" > .o11y/verify-exit-code",
        "exit 0",
    ])
    await ctr.sync()
    await ctr.directory("/src/.o11y").export(str(REPO_ROOT/".o11y"))
    envelope=int((await ctr.file("/src/.o11y/envelope-exit-code").contents()).strip())
    agg=int((await ctr.file("/src/.o11y/aggregate-exit-code").contents()).strip())
    verify=int((await ctr.file("/src/.o11y/verify-exit-code").contents()).strip())
    upstream={k:v.get("result") for k,v in needs.items()}
    profile_path=REPO_ROOT/".o11y"/"verification-profile.json"
    profile=None
    if profile_path.exists():
        try: profile=json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception: profile={"unparsed": profile_path.read_text(encoding="utf-8")[-4000:]}
    if envelope or agg or verify:
        raise StageFailure(
            f"observability aggregate failed (envelope={envelope}, aggregate={agg}, verify={verify}); diagnostics were exported",
            payload={"envelope_exit":envelope,"aggregate_exit":agg,"verify_exit":verify,"upstream_results":upstream,"verification_profile":profile},
        )
    sha=os.environ.get("GITHUB_SHA","")
    if not sha: raise RuntimeError("GITHUB_SHA is required")
    return {"verified_sha":sha,"upstream_results":upstream,"verification_profile":profile}

if __name__ == "__main__": stage("o11y",run)
