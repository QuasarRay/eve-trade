# Source-side executable infrastructure

`agentinfra` is the Python 3.11+ stdlib-only recovery/control plane. Hypothesis is an optional development/test dependency declared under `project.optional-dependencies.test`; runtime recovery, state, evidence, audit, packaging, and laws do not depend on it.

The shared layout resolver prefers a complete authoritative source checkout and otherwise reads deployed `.agents`. Mutable paths always use `.aegis`. Source commands:

```text
python -B bin/agentctl.py --root . doctor
python -B bin/agentctl.py --root . discover
python -B bin/agentctl.py --root . policy validate <project-contract.toml>
python -B bin/agentctl.py --root . precheck build <project-contract.toml> ...
python -B bin/agentctl.py --root . law run
python -B bin/agentctl.py --root . audit
python -B bin/agentctl.py --root . package build dist/aegis-governance
```

Core modules implement governance guards, atomic transactions/recovery, locks, repository discovery, constitutional/project policy compilation, scope/budgets, TDD assurance, task state, evidence, independent review, exact final audit, law isolation, source packaging, runtime migration, modules, and host adapters.

Operational commands do not expose governance mutators. Package output is outside `.agents`; deployment is manual. `scripts/verify_release.py` performs source static/provenance checks, full source suites, deterministic double build, disposable human-maintainer simulation, clean-deployment suites/audit, exact traceability, the 40-check release receipt, and initial/final active-governance snapshots.
