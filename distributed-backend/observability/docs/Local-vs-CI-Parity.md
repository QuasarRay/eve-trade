# Local vs GitHub Actions parity

## Capture Both Sides

Local:

```powershell
python distributed-backend/observability/ci/observed_run.py e2e --maxfail 1
```

CI runs write `.o11y/runs/<run-id>` with GitHub SHA, ref, run ID, attempt, workflow, and job provenance. If the workflow exports the directory as an artifact, download and extract it without changing the run directory layout.

## Compare

```powershell
python distributed-backend/observability/ci/compare_runs.py `
  --local .o11y/runs/<local-run> `
  --ci C:\Downloads\eve-trade-observability\<ci-run> `
  --output .o11y/parity
```

Open `parity-diff.html`. The comparison highlights SHA, dirty state, tool versions, Encore config hash, Kubernetes manifest hash, PostgreSQL schema/migration hashes, generated protobuf hash, pytest collection, service URLs, readiness durations, and command sequence.

## Interpretation Order

1. Different SHA or dirty-worktree fingerprint means the executions are not code-equivalent. Use the report freshness state first: `EXACT`, `ANCESTOR`, `DIVERGED`, `DIRTY_WORKTREE_MISMATCH`, or `UNKNOWN`.
2. Different schema, migration, protobuf, Encore, or Kubernetes hashes indicate generated, persistence, runtime, or deployment drift.
3. Different tool versions explain collection/build behavior before investigating business logic.
4. Different test collection often indicates dependency, marker, or path differences.

The diff is evidence, not an automated root-cause claim. Each highlighted hint states the observed difference and a bounded likely impact.
