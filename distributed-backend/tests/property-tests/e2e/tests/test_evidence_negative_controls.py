from __future__ import annotations

import copy
import json
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

from eve_trade_hypothesis.chaos_oracles import validate_chaos_oracle
from eve_trade_hypothesis.evidence_integrity import (
    CHAOS_EVIDENCE_SCHEMA,
    EVIDENCE_SCHEMA,
    PROTOCOL_VERSION,
    EvidenceIntegrityError,
    isoformat_utc,
    validate_chaos_evidence,
    validate_execution_identity,
)
from eve_trade_hypothesis.external import ExternalContractDriver, canonical_case_sha256
from eve_trade_hypothesis.requirements import litmus_contract_for


CONTRACT = "test_litmus_pod_delete_experiment_targets_only_selected_eve_trade_workload_labels"


def _case(spec):
    result = {"category": 101, "nonce": "negative-control"}
    for name, definition in spec["generated_parameters"].items():
        result[name] = definition.get("values", [definition.get("min")])[0]
    return result


def evidence_fixture():
    spec = litmus_contract_for(CONTRACT)
    now = datetime.now(timezone.utc)
    issued = now - timedelta(seconds=8)
    active = now - timedelta(seconds=6)
    started = now - timedelta(seconds=5)
    finished = now - timedelta(seconds=4)
    recovery_started = now - timedelta(seconds=3)
    ready = now - timedelta(seconds=2)
    cleared = now - timedelta(seconds=1)
    case = _case(spec)
    case_sha = canonical_case_sha256(case)
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "evidence_schema": EVIDENCE_SCHEMA,
        "contract": CONTRACT,
        "case": case,
        "case_sha256": case_sha,
        "execution": {
            "run_id": "pt-negative-controls",
            "invocation_id": "invocation-0123456789",
            "nonce": "nonce-01234567890123456789",
            "issued_at": isoformat_utc(issued),
            "max_age_seconds": 600,
        },
    }
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "encore-backend-before",
            "namespace": "eve-trade",
            "uid": "pod-uid-before",
            "resourceVersion": "101",
            "labels": {"app.kubernetes.io/name": "encore-backend"},
        },
    }
    engine_environment = [
        {
            "name": str(env_name),
            "value": str(value).replace("${APP_NAMESPACE}", "eve-trade"),
        }
        for env_name, value in spec["fault_injection"].get(
            "static_environment", {}
        ).items()
    ]
    engine_environment.extend(
        {
            "name": str(definition["litmus_env"]),
            "value": str(case[parameter]),
        }
        for parameter, definition in spec["generated_parameters"].items()
        if definition.get("litmus_env")
    )
    engine = {
        "apiVersion": "litmuschaos.io/v1alpha1",
        "kind": "ChaosEngine",
        "metadata": {
            "name": "engine",
            "uid": "engine-uid",
            "resourceVersion": "201",
            "labels": {
                "eve-trade.io/execution-id": "engine",
                "eve-trade.io/run-id": request["execution"]["run_id"],
                "eve-trade.io/contract-hash": case_sha[:16],
            },
        },
        "spec": {
            "engineState": "active",
            "annotationCheck": "false",
            "chaosServiceAccount": spec["fault_injection"]["service_account"],
            "appinfo": {
                "appns": "eve-trade",
                "applabel": spec["target"]["selector"],
                "appkind": spec["target"]["kind"],
            },
            "experiments": [
                {
                    "name": spec["fault_injection"]["experiment"],
                    "spec": {"components": {"env": engine_environment}},
                }
            ],
        },
    }
    chaos_result = {
        "apiVersion": "litmuschaos.io/v1alpha1",
        "kind": "ChaosResult",
        "metadata": {
            "name": "engine-pod-delete",
            "uid": "result-uid",
            "resourceVersion": "301",
            "labels": {"chaosUID": "engine-uid"},
        },
        "status": {"experimentStatus": {"phase": "Completed", "verdict": "Pass"}},
    }
    before_after = {
        "selector": "app.kubernetes.io/name=encore-backend",
        "kind": "deployment",
        "workload": "encore-backend",
        "desired": 1,
        "ready": 1,
    }
    def successful_probe(finished_at: datetime, latency_ms: float = 12.0):
        return {
            "success": True,
            "latency_ms": latency_ms,
            "status": 200,
            "error": None,
            "started_at": isoformat_utc(finished_at - timedelta(milliseconds=20)),
            "finished_at": isoformat_utc(finished_at),
        }

    result = {
        "protocol_version": PROTOCOL_VERSION,
        "evidence_schema": EVIDENCE_SCHEMA,
        "chaos_evidence_schema": CHAOS_EVIDENCE_SCHEMA,
        "contract": CONTRACT,
        "case_sha256": case_sha,
        "evidence_id": "evidence-01234567890123456789",
        "collected_at": isoformat_utc(now),
        "execution": {
            "run_id": request["execution"]["run_id"],
            "invocation_id": request["execution"]["invocation_id"],
            "nonce": request["execution"]["nonce"],
        },
        "scenario": {
            "action_executed": True,
            "preconditions_satisfied": True,
            "generated_case_applied": True,
            "applied_case_sha256": case_sha,
            "applied_parameters": {
                name: case[name] for name in spec["generated_parameters"]
            },
        },
        "fault": {
            "injected": True,
            "kind": "pod_deletion",
            "family": "pod_delete",
            "active_during_target_window": True,
            "requests_crossing_target_window": 2,
            "window": {"active_at": isoformat_utc(active), "cleared_at": isoformat_utc(cleared)},
            "target": {
                "selector": spec["target"]["selector"],
                "resource_identity": "pod-uid-before",
                "selected_resources": [pod],
            },
            "effect_signals": [
                {
                    "source": "kubernetes-api",
                    "kind": "pod_uid_transition",
                    "observed_at": isoformat_utc(active),
                    "raw": {"before_uids": ["pod-uid-before"], "active_uids": ["pod-uid-after"]},
                },
                {
                    "source": "litmus-runner",
                    "kind": "experiment_log",
                    "observed_at": isoformat_utc(active),
                    "raw": {"lines": ["chaos injection completed for target pod"]},
                },
            ],
            "litmus": {
                "experiment": "pod-delete",
                "phase": "Completed",
                "verdict": "Pass",
                "engine_uid": "engine-uid",
                "result_uid": "result-uid",
                "target_resource_version": "101",
                "engine_resource": engine,
                "result_resource": chaos_result,
            },
        },
        "workload": {
            "requests": [
                {
                    "request_id": "request-1",
                    "started_at": isoformat_utc(started),
                    "finished_at": isoformat_utc(finished),
                    "raw": {"success": False, "status": 503},
                },
                {
                    "request_id": "request-2",
                    "started_at": isoformat_utc(started),
                    "finished_at": isoformat_utc(finished),
                    "raw": {"success": True, "status": 200},
                }
            ]
        },
        "recovery": {
            "completed": True,
            "started_at": isoformat_utc(recovery_started),
            "completed_at": isoformat_utc(cleared),
            "deadline_at": isoformat_utc(
                recovery_started
                + timedelta(seconds=int(case["recovery_deadline_seconds"]))
            ),
            "raw_probes": [
                {
                    "source": "kubernetes-api",
                    "kind": "target_ready",
                    "observed_at": isoformat_utc(ready),
                    "raw": {
                        "desired": 1,
                        "ready": 1,
                        "probe_samples": [successful_probe(ready) for _ in range(3)],
                    },
                },
                {
                    "source": "kubernetes-api",
                    "kind": "fault_absent",
                    "observed_at": isoformat_utc(cleared),
                    "raw": {
                        "remaining_chaos_resources": [],
                        "target_effect_active": False,
                        "post_cleanup_probe_samples": [
                            successful_probe(cleared - timedelta(milliseconds=50))
                            for _ in range(8)
                        ],
                        "target_state": before_after,
                    },
                },
            ],
        },
        "comparison": {"before": before_after, "after": before_after},
        "outcome": {"observed_request_count": 2},
        "observations": [
            {
                "source": "kubernetes-api",
                "kind": "chaosengine-created",
                "observed_at": isoformat_utc(active - timedelta(milliseconds=500)),
                "resource": engine,
            },
            {
                "source": "active-probe",
                "kind": "control-baseline",
                "observed_at": isoformat_utc(issued + timedelta(seconds=1)),
                "samples": [{"success": True, "status": 200}],
            },
            {
                "source": "kubernetes-api",
                "kind": "target-before",
                "observed_at": isoformat_utc(active),
                "resource": pod,
            },
            {
                "source": "workload-http",
                "kind": "request",
                "observed_at": isoformat_utc(finished),
                "raw": {"request_id": "request-1", "status": 503},
            },
        ],
    }
    return request, result, spec


