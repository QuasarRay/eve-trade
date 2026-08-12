//! AnySystem-backed deterministic scenario controller.
//!
//! The only modeled process is this controller. EVE Trade, Kubernetes,
//! PostgreSQL, NSQ, and network behavior are never represented in AnySystem.

use anysystem::{Context, Message, Process, System};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Action {
    action_id: String,
    kind: String,
    requires_barrier: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    seeded_variants: Vec<Value>,
    #[serde(flatten)]
    metadata: serde_json::Map<String, Value>,
}

#[derive(Debug, Deserialize)]
struct Scenario {
    scenario_id: String,
    scenario_revision: String,
    #[allow(non_snake_case)]
    AnySystem_revision: String,
    action_plan: Vec<Action>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct BarrierOutcome {
    barrier: String,
    outcome: String,
    #[serde(default)]
    control_plane_evidence_refs: Vec<String>,
    #[serde(default)]
    independent_effect_evidence_refs: Vec<String>,
    #[serde(default)]
    observed_facts: Value,
}

#[derive(Clone, Debug, Serialize)]
struct TraceEntry {
    trace_position: usize,
    logical_tick: u64,
    event: String,
    action_id: Option<String>,
    barrier: Option<String>,
    outcome: Option<String>,
    selected_variant: Option<Value>,
}

#[derive(Clone)]
struct Controller {
    scenario_id: String,
    scenario_revision: String,
    anysystem_revision: String,
    seed: u64,
    parameters: Value,
    plan: Vec<Action>,
    next_action: usize,
    awaiting: Option<(String, String)>,
    logical_tick: u64,
    trace: Vec<TraceEntry>,
    started: bool,
    failed: bool,
}

impl Controller {
    fn new(scenario: Scenario, seed: u64, parameters: Value) -> Self {
        Self {
            scenario_id: scenario.scenario_id,
            scenario_revision: scenario.scenario_revision,
            anysystem_revision: scenario.AnySystem_revision,
            seed,
            parameters,
            plan: scenario.action_plan,
            next_action: 0,
            awaiting: None,
            logical_tick: 0,
            trace: Vec::new(),
            started: false,
            failed: false,
        }
    }

    fn trace(
        &mut self,
        event: &str,
        action_id: Option<String>,
        barrier: Option<String>,
        outcome: Option<String>,
        selected_variant: Option<Value>,
    ) {
        self.trace.push(TraceEntry {
            trace_position: self.trace.len(),
            logical_tick: self.logical_tick,
            event: event.to_owned(),
            action_id,
            barrier,
            outcome,
            selected_variant,
        });
    }

    fn emit(&mut self, tip: &str, body: Value, ctx: &mut Context) {
        ctx.send_local(Message::new(tip.to_owned(), body.to_string()));
    }

    fn emit_next_action(&mut self, ctx: &mut Context) {
        if self.failed {
            return;
        }
        if self.next_action >= self.plan.len() {
            self.logical_tick += 1;
            self.trace("COMPLETE", None, None, None, None);
            let body = json!({
                "protocol_version": "eve-trade.anysystem-controller/v1",
                "type": "COMPLETE",
                "scenario_id": self.scenario_id.clone(),
                "scenario_revision": self.scenario_revision.clone(),
                "AnySystem_revision": self.anysystem_revision.clone(),
                "AnySystem_seed": self.seed,
                "parameters": self.parameters.clone(),
                "logical_tick": self.logical_tick,
                "trace": self.trace.clone(),
            });
            self.emit("COMPLETE", body, ctx);
            return;
        }

        let action = self.plan[self.next_action].clone();
        let selected_variant = if action.seeded_variants.is_empty() {
            None
        } else {
            let index = ((ctx.rand() * action.seeded_variants.len() as f64).floor() as usize)
                .min(action.seeded_variants.len() - 1);
            Some(action.seeded_variants[index].clone())
        };
        self.logical_tick += 1;
        self.awaiting = Some((action.action_id.clone(), action.requires_barrier.clone()));
        self.trace(
            "ACTION_EMITTED",
            Some(action.action_id.clone()),
            Some(action.requires_barrier.clone()),
            None,
            selected_variant.clone(),
        );
        let body = json!({
            "protocol_version": "eve-trade.anysystem-controller/v1",
            "type": "ACTION",
            "scenario_id": self.scenario_id.clone(),
            "scenario_revision": self.scenario_revision.clone(),
            "AnySystem_revision": self.anysystem_revision.clone(),
            "AnySystem_seed": self.seed,
            "logical_tick": self.logical_tick,
            "trace_position": self.trace.len() - 1,
            "action": action,
            "selected_variant": selected_variant,
        });
        self.emit("ACTION", body, ctx);
    }

