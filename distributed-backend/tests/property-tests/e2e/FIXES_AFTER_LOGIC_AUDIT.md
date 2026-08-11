# Superseded Logic-Audit Report

This historical report previously claimed that generic protocol-v2 evidence schemas closed missing contract and fault logic. A later exhaustive audit disproved that conclusion: a case hash and test-name-derived predicates did not establish the physical prerequisite or named business invariant.

The current repair deliberately preserves the useful source finding inventories (`audit_resolution_manifest.json` and `semantic_override_manifest.json`) while revoking their old resolution claims. `tools/generate_hypothesis_audit.py` groups every finding, records its full affected-name set, and distinguishes:

- fixed infrastructure defects;
- partial Litmus capability;
- contracts mitigated by strict fail-closed routing but still lacking a semantic oracle.

See `HYPOTHESIS_AUDIT.json` for the exhaustive result and `../infra/test-requirements.json` for every remaining exact blocker.
