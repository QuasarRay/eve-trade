"""Independent precondition oracle for real-deployment-backed business tests."""
from __future__ import annotations

from typing import Any, Mapping


REAL_PROVENANCE = {
    "KUBERNETES_CONTROL_PLANE",
    "REAL_APPLICATION",
    "REAL_DATABASE",
    "REAL_BROKER",
    "INDEPENDENT_NETWORK_PROBE",
    "INDEPENDENT_RESOURCE_PROBE",
}


def assert_real_deployment_prerequisite(evidence: Mapping[str, Any]) -> None:
    assert evidence.get("schema_version") == "eve-trade.emulation-run-evidence/v1"
    action_trace = evidence.get("dagger_action_trace")
    assert isinstance(action_trace, list) and action_trace, "Dagger action trace is absent"
    action_ids = [entry.get("action_id") for entry in action_trace]
    assert action_ids[0] == "deploy-real-components", "real deployment was not the first controlled action"
    assert "execute-identified-workload" in action_ids, "identified real workload path was not invoked"
    assert "collect-factual-observations" in action_ids, "real observations were not collected"
    assert action_ids[-1] == "cleanup-scenario-owned-state", "scoped cleanup did not complete"

    witness = evidence.get("prerequisite_witness")
    assert isinstance(witness, dict), "prerequisite witness is absent"
    assert witness.get("scenario_id") == evidence.get("scenario_id")
    assert witness.get("scenario_revision") == evidence.get("scenario_revision")
    assert witness.get("run_id") == evidence.get("run_id")
    assert witness.get("namespace") == evidence.get("namespace")
    assert witness.get("AnySystem_seed") == evidence.get("AnySystem_seed")
    assert witness.get("established_conditions"), "prerequisite witness has no established condition"
    assert witness.get("relevant_entity_ids"), "prerequisite witness has no exact entity identity"
    assert witness.get("independent_effect_evidence_refs"), "prerequisite was not independently witnessed"

    observations = evidence.get("observations")
    assert isinstance(observations, dict) and observations.get("facts"), "observation domain is empty"
    provenances = {fact.get("provenance") for fact in observations["facts"] if isinstance(fact, dict)}
    assert provenances & REAL_PROVENANCE, "controller/orchestrator output substituted for every real observation"