    fn fail(&mut self, reason: &str, outcome: Option<&BarrierOutcome>, ctx: &mut Context) {
        self.failed = true;
        self.logical_tick += 1;
        self.trace(
            "FAILED",
            self.awaiting.as_ref().map(|entry| entry.0.clone()),
            outcome.map(|entry| entry.barrier.clone()),
            outcome.map(|entry| entry.outcome.clone()),
            None,
        );
        let body = json!({
            "protocol_version": "eve-trade.anysystem-controller/v1",
            "type": "FAILED",
            "scenario_id": self.scenario_id.clone(),
            "scenario_revision": self.scenario_revision.clone(),
            "AnySystem_revision": self.anysystem_revision.clone(),
            "AnySystem_seed": self.seed,
            "logical_tick": self.logical_tick,
            "reason": reason,
            "trace": self.trace.clone(),
        });
        self.emit("FAILED", body, ctx);
    }
}

impl Process for Controller {
    fn on_message(
        &mut self,
        _msg: Message,
        _from: String,
        _ctx: &mut Context,
    ) -> Result<(), String> {
        Err("controller accepts local orchestration messages only".to_owned())
    }

    fn on_local_message(&mut self, msg: Message, ctx: &mut Context) -> Result<(), String> {
        match msg.tip.as_str() {
            "START" => {
                if self.started {
                    self.fail("controller received START more than once", None, ctx);
                    return Ok(());
                }
                self.started = true;
                self.trace("STARTED", None, None, None, None);
                self.emit_next_action(ctx);
            }
            "BARRIER_OUTCOME" => {
                if !self.started || self.failed {
                    self.fail(
                        "barrier outcome arrived outside an active controller run",
                        None,
                        ctx,
                    );
                    return Ok(());
                }
                let outcome: BarrierOutcome = serde_json::from_str(&msg.data)
                    .map_err(|error| format!("invalid barrier outcome: {error}"))?;
                let Some((action_id, required_barrier)) = self.awaiting.clone() else {
                    self.fail(
                        "barrier outcome arrived while no action was awaiting a barrier",
                        Some(&outcome),
                        ctx,
                    );
                    return Ok(());
                };
                if outcome.barrier != required_barrier {
                    self.fail(
                        &format!(
                            "barrier mismatch for {action_id}: expected {required_barrier}, got {}",
                            outcome.barrier
                        ),
                        Some(&outcome),
                        ctx,
                    );
                    return Ok(());
                }
                if outcome.outcome != "SATISFIED" {
                    self.fail(
                        &format!("barrier {required_barrier} was not satisfied"),
                        Some(&outcome),
                        ctx,
                    );
                    return Ok(());
                }
                if outcome.control_plane_evidence_refs.is_empty() {
                    self.fail(
                        &format!(
                            "barrier {required_barrier} has no control-plane evidence reference"
                        ),
                        Some(&outcome),
                        ctx,
                    );
                    return Ok(());
                }
                if required_barrier != "cleanup-complete"
                    && outcome.independent_effect_evidence_refs.is_empty()
                {
                    self.fail(
                        &format!("barrier {required_barrier} has no independent-effect evidence reference"),
                        Some(&outcome),
                        ctx,
                    );
                    return Ok(());
                }
                if outcome.observed_facts.is_null() {
                    self.fail(
                        &format!("barrier {required_barrier} has no observed facts"),
                        Some(&outcome),
                        ctx,
                    );
                    return Ok(());
                }
                self.logical_tick += 1;
                self.trace(
                    "BARRIER_SATISFIED",
                    Some(action_id),
                    Some(outcome.barrier),
                    Some(outcome.outcome),
                    None,
                );
                self.awaiting = None;
                self.next_action += 1;
                self.emit_next_action(ctx);
            }
            other => {
                self.fail(&format!("undeclared controller command {other}"), None, ctx);
            }
        }
        Ok(())
    }