def validate_all(request, result, spec):
    validate_execution_identity(request, result, seen_evidence_ids=set())
    validate_chaos_evidence(request, result, spec)
    validate_chaos_oracle(CONTRACT, request, result, spec)


def test_structurally_valid_independent_pod_delete_evidence_is_accepted():
    validate_all(*evidence_fixture())


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda result: result["fault"].update(injected=False), "fault.injected"),
        (
            lambda result: result["fault"]["effect_signals"][0]["raw"].update(
                active_uids=["pod-uid-before"]
            ),
            "does not prove a pod UID transition",
        ),
        (
            lambda result: result["fault"]["effect_signals"][0]["raw"].update(
                before_uids=["unrelated-pod"]
            ),
            "physical pod transition is not bound",
        ),
        (
            lambda result: result["fault"]["litmus"]["engine_resource"]["spec"][
                "appinfo"
            ].update(applabel="app.kubernetes.io/name=unrelated"),
            "raw ChaosEngine appinfo.applabel mismatch",
        ),
        (
            lambda result: result["fault"]["litmus"]["engine_resource"]["spec"][
                "experiments"
            ][0]["spec"]["components"]["env"][0].update(value="wrong-generated-value"),
            "raw ChaosEngine generated environment",
        ),
        (lambda result: result.update(case_sha256="wrong"), "case_sha256 mismatch"),
        (lambda result: result.update(contract="wrong-contract"), "contract mismatch"),
        (lambda result: result.update(observations=[]), "observations must contain"),
        (lambda result: result.update(ok=True), "generic ok responses"),
        (
            lambda result: result["fault"]["litmus"].update(phase="Running"),
            "evidence must be Completed",
        ),
        (
            lambda result: result["fault"]["litmus"]["result_resource"]["metadata"]["labels"].update(
                chaosUID="different-engine-uid"
            ),
            "result_resource.metadata.labels.chaosUID mismatch",
        ),
        (lambda result: result["recovery"].update(completed=False), "recovery.completed"),
        (
            lambda result: result["recovery"].update(
                deadline_at=isoformat_utc(datetime.now(timezone.utc) + timedelta(hours=1))
            ),
            "deadline_at is not derived from generated",
        ),
        (
            lambda result: result["workload"].update(requests=[]),
            "workload.requests count does not match",
        ),
        (
            lambda result: result["workload"]["requests"][0]["raw"].update(
                success=True
            ),
            "pod deletion was not observed",
        ),
        (
            lambda result: result["observations"][0].update(expected=True),
            "collector-authored oracle fields",
        ),
        (
            lambda result: result["scenario"]["applied_parameters"].update(
                fault_duration_seconds=999
            ),
            "scenario.applied_parameters.fault_duration_seconds mismatch",
        ),
        (
            lambda result: result["observations"][0].update(
                observed_at=result["fault"]["window"]["cleared_at"]
            ),
            "does not respect generated start_offset_ms",
        ),
        (
            lambda result: result["recovery"]["raw_probes"][1]["raw"][
                "post_cleanup_probe_samples"
            ][0].update(success=False),
            "post-cleanup application probe was unsuccessful",
        ),
    ],
)
def test_hostile_evidence_mutations_are_rejected(mutate, message):
    request, result, spec = evidence_fixture()
    mutate(result)
    with pytest.raises(EvidenceIntegrityError, match=re.escape(message)):
        validate_all(request, result, spec)


