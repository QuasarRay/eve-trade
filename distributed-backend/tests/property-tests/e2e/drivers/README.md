# External fault / evidence driver protocol v3

Environment-dependent contracts cannot all be injected from the standalone Python
package.  Examples include killing a process between two persistence boundaries,
partitioning NSQ, forcing PostgreSQL primary failover, and proving live cloud IAM.

The suite **does not trust an external boolean**.  The external command must
collect raw observations; the Python Hypothesis harness checks the named invariant.

Configure one or both of:

- `EVE_TRADE_FAULT_DRIVER`
- `EVE_TRADE_EVIDENCE_DRIVER`

The driver receives JSON on stdin:

```json
{
  "protocol_version": 3,
  "evidence_schema": "eve-trade.external-evidence/v3",
  "contract": "test_name",
  "category": 24,
  "case": {"hypothesis": "generated values"},
  "case_sha256": "...",
  "execution": {
    "run_id": "pt-a1b2c3d4",
    "invocation_id": "fresh-per-example-id",
    "nonce": "fresh-unpredictable-challenge",
    "issued_at": "2026-08-11T00:00:00Z",
    "max_age_seconds": 600
  },
  "repo_root": "/path/to/eve-trade",
  "role": "fault",
  "evidence_spec": {
    "required_sections": ["scenario", "fault", "outcome"],
    "predicates": []
  }
}
```

The response must repeat the contract/case identity and the exact execution
challenge, carry a fresh `evidence_id` and `collected_at`, and contain timestamped
raw observation records. Returning only `{"ok": true}` fails. Replaying evidence
from an earlier invocation fails even when Hypothesis shrinks to the same case hash.

The harness checks:

1. the evidence belongs to the exact shrunk Hypothesis example and fresh invocation;
2. observations fall inside the bounded execution window and identify physical sources;
3. every required evidence section exists;
4. every semantic predicate is evaluated in Python;
5. Litmus evidence includes an independently observed target effect and request overlap;
6. a repository-owned oracle exists for the contract; unresolved semantic contracts
   fail before a generic driver can make them green.

## Mapped command wrapper

`mapped_contract_driver.py` is a protocol-v3 adapter.  `EVE_TRADE_CONTRACT_DRIVER_MAP`
points to a JSON object mapping exact test names to argv arrays.  The mapped command
receives the full protocol-v3 request on stdin and prints raw observation JSON.
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

A mapped probe should return observations rather than expected answers. The wrapper
adds the trusted identity envelope, but the probe must supply `evidence_id`,
`collected_at`, `observations`, and the contract-specific raw sections.

```json
{
  "scenario": {
    "action_executed": true,
    "preconditions_satisfied": true,
    "generated_case_applied": true,
    "applied_case_sha256": "<request case_sha256>",
    "applied_parameters": {"generated_parameter": "actual applied value"}
  },
  "fault": {
    "injected": true,
    "kind": "process_crash",
    "active_during_target_window": true
  },
  "recovery": {
    "completed": true,
    "completed_at": "2026-08-11T00:00:10Z",
    "deadline_at": "2026-08-11T00:01:00Z",
    "raw_probes": []
  },
  "outcome": {"accepted": true},
  "effects": {"business_effect_count": 1},
  "evidence_id": "fresh-opaque-value-at-least-16-characters",
  "collected_at": "2026-08-11T00:00:11Z",
  "observations": []
}
```

Empty observation/probe arrays above are placeholders and will be rejected. The exact
raw fields are contract-specific. Generate the legacy predicate view with:

```bash
python tools/export_evidence_specs.py
```
