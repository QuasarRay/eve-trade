# Implementation Status

The former summary treated collected test names and protocol-v2 name-derived predicates as completed implementations. That claim was incorrect.

Current machine-readable truth lives in:

- `../infra/test-requirements.json` — every exact contract, execution mechanism, implementation status, and blocker;
- `../infra/litmus-contracts.json` — every exact chaos prerequisite and evidence requirement;
- `HYPOTHESIS_AUDIT.json` — every audit finding and full affected-name set;
- `HYPOTHESIS_AUDIT.md` — compact generated audit summary.

Run the generators with `--check` and `../infra/verify_collection.py` to obtain current verified counts. A contract with `SEMANTIC_ORACLE_REQUIRED`, `EXTERNAL_CAPABILITY_REQUIRED`, or `INFRASTRUCTURE_READY` is not counted as implemented and fails closed in strict execution.