def test_replayed_evidence_id_is_rejected_even_when_case_hash_matches():
    request, result, _ = evidence_fixture()
    seen: set[str] = set()
    validate_execution_identity(request, result, seen_evidence_ids=seen)
    with pytest.raises(EvidenceIntegrityError, match="replayed evidence_id"):
        validate_execution_identity(request, result, seen_evidence_ids=seen)


def test_evidence_generated_before_current_invocation_is_rejected():
    request, result, _ = evidence_fixture()
    result["collected_at"] = isoformat_utc(
        datetime.fromisoformat(request["execution"]["issued_at"].replace("Z", "+00:00"))
        - timedelta(seconds=1)
    )
    with pytest.raises(EvidenceIntegrityError, match="predates the current invocation"):
        validate_execution_identity(request, result, seen_evidence_ids=set())


def test_fault_entirely_outside_workload_window_is_rejected():
    request, result, spec = evidence_fixture()
    cleared = datetime.fromisoformat(result["fault"]["window"]["cleared_at"].replace("Z", "+00:00"))
    for item in result["workload"]["requests"]:
        item["started_at"] = isoformat_utc(cleared + timedelta(milliseconds=10))
        item["finished_at"] = isoformat_utc(cleared + timedelta(milliseconds=20))
    with pytest.raises(EvidenceIntegrityError, match="no workload request crossed"):
        validate_chaos_evidence(request, result, spec)


