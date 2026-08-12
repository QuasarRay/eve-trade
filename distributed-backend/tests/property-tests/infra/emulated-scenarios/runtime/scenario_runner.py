"""Execute one registered deterministic-emulation scenario against real services.

The AnySystem process chooses and gates controller actions.  This runner is the
orchestration bridge: it executes only invocation-declared real-deployment
adapters, returns factual barrier evidence, and writes oracle-consumable facts.
It deliberately contains no business assertions.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from action_executor import ActionExecutor, ActionFailure, ExecutionContext, fact, redact
from catalog import PROPERTY_ROOT, SCENARIO_ROOT


ANYSYSTEM_REVISION = "74613a368c73fb12f25778ce33ca11c9a833da96"
CONTROLLER_PROTOCOL = "eve-trade.anysystem-controller/v1"
INVOCATION_SCHEMA = "eve-trade.scenario-invocation/v1"
IMPLEMENTED_PENDING = "IMPLEMENTED_BUT_EMULATION_VERIFICATION_PENDING"
EXPECTED_DAGGER_VERSION = "0.21.8"
RUN_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SCENARIO_ID = re.compile(r"^[a-z0-9_]+$")
VARIANT_SENTINEL = "${ANYSYSTEM_SELECTED_VARIANT}"


class ScenarioRunFailure(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScenarioRunFailure(f"cannot load JSON object {path}: {error}") from error
    if not isinstance(value, dict):
        raise ScenarioRunFailure(f"JSON document must be an object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(redact(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioRunFailure(message)


def validate_invocation(invocation: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "scenario_id",
        "scenario_revision",
        "run_id",
        "namespace",
        "AnySystem_seed",
        "source_revision",
        "deployment_artifact_revisions",
        "scenario_parameters",
        "business_property_input",
        "action_inputs",
    }
    require(set(invocation) == required, "invocation fields differ from the v1 closed schema")
    require(invocation["schema_version"] == INVOCATION_SCHEMA, "unsupported invocation schema")
    require(isinstance(invocation["scenario_id"], str) and bool(SCENARIO_ID.fullmatch(invocation["scenario_id"])), "invalid scenario ID")
    require(isinstance(invocation["scenario_revision"], str) and bool(invocation["scenario_revision"]), "scenario revision is required")
    require(isinstance(invocation["run_id"], str) and bool(RUN_ID.fullmatch(invocation["run_id"])), "run ID must be a DNS-safe label")
    require(isinstance(invocation["namespace"], str) and bool(RUN_ID.fullmatch(invocation["namespace"])), "namespace must be a DNS-safe label")
    require(
        invocation["namespace"] == f"emu-{invocation['run_id']}",
        "scenario namespace must be the exact run-isolated emu-<run_id> namespace",
    )
    seed = invocation["AnySystem_seed"]
    require(isinstance(seed, int) and not isinstance(seed, bool) and 0 <= seed <= (2**64 - 1), "AnySystem seed must be an unsigned 64-bit integer")
    require(
        isinstance(invocation["source_revision"], str)
        and bool(re.fullmatch(r"[0-9a-f]{40}", invocation["source_revision"])),
        "source revision must be an exact 40-character Git commit",
    )
    revisions = invocation["deployment_artifact_revisions"]
    required_revisions = {
        "application_images",
        "deployment_manifest_sha256",
        "configuration_sha256",
        "database_schema_version",
        "dependency_lock_digests",
        "kubernetes_environment",
        "Dagger_version",
        "Litmus_version",
        "AnySystem_revision",
    }
    require(isinstance(revisions, dict), "deployment artifact revisions must be an object")
    require(
        set(revisions) == required_revisions,
        "deployment artifact revision fields differ from the closed fidelity schema",
    )
    require(revisions.get("AnySystem_revision") == ANYSYSTEM_REVISION, "fidelity record has a different AnySystem revision")
    require(
        all(revisions.get(name) not in (None, "", {}, []) for name in required_revisions),
        "deployment fidelity inputs cannot be empty",
    )
    for digest_name in ("deployment_manifest_sha256", "configuration_sha256"):
        require(
            isinstance(revisions[digest_name], str) and bool(re.fullmatch(r"[0-9a-f]{64}", revisions[digest_name])),
            f"{digest_name} must be an exact SHA-256 digest",
        )
    require(revisions.get("Dagger_version") == EXPECTED_DAGGER_VERSION, "fidelity record has a different Dagger version")
    litmus_contracts = load_json(PROPERTY_ROOT / "infra" / "litmus-contracts.json")
    require(
        revisions.get("Litmus_version") == litmus_contracts.get("litmus_core"),
        "fidelity record differs from the exact pinned Litmus release inputs",
    )
    images = revisions.get("application_images")
    require(isinstance(images, dict) and bool(images), "application image fidelity map is empty")
    require(
        all(
            isinstance(value, str)
            and bool(re.fullmatch(r"(?:[a-z0-9._/:@-]+@)?sha256:[0-9a-f]{64}", value))
            for value in images.values()
        ),
        "every application image fidelity value must be an exact SHA-256 image digest",
    )
    locks = revisions.get("dependency_lock_digests")
    require(isinstance(locks, dict) and bool(locks), "dependency lock digest map is empty")
    require(
        all(isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value)) for value in locks.values()),
        "every dependency lock digest must be an exact SHA-256",
    )
    for name in ("scenario_parameters", "business_property_input", "action_inputs"):
        require(isinstance(invocation[name], dict), f"{name} must be an object")
    require(bool(invocation["business_property_input"]), "business property input cannot be empty")


def validate_action_inputs(scenario: dict[str, Any], invocation: dict[str, Any]) -> None:
    plan = scenario.get("action_plan")
    require(isinstance(plan, list) and bool(plan), "scenario action plan is empty")
    plan_ids = [entry.get("action_id") for entry in plan if isinstance(entry, dict)]
    require(len(plan_ids) == len(plan), "scenario action plan contains a malformed entry")
    require(len(plan_ids) == len(set(plan_ids)), "scenario action plan has duplicate action IDs")
    provided = invocation["action_inputs"]
    require(set(provided) == set(plan_ids), "invocation must declare exactly one adapter input for every planned action")
    primitive_adapters = {"KUBERNETES", "HTTP_JSON", "UDP_JSON", "POSTGRES_SQL", "NSQ_HTTP"}

    def validate_primitive(specification: Any, label: str, allowed: set[str]) -> None:
        require(isinstance(specification, dict), f"{label}: nested action must be an object")
        require(set(specification) == {"adapter", "request"}, f"{label}: nested action fields differ from the closed schema")
        require(specification.get("adapter") in allowed, f"{label}: nested adapter is not allowed for this phase")
        require(isinstance(specification.get("request"), dict), f"{label}: nested request must be an object")

    def validate_sequence(request: Any, label: str, allowed: set[str]) -> None:
        require(isinstance(request, dict) and set(request) == {"actions"}, f"{label}: action sequence request must contain only actions")
        nested = request.get("actions")
        require(isinstance(nested, list) and bool(nested), f"{label}: action sequence cannot be empty")
        for index, nested_specification in enumerate(nested):
            validate_primitive(nested_specification, f"{label}[{index}]", allowed)

    def resolve_policy_value(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("${RUN_NAMESPACE}", invocation["namespace"])
        if isinstance(value, list):
            return [resolve_policy_value(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve_policy_value(item) for key, item in value.items()}
        return value

    for action in plan:
        action_id = action["action_id"]
        specification = provided[action_id]
        require(isinstance(specification, dict), f"{action_id}: action input must be an object")
        require(set(specification) == {"adapter", "request"}, f"{action_id}: action input fields differ from the closed schema")
        policy = action.get("adapter_policy")
        require(isinstance(policy, dict), f"{action_id}: scenario omits its adapter policy")
        require(specification.get("adapter") == policy.get("adapter"), f"{action_id}: adapter differs from the scenario contract")
        request = specification.get("request")
        require(isinstance(request, dict), f"{action_id}: request must be an object")
        allowed_fields = policy.get("request_allowed_fields")
        required_fields = policy.get("request_required_fields")
        fixed_fields = policy.get("request_fixed_fields")
        require(isinstance(allowed_fields, list), f"{action_id}: malformed allowed-field policy")
        require(isinstance(required_fields, list), f"{action_id}: malformed required-field policy")
        require(isinstance(fixed_fields, dict), f"{action_id}: malformed fixed-field policy")
        require(set(request) <= set(allowed_fields), f"{action_id}: request contains a field not allowed by the scenario")
        require(set(required_fields) <= set(request), f"{action_id}: request omits scenario-required fields")
        for name, expected in fixed_fields.items():
            require(
                request.get(name) == resolve_policy_value(expected),
                f"{action_id}: {name} differs from the scenario contract",
            )
        fixed_environment = policy.get("environment_fixed_fields", {})
        require(isinstance(fixed_environment, dict), f"{action_id}: malformed fixed-environment policy")
        if fixed_environment:
            environment = request.get("environment")
            require(isinstance(environment, dict), f"{action_id}: Litmus environment must be an object")
            for name, expected in fixed_environment.items():
                require(
                    environment.get(name) == resolve_policy_value(expected),
                    f"{action_id}: Litmus environment {name} differs from the scenario contract",
                )
        if specification["adapter"] == "ACTION_SEQUENCE":
            allowed_nested = set(policy.get("allowed_nested_adapters", []))
            require(allowed_nested <= primitive_adapters, f"{action_id}: scenario permits an unknown nested adapter")
            validate_sequence(request, action_id, allowed_nested)
        elif specification["adapter"] == "LITMUS":
            independent = request.get("independent_witness")
            require(isinstance(independent, dict), f"{action_id}: Litmus action omits its independent witness")
            if independent.get("adapter") == "ACTION_SEQUENCE":
                require(set(independent) == {"adapter", "request"}, f"{action_id}: malformed independent witness")
                validate_sequence(independent.get("request"), f"{action_id}.independent_witness", primitive_adapters)
            else:
                validate_primitive(independent, f"{action_id}.independent_witness", primitive_adapters)
            if request.get("operation") == "ACTIVATE":
                target = request.get("target")
                if target == VARIANT_SENTINEL:
                    require(bool(action.get("seeded_variants")), f"{action_id}: target sentinel has no declared variants")
                else:
                    require(isinstance(target, dict), f"{action_id}: Litmus target must be an object")
                    require(bool(target.get("kind")) and bool(target.get("selector")), f"{action_id}: Litmus target must name kind and selector")


def _contains_variant_sentinel(value: Any) -> bool:
    if value == VARIANT_SENTINEL:
        return True
    if isinstance(value, list):
        return any(_contains_variant_sentinel(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_variant_sentinel(item) for item in value.values())
    return False


def _bind_variant(value: Any, selected_variant: Any) -> Any:
    if value == VARIANT_SENTINEL:
        return copy.deepcopy(selected_variant)
    if isinstance(value, list):
        return [_bind_variant(item, selected_variant) for item in value]
    if isinstance(value, dict):
        return {key: _bind_variant(item, selected_variant) for key, item in value.items()}
    return value


def bind_selected_variant(action: dict[str, Any], specification: dict[str, Any], selected_variant: Any) -> dict[str, Any]:
    request = specification["request"]
    has_sentinel = _contains_variant_sentinel(request)
    declared = action.get("seeded_variants", [])
    if declared:
        require(selected_variant in declared, f"{action['action_id']}: controller selected an undeclared variant")
        require(has_sentinel, f"{action['action_id']}: seeded choice is not bound into its real action request")
    else:
        require(selected_variant is None, f"{action['action_id']}: controller selected a variant for an unparameterized action")
        require(not has_sentinel, f"{action['action_id']}: request references a variant but the plan declares none")
    bound = copy.deepcopy(specification)
    bound["request"] = _bind_variant(request, selected_variant)
    return bound


class ControllerProcess:
    def __init__(self, binary: Path, scenario_path: Path, seed: int, parameters: dict[str, Any]):
        argv = [
            str(binary),
            "--scenario",
            str(scenario_path),
            "--seed",
            str(seed),
            "--parameters",
            json.dumps(parameters, separators=(",", ":"), sort_keys=True),
        ]
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.output: queue.Queue[str | None] = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.output.put(line)
        self.output.put(None)

    def _pump_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr.append(line)
            if sum(map(len, self.stderr)) > 65_536:
                self.stderr = self.stderr[-64:]

    def read(self, timeout_seconds: int) -> dict[str, Any]:
        try:
            line = self.output.get(timeout=timeout_seconds)
        except queue.Empty as error:
            raise ScenarioRunFailure(f"controller emitted no output within {timeout_seconds}s") from error
        if line is None:
            raise ScenarioRunFailure(
                f"controller ended before emitting its next message: {redact(''.join(self.stderr)[-4096:])}"
            )
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ScenarioRunFailure("controller emitted malformed JSON") from error
        require(isinstance(value, dict), "controller message must be an object")
        return value

    def send_barrier(self, outcome: dict[str, Any]) -> None:
        require(self.process.stdin is not None and self.process.poll() is None, "controller is not accepting barrier outcomes")
        self.process.stdin.write(json.dumps(outcome, separators=(",", ":"), sort_keys=True) + "\n")
        self.process.stdin.flush()

    def finish(self, expected_success: bool) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            return_code = self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            raise ScenarioRunFailure("controller did not terminate after protocol completion")
        if expected_success and return_code != 0:
            raise ScenarioRunFailure(f"controller exited {return_code}: {redact(''.join(self.stderr)[-4096:])}")

    def abort(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def controller_binary(explicit: str | None) -> Path:
    configured = explicit or os.environ.get("EVE_TRADE_ANYSYSTEM_CONTROLLER")
    if configured:
        path = Path(configured).resolve()
    else:
        suffix = ".exe" if os.name == "nt" else ""
        path = (Path(__file__).parent / "deterministic-controller" / "target" / "release" / f"deterministic-controller{suffix}").resolve()
    require(path.is_file(), f"AnySystem controller binary is absent: {path}")
    return path


def _validate_controller_message(message: dict[str, Any], invocation: dict[str, Any], scenario: dict[str, Any]) -> None:
    require(message.get("protocol_version") == CONTROLLER_PROTOCOL, "controller protocol mismatch")
    require(message.get("scenario_id") == invocation["scenario_id"], "controller scenario ID mismatch")
    require(message.get("scenario_revision") == invocation["scenario_revision"], "controller scenario revision mismatch")
    require(message.get("AnySystem_revision") == ANYSYSTEM_REVISION, "controller AnySystem revision mismatch")
    require(message.get("AnySystem_revision") == scenario["AnySystem_revision"], "scenario/controller AnySystem pin mismatch")
    require(message.get("AnySystem_seed") == invocation["AnySystem_seed"], "controller seed mismatch")


def _barrier_timeout(scenario: dict[str, Any], barrier: str) -> int:
    policy = scenario["barrier_timeout_policy"]
    if barrier == "deployment-ready":
        return int(policy["deployment_seconds"])
    if barrier == "recovery-witnessed":
        return int(policy["recovery_seconds"])
    return int(policy["default_seconds"])


def _require_evidence(action: dict[str, Any], result: dict[str, Any], dagger_ref: str) -> tuple[list[str], list[str]]:
    control_refs = [dagger_ref, *result["control_plane_evidence_refs"]]
    independent_refs = result["independent_effect_evidence_refs"]
    require(bool(result["facts"]), f"{action['action_id']}: adapter returned no factual observations")
    barrier = action["requires_barrier"]
    if barrier != "cleanup-complete":
        require(bool(independent_refs), f"{action['action_id']}: barrier lacks independent real-effect evidence")
    if action["kind"].startswith("LITMUS_"):
        require(bool(result["control_plane_evidence_refs"]), f"{action['action_id']}: Litmus control-plane evidence is absent")
        require(bool(independent_refs), f"{action['action_id']}: Litmus acknowledgement is not independently witnessed")
    return control_refs, independent_refs


def _build_witness(
    invocation: dict[str, Any],
    action: dict[str, Any],
    message: dict[str, Any],
    result: dict[str, Any],
    control_refs: list[str],
    independent_refs: list[str],
) -> dict[str, Any]:
    require(bool(result["relevant_entity_ids"]), "prerequisite witness lacks exact relevant entity identities")
    require(bool(result["established_conditions"]), "prerequisite witness lacks established real conditions")
    require(bool(independent_refs), "prerequisite witness lacks independent effect evidence")
    return {
        "schema_version": "eve-trade.prerequisite-witness/v1",
        "scenario_id": invocation["scenario_id"],
        "scenario_revision": invocation["scenario_revision"],
        "run_id": invocation["run_id"],
        "namespace": invocation["namespace"],
        "AnySystem_seed": invocation["AnySystem_seed"],
        "relevant_entity_ids": result["relevant_entity_ids"],
        "established_conditions": result["established_conditions"],
        "controller_state": f"AWAITING:{action['requires_barrier']}",
        "logical_tick_or_phase": message["logical_tick"],
        "real_observation_timestamp": utc_now(),
        "trace_position": message["trace_position"],
        "control_plane_evidence_refs": control_refs,
        "independent_effect_evidence_refs": independent_refs,
    }


def run_scenario(invocation_path: Path, artifact_root: Path, explicit_controller: str | None = None) -> Path:
    invocation = load_json(invocation_path)
    validate_invocation(invocation)
    scenario_path = SCENARIO_ROOT / invocation["scenario_id"] / "scenario.json"
    scenario = load_json(scenario_path)
    require(scenario.get("scenario_id") == invocation["scenario_id"], "scenario document identity mismatch")
    require(scenario.get("scenario_revision") == invocation["scenario_revision"], "invocation uses a stale scenario revision")
    require(scenario.get("status") == IMPLEMENTED_PENDING, f"scenario is not executable: {scenario.get('status')}")
    require(scenario.get("AnySystem_revision") == ANYSYSTEM_REVISION, "scenario uses an unexpected AnySystem revision")
    validate_action_inputs(scenario, invocation)

    run_dir = artifact_root.resolve() / invocation["run_id"] / invocation["scenario_id"]
    require(not run_dir.exists(), f"refusing to overwrite an existing scenario run: {run_dir}")
    run_dir.mkdir(parents=True)
    write_json(run_dir / "invocation.redacted.json", invocation)
    started_at = utc_now()
    observations = {
        "schema_version": "eve-trade.emulation-observations/v1",
        "scenario_id": invocation["scenario_id"],
        "run_id": invocation["run_id"],
        "facts": [
            fact("scenario_parameters", invocation["scenario_parameters"], "SCENARIO_INPUT", f"invocation://{invocation['run_id']}/scenario-parameters"),
            fact("business_property_input", invocation["business_property_input"], "SCENARIO_INPUT", f"invocation://{invocation['run_id']}/business-property-input"),
        ],
    }
    action_trace: list[dict[str, Any]] = []
    witness_outcomes: list[dict[str, Any]] = []
    prerequisite_witness: dict[str, Any] | None = None
    plan = scenario["action_plan"]
    controller = ControllerProcess(
        controller_binary(explicit_controller),
        scenario_path.resolve(),
        invocation["AnySystem_seed"],
        invocation["scenario_parameters"],
    )
    completion: dict[str, Any] | None = None
    current_action: dict[str, Any] | None = None
    cleanup_completed = False
    try:
        for position, planned_action in enumerate(plan):
            message = controller.read(_barrier_timeout(scenario, planned_action["requires_barrier"]))
            _validate_controller_message(message, invocation, scenario)
            require(message.get("type") == "ACTION", f"controller emitted {message.get('type')!r} before plan completion")
            action = message.get("action")
            require(isinstance(action, dict), "controller ACTION has no action object")
            require(action == planned_action, f"controller action at position {position} differs from the registered plan")
            require(message.get("trace_position") == 1 + (position * 2), "controller trace position is not barrier-gated")
            current_action = action
            specification = bind_selected_variant(
                action,
                invocation["action_inputs"][action["action_id"]],
                message.get("selected_variant"),
            )
            context = ExecutionContext(
                scenario_id=invocation["scenario_id"],
                scenario_revision=invocation["scenario_revision"],
                run_id=invocation["run_id"],
                action_id=action["action_id"],
                namespace=invocation["namespace"],
                artifact_root=run_dir,
            )
            action_started = time.monotonic()
            result = ActionExecutor(context).execute(specification)
            elapsed = time.monotonic() - action_started
            timeout = _barrier_timeout(scenario, action["requires_barrier"])
            require(elapsed <= timeout, f"{action['action_id']}: adapter exceeded its {timeout}s barrier timeout")
            dagger_ref = f"dagger://{invocation['run_id']}/{action['action_id']}/{position}"
            control_refs, independent_refs = _require_evidence(action, result, dagger_ref)
            observations["facts"].extend(result["facts"])
            observations["facts"].append(
                fact(
                    "dagger_action_receipt",
                    {
                        "action_id": action["action_id"],
                        "action_kind": action["kind"],
                        "logical_tick": message["logical_tick"],
                        "selected_variant": message.get("selected_variant"),
                        "physical_duration_seconds": elapsed,
                    },
                    "DAGGER_ORCHESTRATOR",
                    dagger_ref,
                    {"run_id": invocation["run_id"], "action_id": action["action_id"]},
                )
            )
            trace_entry = {
                "position": position,
                "action_id": action["action_id"],
                "action_kind": action["kind"],
                "barrier": action["requires_barrier"],
                "logical_tick": message["logical_tick"],
                "selected_variant": message.get("selected_variant"),
                "adapter_result": result,
                "dagger_receipt_ref": dagger_ref,
            }
            action_trace.append(trace_entry)
            witness_outcomes.append(
                {
                    "action_id": action["action_id"],
                    "barrier": action["requires_barrier"],
                    "outcome": "SATISFIED",
                    "control_plane_evidence_refs": control_refs,
                    "independent_effect_evidence_refs": independent_refs,
                }
            )
            if action["action_id"] in {"establish-controlled-prerequisite", "witness-scenario-effect"}:
                prerequisite_witness = _build_witness(invocation, action, message, result, control_refs, independent_refs)
            controller.send_barrier(
                {
                    "barrier": action["requires_barrier"],
                    "outcome": "SATISFIED",
                    "control_plane_evidence_refs": control_refs,
                    "independent_effect_evidence_refs": independent_refs,
                    "observed_facts": result["facts"],
                }
            )
            cleanup_completed = action["action_id"] == "cleanup-scenario-owned-state"
            current_action = None

        completion = controller.read(int(scenario["barrier_timeout_policy"]["default_seconds"]))
        _validate_controller_message(completion, invocation, scenario)
        require(completion.get("type") == "COMPLETE", "controller did not emit COMPLETE after the final barrier")
        require(isinstance(completion.get("trace"), list) and bool(completion["trace"]), "controller completion lacks trace")
        require(prerequisite_witness is not None, "run completed without a prerequisite witness")
        require(cleanup_completed, "run completed without scoped cleanup")
        controller.finish(expected_success=True)
        evidence = {
            "schema_version": "eve-trade.emulation-run-evidence/v1",
            "scenario_id": invocation["scenario_id"],
            "scenario_revision": invocation["scenario_revision"],
            "run_id": invocation["run_id"],
            "namespace": invocation["namespace"],
            "AnySystem_revision": ANYSYSTEM_REVISION,
            "AnySystem_seed": invocation["AnySystem_seed"],
            "controller_trace": completion["trace"],
            "source_revision": invocation["source_revision"],
            "deployment_artifact_revisions": invocation["deployment_artifact_revisions"],
            "witness_outcomes": witness_outcomes,
            "dagger_action_trace": action_trace,
            "prerequisite_witness": prerequisite_witness,
            "observations": observations,
            "physical_timing": {"started_at": started_at, "finished_at": utc_now()},
        }
        write_json(run_dir / "observations.json", observations)
        write_json(run_dir / "prerequisite-witness.json", prerequisite_witness)
        write_json(run_dir / "run-evidence.json", evidence)
        return run_dir / "run-evidence.json"
    except Exception as error:
        if current_action is not None:
            try:
                controller.send_barrier(
                    {
                        "barrier": current_action["requires_barrier"],
                        "outcome": "UNSATISFIED",
                        "control_plane_evidence_refs": [],
                        "independent_effect_evidence_refs": [],
                        "observed_facts": {"adapter_failure": str(redact(str(error)))},
                    }
                )
                failed = controller.read(10)
                if isinstance(failed.get("trace"), list):
                    completion = failed
            except Exception:
                pass
        controller.abort()
        emergency_cleanup: dict[str, Any]
        if cleanup_completed:
            emergency_cleanup = {"attempted": False, "reason": "planned cleanup already completed"}
        else:
            cleanup_id = "cleanup-scenario-owned-state"
            try:
                cleanup_context = ExecutionContext(
                    scenario_id=invocation["scenario_id"],
                    scenario_revision=invocation["scenario_revision"],
                    run_id=invocation["run_id"],
                    action_id=cleanup_id,
                    namespace=invocation["namespace"],
                    artifact_root=run_dir,
                )
                cleanup_result = ActionExecutor(cleanup_context).execute(invocation["action_inputs"][cleanup_id])
                emergency_cleanup = {
                    "attempted": True,
                    "completed": True,
                    "adapter_result": cleanup_result,
                }
            except Exception as cleanup_error:
                emergency_cleanup = {
                    "attempted": True,
                    "completed": False,
                    "error_type": type(cleanup_error).__name__,
                    "error": str(redact(str(cleanup_error))),
                }
        write_json(
            run_dir / "failure.json",
            {
                "schema_version": "eve-trade.emulation-run-failure/v1",
                "scenario_id": invocation["scenario_id"],
                "scenario_revision": invocation["scenario_revision"],
                "run_id": invocation["run_id"],
                "namespace": invocation["namespace"],
                "AnySystem_revision": ANYSYSTEM_REVISION,
                "AnySystem_seed": invocation["AnySystem_seed"],
                "failed_action_id": current_action.get("action_id") if current_action else None,
                "error_type": type(error).__name__,
                "error": str(redact(str(error))),
                "controller_trace": completion.get("trace", []) if completion else [],
                "dagger_action_trace": action_trace,
                "witness_outcomes": witness_outcomes,
                "emergency_scoped_cleanup": emergency_cleanup,
                "observations": observations,
                "physical_timing": {"started_at": started_at, "finished_at": utc_now()},
            },
        )
        if isinstance(error, (ScenarioRunFailure, ActionFailure)):
            raise
        raise ScenarioRunFailure(str(error)) from error


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invocation", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--controller")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    evidence = run_scenario(args.invocation.resolve(), args.artifacts.resolve(), args.controller)
    print(json.dumps({"run_evidence": str(evidence)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