    fn on_timer(&mut self, timer: String, ctx: &mut Context) -> Result<(), String> {
        self.fail(
            &format!("ambient timer {timer} cannot advance a barrier-gated controller"),
            None,
            ctx,
        );
        Ok(())
    }
}

fn parse_args() -> Result<(PathBuf, u64, Value), String> {
    let mut scenario: Option<PathBuf> = None;
    let mut seed: Option<u64> = None;
    let mut parameters = json!({});
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--scenario" => {
                scenario = Some(PathBuf::from(
                    args.next().ok_or("--scenario requires a path")?,
                ));
            }
            "--seed" => {
                seed = Some(
                    args.next()
                        .ok_or("--seed requires an unsigned integer")?
                        .parse::<u64>()
                        .map_err(|error| format!("invalid --seed: {error}"))?,
                );
            }
            "--parameters" => {
                parameters = serde_json::from_str(
                    &args.next().ok_or("--parameters requires a JSON object")?,
                )
                .map_err(|error| format!("invalid --parameters JSON: {error}"))?;
                if !parameters.is_object() {
                    return Err("--parameters must be a JSON object".to_owned());
                }
            }
            _ => return Err(format!("unknown argument {arg}")),
        }
    }
    Ok((
        scenario.ok_or("--scenario is required")?,
        seed.ok_or("--seed is required")?,
        parameters,
    ))
}

fn read_controller_output(system: &mut System) -> Result<Value, String> {
    let messages = system
        .step_until_local_message("controller")
        .map_err(str::to_owned)?;
    if messages.len() != 1 {
        return Err(format!(
            "controller emitted {} messages; exactly one is required",
            messages.len()
        ));
    }
    serde_json::from_str(&messages[0].data)
        .map_err(|error| format!("controller emitted invalid JSON: {error}"))
}

fn write_output(output: &Value) -> Result<(), String> {
    let stdout = io::stdout();
    let mut handle = stdout.lock();
    serde_json::to_writer(&mut handle, output)
        .map_err(|error| format!("failed to encode controller output: {error}"))?;
    handle
        .write_all(b"\n")
        .and_then(|_| handle.flush())
        .map_err(|error| format!("failed to write controller output: {error}"))
}

fn run() -> Result<(), String> {
    let (scenario_path, seed, parameters) = parse_args()?;
    let raw = fs::read_to_string(&scenario_path)
        .map_err(|error| format!("failed to read {}: {error}", scenario_path.display()))?;
    let scenario: Scenario = serde_json::from_str(&raw)
        .map_err(|error| format!("invalid scenario JSON {}: {error}", scenario_path.display()))?;
    const REVISION: &str = "74613a368c73fb12f25778ce33ca11c9a833da96";
    if scenario.AnySystem_revision != REVISION {
        return Err(format!(
            "scenario AnySystem revision {} differs from compiled pin {REVISION}",
            scenario.AnySystem_revision
        ));
    }

    let mut system = System::new(seed);
    system.add_node("controller-node");
    system.add_process(
        "controller",
        Box::new(Controller::new(scenario, seed, parameters)),
        "controller-node",
    );
    system.send_local_message("controller", Message::new("START", "{}"));
    let mut output = read_controller_output(&mut system)?;
    write_output(&output)?;

    let stdin = io::stdin();
    let mut lines = stdin.lock().lines();
    loop {
        let output_type = output
            .get("type")
            .and_then(Value::as_str)
            .ok_or("controller output has no type")?;
        if output_type == "COMPLETE" {
            return Ok(());
        }
        if output_type == "FAILED" {
            return Err("controller entered FAILED state".to_owned());
        }
        let line = lines
            .next()
            .ok_or("orchestrator input ended before controller completion")?
            .map_err(|error| format!("failed to read orchestrator input: {error}"))?;
        let outcome: BarrierOutcome = serde_json::from_str(&line)
            .map_err(|error| format!("invalid orchestrator input: {error}"))?;
        system.send_local_message(
            "controller",
            Message::new(
                "BARRIER_OUTCOME".to_owned(),
                serde_json::to_string(&outcome)
                    .map_err(|error| format!("failed to encode barrier outcome: {error}"))?,
            ),
        );
        output = read_controller_output(&mut system)?;
        write_output(&output)?;
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("deterministic controller failed closed: {error}");
        std::process::exit(1);
    }
}