def test_chaosresult_without_family_specific_target_effect_is_rejected():
    request, result, spec = evidence_fixture()
    result["fault"]["effect_signals"] = [result["fault"]["effect_signals"][1]]
    with pytest.raises(EvidenceIntegrityError, match="distinct sources"):
        validate_chaos_evidence(request, result, spec)


def test_noop_fault_driver_cannot_make_implemented_chaos_property_green(tmp_path):
    script = tmp_path / "noop_driver.py"
    script.write_text(
        "import json,secrets,sys\n"
        "from datetime import datetime,timezone\n"
        "r=json.load(sys.stdin); now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')\n"
        "out={'protocol_version':3,'evidence_schema':'eve-trade.external-evidence/v3',"
        "'contract':r['contract'],'case_sha256':r['case_sha256'],'evidence_id':secrets.token_urlsafe(24),"
        "'collected_at':now,'execution':{k:r['execution'][k] for k in ('run_id','invocation_id','nonce')},"
        "'scenario':{'action_executed':True,'preconditions_satisfied':True,'generated_case_applied':True,"
        "'applied_case_sha256':r['case_sha256']},'observations':[{'source':'kubernetes-api','kind':'noop',"
        "'observed_at':now,'raw':{'resources':[]}}],'chaos_evidence_schema':'eve-trade.chaos-evidence/v1',"
        "'fault':{'injected':False}}\n"
        "print(json.dumps(out))\n",
        encoding="utf-8",
    )
    spec = litmus_contract_for(CONTRACT)
    driver = ExternalContractDriver(
        f'"{sys.executable}" "{script}"',
        repo_root=tmp_path,
        strict=True,
        role="fault",
        run_id="pt-noop-driver",
    )
    with pytest.raises(AssertionError, match="fault.injected"):
        driver.run(CONTRACT, _case(spec))
