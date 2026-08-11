# External fault / evidence driver protocol v2

Environment-dependent contracts cannot all be injected from the standalone Python
package.  Examples include killing a process between two persistence boundaries,
partitioning NSQ, forcing PostgreSQL primary failover, and proving live cloud IAM.

The fixed suite **does not trust an external boolean**.  The external command must
collect raw observations; the Python Hypothesis harness checks the named invariant.

Configure one or both of:

- `EVE_TRADE_FAULT_DRIVER`
- `EVE_TRADE_EVIDENCE_DRIVER`

The driver receives JSON on stdin:

```json
{
  "protocol_version": 2,
  "contract": "test_name",
  "category": 24,
  "case": {"hypothesis": "generated values"},
  "case_sha256": "...",
  "repo_root": "/path/to/eve-trade",
  "role": "fault",
  "evidence_spec": {
    "required_sections": ["scenario", "fault", "outcome"],
    "predicates": []
  }
}
```

The response must repeat `protocol_version`, `contract`, and `case_sha256`, and
contain all raw observation sections required by `evidence_spec`.  Returning only
`{"ok": true}` fails.

The harness checks:

1. the evidence belongs to the exact shrunk Hypothesis example via `case_sha256`;
2. every required evidence section exists;
3. every name-derived predicate is evaluated in Python;
4. contracts rerouted after the semantic audit receive additional anti-confounding
   checks from `semantic_validation.py`.

## Mapped command wrapper

`mapped_contract_driver.py` is a protocol-v2 adapter.  `EVE_TRADE_CONTRACT_DRIVER_MAP`
points to a JSON object mapping exact test names to argv arrays.  The mapped command
receives the full protocol-v2 request on stdin and prints raw observation JSON.
The wrapper adds the identity envelope; it rejects an observation consisting only
of an `ok` flag.

Example:

```json
{
  "test_worker_crash_after_processing_transition_is_recovered": [
    "python",
    "tests/faults/worker_crash_after_processing.py"
  ]
}
```

A mapped probe should return observations such as:

```json
{
  "scenario": {
    "action_executed": true,
    "preconditions_satisfied": true,
    "generated_case_applied": true
  },
  "fault": {
    "injected": true,
    "kind": "process_crash",
    "active_during_target_window": true
  },
  "recovery": {"completed": true},
  "outcome": {"accepted": true},
  "effects": {"business_effect_count": 1}
}
```

The exact fields are contract-specific.  Generate the complete schemas with:

```bash
python tools/export_evidence_specs.py
```
