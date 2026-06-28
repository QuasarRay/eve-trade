# Local vs GitHub Actions parity

## Capture both sides

Local:

```powershell
python observability/ci/observed_run.py e2e --maxfail 1
```

CI automatically uploads `.o11y/runs/`. Download and extract the artifact without changing the run directory layout.

## Compare

```powershell
python observability/ci/compare_runs.py `
  --local .o11y/runs/<local-run> `
  --ci C:\Downloads\eve-trade-observability\<ci-run> `
  --output .o11y/parity
```

Open `parity-diff.html`. The comparison highlights:

- SHA, branch, and dirty working tree.
- Python, Go, Rust, Docker, Compose, and OS versions.
- Presence (never secret values) of environment variables.
- Compose config hash and per-service image IDs/digests.
- PostgreSQL schema and migration hashes.
- Generated protobuf hash.
- Pytest collection and first failure.
- Service URLs and readiness command durations.
- Ordered pipeline command sequence.

## Interpretation order

1. Different SHA or local dirty state means the executions are not code-equivalent.
2. Different schema/migration/protobuf hashes indicate generated or persistence drift.
3. Different image IDs mean source-equivalent Compose files may still execute different binaries.
4. Different tool versions explain collection/build behavior before investigating business logic.
5. Different test collection often indicates dependency, marker, or path differences.

The diff is evidence, not an automated root-cause claim. Each highlighted hint states the observed difference and a bounded likely impact.
