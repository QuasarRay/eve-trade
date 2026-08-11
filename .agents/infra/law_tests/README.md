# Complete Aegis acceptance system

`traceability.json` deterministically maps every one of the 834 immutable `tests-to-impl` names to executable production-boundary observations. `constitutional_catalog.py` separately defines exactly 105 constitutional/TDD laws with invariant IDs, falsifiers, severity, TDD mode, source symbol, runtime test identity, definition digest, and reviewed property subsumption.

`run_suite.py` builds a disposable deployment when invoked from source, executes legacy units/built-ins/templates and every semantic family, verifies protected definitions before/after, runs all source unit/property modules in import-isolated interpreters, evaluates each constitutional method outcome, and writes a sealed ledger plus human constitutional Markdown under `.aegis/law-results/`.

Capability and correctness are separate. Portable laws must PASS. Known live-host/foreign-platform/optional-extension limits may be `UNAVAILABLE`, `BLOCKED`, or justified `NOT_APPLICABLE`; they retain concrete detail and never become PASS. A top-level ledger with any integrity error, incomplete record, invalid digest, FAIL/ERROR, or inconsistent constitutional summary is rejected by traceability.

`build_traceability.py` is bookkeeping, not proof. Supplying a completed ledger is what promotes entries to final statuses. Release verification additionally checks the exact changed-production-file-to-TDD-cycle map under `.aegis/audit/tdd-production-map.json`; the reported count of unmapped behavioral changes is computed, not asserted.
