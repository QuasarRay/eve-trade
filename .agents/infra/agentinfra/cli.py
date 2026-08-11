from __future__ import annotations
import argparse, hashlib, json, platform, sys, tomllib
from pathlib import Path
from .audit import audit
from .bootstrap import verify_installed as bootstrap_verify
from .codex_config import verify_static as codex_verify, load_role_specs
from .context_cache import ContextLedger
from .controls import validate_gate_waiver
from .discovery import discover_repository, write_discovery_artifact
from .evidence import _append_verified_observation, append_evidence, execute_command_evidence, load_evidence, rollback_last_evidence, verify_evidence
from .falsification_runtime import complete as falsification_complete, run_attempt as falsification_run
from .laws import LawRunner
from .locks import FileLock, LeaseLock, LockError
from .manifest import verify as verify_manifest
from .modules import discover, detected, optional_python_packages, run_action
from .paths import find_root, framework_dir, tasks_dir, leases_dir
from .policy import compile_contract, load_project_contract, write_compiled_policy
from .precheck import build_precheck
from .runtime_migration import migrate_runtime
from .release_source import build_deployment_tree, verify_deployment_tree
from .review import review_handoff_digest, review_payload_digest, validate_review_payload
from .review_runtime import complete as review_complete
from .security import confined_path
from .shell_select import choose as choose_shell, available as available_shells
from .state_store import StateStore, now
from .tdd_runtime import abort as tdd_abort, baseline as tdd_baseline, design as tdd_design, green as tdd_green, status as tdd_status
from .workspace import workspace_fingerprint

def dump(obj): print(json.dumps(obj,indent=2,sort_keys=True,default=str))
def task_dir(root,tid):return tasks_dir(root)/tid

def cmd_doctor(root,args):
    mods=discover(root)
    dump({"framework_root":str(root),"python":sys.version.split()[0],"platform":platform.platform(),
          "reasoning_default":"max","available_shells":available_shells(),"optional_python":optional_python_packages(),
          "modules":{k:{"detected":detected(v),"kind":v['manifest']['module']['kind']} for k,v in mods.items()}})
    return 0

def cmd_task(root,args):
    s=StateStore(root)
    if args.action=="new":dump(s.create(args.title,args.mode,args.complexity,args.risk));return 0
    if args.action=="list":dump(s.list_tasks());return 0
    tid=args.task_id or None
    if args.action=="status":dump(s.load(tid));return 0
    if args.action=="transition":dump(s.transition(args.state,args.reason,tid));return 0
    if args.action=="gate-add":
        if not args.description.strip():raise RuntimeError("gate description must not be empty")
        def f(t):
            gid=args.id or f"G{len(t['gates'])+1}"
            if any(g["id"]==gid for g in t["gates"]):raise RuntimeError("duplicate gate id")
            risk_severity={"HARD":"critical","REQUIRED":"high","ADVISORY":"low"}[args.severity]
            t["gates"].append({"id":gid,"description":args.description.strip(),"severity":risk_severity,"gate_severity":args.severity,"status":"OPEN","evidence":[],"created_revision":t["revision"]+1});t["verification_evidence"]=[];t["verification_epoch"]=None;t["final_audit_complete"]=False
        dump(s.mutate(f,tid));return 0
    if args.action=="gate-prove":
        def f(t):
            g=next((x for x in t["gates"] if x["id"]==args.id),None)
            if not g:raise RuntimeError("unknown gate")
            records={e["id"]:e for e in load_evidence(task_dir(root,t["id"]))}
            rec=records.get(args.evidence)
            if rec is None:raise RuntimeError("unknown evidence id")
            if rec.get("schema")!=2 or rec.get("task_id")!=t["id"] or rec.get("provenance") not in {"framework-command","verified-observation","external-source"}:
                raise RuntimeError("acceptance-gate proof requires task-bound non-manual evidence")
            if args.id not in rec.get("details",{}).get("gate_ids",[]):raise RuntimeError("acceptance-gate evidence lacks explicit relevance binding")
            if int(rec.get("details",{}).get("task_revision",-1)) < int(g.get("created_revision",0)):raise RuntimeError("acceptance-gate evidence predates gate definition")
            if t.get("mode")=="write" and rec.get("details",{}).get("change_epoch") != int(t.get("change_epoch",0)):
                raise RuntimeError("acceptance-gate evidence is stale for the current implementation epoch")
            command=rec.get("details",{}).get("command")
            if rec.get("provenance")=="framework-command" and (not isinstance(command,dict) or command.get("success") is not True):
                raise RuntimeError("failed command evidence cannot prove a gate")
            g["status"]="PROVEN"
            if args.evidence not in g["evidence"]:g["evidence"].append(args.evidence)
            t["final_audit_complete"]=False
        dump(s.mutate(f,tid));return 0
    if args.action=="gate-waive":
        def f(t):
            g=next((x for x in t["gates"] if x["id"]==args.id),None)
            if not g:raise RuntimeError("unknown gate")
            if not args.reason.strip():raise RuntimeError("waiver requires reason")
            records={record["id"]:record for record in load_evidence(task_dir(root,t["id"]))}
            candidate={**g,"status":"WAIVED","waiver_reason":args.reason.strip(),"waiver_evidence":args.evidence}
            if not validate_gate_waiver(candidate,records,task_id=t["id"],current_epoch=int(t.get("change_epoch",0))):
                raise RuntimeError("gate waiver requires current task-bound external user/host evidence; HARD gates cannot be waived")
            g.update(candidate);t["verification_evidence"]=[];t["verification_epoch"]=None;t["final_audit_complete"]=False
        dump(s.mutate(f,tid));return 0
    if args.action=="risk-add":
        if not args.description.strip():raise RuntimeError("risk description must not be empty")
        def f(t):
            rid=args.id or f"R{len(t['risks'])+1}"
            if any(r["id"]==rid for r in t["risks"]):raise RuntimeError("duplicate risk id")
            t["risks"].append({"id":rid,"description":args.description.strip(),"severity":args.severity,"status":"open","mitigation":args.mitigation.strip()});t["final_audit_complete"]=False
        dump(s.mutate(f,tid));return 0
    if args.action=="risk-resolve":
        if not args.resolution.strip():raise RuntimeError("risk resolution must not be empty")
        def f(t):
            r=next((x for x in t["risks"] if x["id"]==args.id),None)
            if not r:raise RuntimeError("unknown risk")
            r["status"]="resolved";r["resolution"]=args.resolution;t["final_audit_complete"]=False
        dump(s.mutate(f,tid));return 0
    if args.action=="decision-add":
        if not args.statement.strip():raise RuntimeError("decision statement must not be empty")
        if not args.rationale.strip():raise RuntimeError("decision rationale must not be empty")
        def f(t):
            did=args.id or f"D{len(t.setdefault('decisions',[]))+1}"
            if any(d["id"]==did for d in t["decisions"]):raise RuntimeError("duplicate decision id")
            evidence=[]
            if args.evidence:
                records={e["id"] for e in load_evidence(task_dir(root,t["id"]))}
                missing=[eid for eid in args.evidence if eid not in records]
                if missing:raise RuntimeError("unknown decision evidence: "+", ".join(missing))
                evidence=list(dict.fromkeys(args.evidence))
            t["decisions"].append({"id":did,"at":now(),"statement":args.statement.strip(),"rationale":args.rationale.strip(),"evidence":evidence})
            t["final_audit_complete"]=False
        dump(s.mutate(f,tid));return 0
    if args.action=="audit-complete":
        dump(s.audit_complete(tid));return 0
    raise RuntimeError("unknown task action")

def cmd_evidence(root,args):
    s=StateStore(root);tid=args.task_id or s.current_id();td=task_dir(root,tid)
    if args.action=="list":s.load(tid);dump(load_evidence(td));return 0
    if args.action=="verify":
        s.load(tid)
        ok,msg=verify_evidence(td);dump({"ok":ok,"detail":msg});return 0 if ok else 1
    if args.action=="add":
        if args.verification and not args.argv:
            raise RuntimeError("verification evidence must execute a command using --argv")
        attached={}
        def f(x):
            if args.verification and x.get("state")!="VERIFY":
                raise RuntimeError("verification evidence may be attached only while task state is VERIFY")
            if args.argv:
                rec,result=execute_command_evidence(td,root=root,argv=args.argv,summary=args.summary,
                    change_epoch=x.get("change_epoch",0),task_revision=x.get("revision"),gate_ids=args.gate_id or (),timeout=args.timeout,expected_exit=args.expected_exit,lock_held=True)
                attached["result"]=result
                if args.verification and rec.get("details",{}).get("command",{}).get("success") is not True:
                    attached["record"]=rec
                    raise RuntimeError("verification command did not succeed in a stable workspace")
            else:
                rec=append_evidence(td,args.kind,args.summary,provenance="manual",result=args.result,path=args.path,
                                    task_revision=x.get("revision"),change_epoch=x.get("change_epoch",0),task_state=x.get("state"),lock_held=True)
            attached["record"]=rec
            x["evidence_head"] = rec["record_sha256"]
            x["final_audit_complete"] = False
            x.pop("final_audit_workspace",None)
            if args.verification:
                if x.get("state")!="VERIFY":raise RuntimeError("task left VERIFY before evidence attachment")
                if rec["id"] not in x.setdefault("verification_evidence",[]):x["verification_evidence"].append(rec["id"])
                x["verification_epoch"] = int(x.get("change_epoch", 0))
        def rollback():
            rec=attached.get("record")
            if rec is not None:
                rollback_last_evidence(td,rec["record_sha256"],lock_held=True)
        s.mutate(f,tid,hold_evidence_lock=True,on_failure=rollback)
        rec=attached["record"]
        dump(rec);return 0

def cmd_tdd(root,args):
    if args.action=="design":
        dump(tdd_design(root,adapter_kind=args.adapter,test_id=args.test_id,test_paths=args.test_path,oracle_paths=args.oracle_path or [],task_id=args.task_id or None));return 0
    if args.action=="baseline":
        report,accepted=tdd_baseline(root,semantic_reason=args.semantic_reason,timeout=args.timeout,task_id=args.task_id or None);dump(report);return 0 if accepted else 1
    if args.action=="green":
        report,accepted=tdd_green(root,timeout=args.timeout,task_id=args.task_id or None);dump(report);return 0 if accepted else 1
    if args.action=="status":dump(tdd_status(root,task_id=args.task_id or None));return 0
    if args.action=="abort":dump(tdd_abort(root,reason=args.reason,task_id=args.task_id or None));return 0
    raise RuntimeError("unknown TDD action")

def cmd_falsify(root,args):
    if args.action=="run":dump(falsification_run(root,family=args.family,adapter_kind=args.adapter,test_id=args.test_id,interpretation=args.interpretation,timeout=args.timeout,task_id=args.task_id or None));return 0
    if args.action=="complete":dump(falsification_complete(root,task_id=args.task_id or None));return 0
    raise RuntimeError("unknown falsification action")

def cmd_context(root,args):
    c=ContextLedger(root)
    if args.action in {"record","check"}:
        p=(root/args.path).resolve() if not Path(args.path).is_absolute() else Path(args.path)
        if args.action=="record":dump(c.record_file(p,args.conclusion));return 0
        dump(c.check_file(p));return 0
    if args.action=="external-record":dump(c.record_external(args.source,args.fingerprint,args.conclusion,args.ttl_seconds));return 0
    if args.action=="external-check":dump(c.check_external(args.source,args.fingerprint));return 0

def child_lease(root):return LeaseLock(leases_dir(root)/"subagent-lease.json","single-active-subagent")
def child_control(root):return FileLock(leases_dir(root)/"subagent-control.lock","subagent-control")

def _read_review_payload(root,value):
    path=confined_path(root,value,must_exist=True,reject_symlinks=True)
    if not path.is_file():raise RuntimeError("review handoff path must be a regular file")
    before=path.stat();raw=path.read_bytes();after=path.stat()
    if len(raw)>65_536:raise RuntimeError("review handoff exceeds 65536-byte bound")
    stable=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
    if not stable or len(raw)!=after.st_size:raise RuntimeError("review handoff changed while being read")
    try:payload=json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise RuntimeError(f"invalid review handoff JSON: {exc}") from exc
    return validate_review_payload(payload)

def cmd_subagent(root,args):
    lease=child_lease(root);s=StateStore(root)
    if args.action=="status":dump(lease.inspect());return 0
    if args.action=="open":
        t=s.load(args.task_id or None)
        if t.get("state") in {"FINAL_AUDIT","FINALIZE","FAILED","CANCELLED","ABANDONED"}:raise RuntimeError("cannot open a subagent during/after final audit or for a terminal task")
        roles={"reviewer","worker","researcher","explorer","architect","implementer","verifier","test-engineer","law-analyst","adversarial-reviewer","security-reviewer","performance-reviewer","migration-analyst","diagnostician"}
        if (framework_dir(root)/"modules"/"codex"/"config"/"agents.toml").is_file():roles.update(load_role_specs(root))
        if args.role not in roles:raise RuntimeError("unknown subagent role")
        requested=list(dict.fromkeys(args.context_evidence or []))
        known={record["id"]:record for record in load_evidence(task_dir(root,t["id"]))}
        missing=[evidence_id for evidence_id in requested if evidence_id not in known]
        if missing:raise RuntimeError("unknown child-context evidence: "+", ".join(missing))
        context_records=[]
        for evidence_id in requested:
            record=known[evidence_id]
            if record.get("task_id")!=t["id"] or record.get("change_epoch")!=t["change_epoch"]:
                raise RuntimeError("child context evidence is not bound to current task epoch")
            if record.get("provenance") not in {"framework-command","verified-observation","external-source"}:
                raise RuntimeError("child context accepts only directly verified parent facts")
            context_records.append({key:record.get(key) for key in ("id","kind","summary","record_sha256")})
        context_brief={"schema":1,"task_id":t["id"],"change_epoch":t["change_epoch"],"evidence":context_records}
        context_bytes=json.dumps(context_brief,sort_keys=True,separators=(",",":")).encode("utf-8")
        if len(context_bytes)>16_384:raise RuntimeError("child context brief exceeds 16384-byte bound")
        context_digest=hashlib.sha256(context_bytes).hexdigest()
        control=child_control(root);control.acquire()
        try:
            info=lease.acquire(task_id=t["id"],role=args.role,parent_id=t["id"])
            def f(x):
                if x.get("state") in {"FINAL_AUDIT","FINALIZE","FAILED","CANCELLED","ABANDONED"}:raise RuntimeError("task entered final audit or terminal state before child open")
                if x.get("active_child"):raise RuntimeError("task already records active child")
                x["active_child"]={"role":args.role,"opened":now(),"lease_id":info["lease_id"],"owner_nonce":info["owner_nonce"],"context_brief":context_brief,"context_brief_sha256":context_digest};x["final_audit_complete"]=False
            updated=s.mutate(f,t["id"]);dump({"task":updated,"lease":info});return 0
        except BaseException:
            if 'info' in locals():lease.release(info["lease_id"],owner_nonce=info["owner_nonce"],task_id=t["id"],role=args.role)
            raise
        finally:control.release()
    if args.action=="close":
        t=s.load(args.task_id or None);child=t.get("active_child")
        if not child:raise RuntimeError("task has no active child")
        info=lease.inspect()
        if not info.get("exists") or info.get("lease_id")!=child.get("lease_id") or info.get("task_id")!=t["id"]:
            raise RuntimeError("task/global subagent lease mismatch; recover explicitly")
        if args.lease_id and args.lease_id!=child["lease_id"]:raise RuntimeError("provided lease id mismatch")
        if not args.summary.strip():raise RuntimeError("child handoff summary must not be empty")
        evidence=list(dict.fromkeys(args.evidence or []))
        if evidence:
            known={e["id"] for e in load_evidence(task_dir(root,t["id"]))}
            missing=[eid for eid in evidence if eid not in known]
            if missing:raise RuntimeError("unknown child-handoff evidence: "+", ".join(missing))
        review_payload=_read_review_payload(root,args.review_file) if args.review_file else None
        if review_payload is not None and review_payload["outcome"].casefold()!=args.outcome:
            raise RuntimeError("structured review outcome conflicts with child handoff outcome")
        control=child_control(root);control.acquire()
        try:
            lease.release(child["lease_id"],owner_nonce=child.get("owner_nonce"),task_id=t["id"],role=child["role"])
            try:
                def f(x):
                    handoff={"schema":2 if review_payload is not None else 1,"role":child["role"],"lease_id":child["lease_id"],"opened":child["opened"],"closed":now(),"outcome":args.outcome,"summary":args.summary.strip(),"evidence":evidence,"context_brief":child.get("context_brief"),"context_brief_sha256":child.get("context_brief_sha256"),"review_payload":review_payload,"review_payload_sha256":review_payload_digest(review_payload) if review_payload is not None else None}
                    handoff["handoff_sha256"]=review_handoff_digest(handoff)
                    x.setdefault("child_history",[]).append(handoff)
                    x["active_child"]=None;x["final_audit_complete"]=False
                updated=s.mutate(f,t["id"],allow_active_child=True)
            except BaseException:
                lease.restore(info);raise
        finally:control.release()
        dump(updated);return 0
    if args.action=="recover":
        if not args.force:raise RuntimeError("subagent recovery is destructive workflow repair; pass --force")
        info=lease.inspect();target=args.task_id or (info.get("task_id") if info.get("exists") else None)
        if not target:raise RuntimeError("no lease task exists to recover")
        if info.get("exists") and info.get("task_id")!=target:raise RuntimeError("recovery target does not match global lease task")
        t=s.load(target);child=t.get("active_child")
        if not child or child.get("lease_id")!=info.get("lease_id"):raise RuntimeError("task/global subagent lease mismatch")
        control=child_control(root);control.acquire()
        try:
            cleared=lease.force_clear(reason=args.reason,expected_task_id=target)
            try:
                holder={}
                def f(x):
                    rec=_append_verified_observation(task_dir(root,target),"recovery","Subagent lease explicitly recovered",task_id=target,lock_held=True,change_epoch=x["change_epoch"],task_revision=x["revision"],reason=args.reason,lease_id=child["lease_id"])
                    holder["record"]=rec
                    x.setdefault("child_history",[]).append({"role":child["role"],"lease_id":child["lease_id"],"opened":child["opened"],"closed":now(),"outcome":"recovered","summary":args.reason.strip(),"evidence":[rec["id"]]})
                    x["active_child"]=None;x["evidence_head"]=rec["record_sha256"]
                def undo():
                    if holder.get("record"):rollback_last_evidence(task_dir(root,target),holder["record"]["record_sha256"],lock_held=True)
                updated=s.mutate(f,target,allow_active_child=True,hold_evidence_lock=True,on_failure=undo)
                rec=holder["record"]
            except BaseException:
                lease.restore(cleared);raise
        finally:control.release()
        dump({"cleared":cleared,"task":updated,"evidence":rec});return 0

def cmd_review(root,args):
    if args.action=="complete":dump(review_complete(root,lease_id=args.lease_id,task_id=args.task_id or None));return 0
    raise RuntimeError("unknown review action")

def law_files(root,args):
    if args.files:return [Path(x).resolve() if Path(x).is_absolute() else root/x for x in args.files]
    framework=framework_dir(root)
    fs=[framework/"infra"/"laws"/"framework.toml"]
    proj=framework/"laws"/"project"
    if proj.exists():fs+=sorted(proj.glob("*.toml"))
    return fs

def cmd_law(root,args):
    files=law_files(root,args);res=LawRunner(root).run(files)
    for r in res:print(f"{'PASS' if r.passed else 'FAIL'} {r.id}: {r.detail}")
    hardfail=any(not r.passed and r.severity=="hard" for r in res)
    print(f"summary: {sum(r.passed for r in res)}/{len(res)} passed")
    return 1 if hardfail else 0

def cmd_module(root,args):
    mods=discover(root)
    if args.action=="list":dump({k:{"path":str(v["path"]),"detected":detected(v),"manifest":v["manifest"]["module"]} for k,v in mods.items()});return 0
    if args.id not in mods:raise RuntimeError("unknown module")
    info=mods[args.id]
    if args.action=="show":dump(info["manifest"]);return 0
    if args.action=="verify":
        result=run_action(root,info,args.action,apply=False);dump(result);return 0 if result["exit"]==0 else 1
    raise RuntimeError("unknown module action")

def cmd_manifest(root,args):
    ok,detail=verify_manifest(root);dump({"ok":ok,"detail":detail});return 0 if ok else 1

def cmd_policy(root,args):
    contract_path=Path(args.contract);contract_path=contract_path if contract_path.is_absolute() else root/contract_path
    contract=load_project_contract(contract_path)
    if args.action=="validate":dump({"ok":True,"contract":str(contract_path)});return 0
    compiled=compile_contract(contract,declared_classes=args.classes or (),changed_paths=args.changed_paths or (),risk=args.risk)
    if args.action=="compile":
        path=write_compiled_policy(root,compiled);dump({"ok":True,"path":str(path),"compiled":compiled});return 0
    dump({"ok":True,"classes":compiled["task"]["classes"],"tdd_mode":compiled["task"]["tdd_mode"],"policy_packs":compiled["policy_packs"],"gates":compiled["gates"],"scope":compiled["scope"],"digest":compiled["digest"]});return 0

def cmd_precheck(root,args):
    if not args.test_contract_digest or not args.oracle_digest:
        raise RuntimeError("precheck build requires --test-contract-digest and --oracle-digest")
    contract_path=Path(args.contract);contract_path=contract_path if contract_path.is_absolute() else root/contract_path
    contract=load_project_contract(contract_path)
    dump(build_precheck(root,project_contract=contract,declared_classes=args.classes or (),changed_paths=args.changed_paths or (),risk=args.risk,test_contract_digest=args.test_contract_digest,oracle_digest=args.oracle_digest));return 0

def build_parser():
    p=argparse.ArgumentParser(prog="agentctl");p.add_argument("--root");p.add_argument("--json",action="store_true");sp=p.add_subparsers(dest="cmd",required=True)
    sp.add_parser("doctor")
    q=sp.add_parser("shell");qs=q.add_subparsers(dest="action",required=True);c=qs.add_parser("choose");c.add_argument("--purpose",default="interactive")
    q=sp.add_parser("task");qs=q.add_subparsers(dest="action",required=True)
    n=qs.add_parser("new");n.add_argument("--title",required=True);n.add_argument("--mode",choices=["read","write"],default="write");n.add_argument("--complexity",choices=["S","M","L","XL"],default="M");n.add_argument("--risk",choices=["low","medium","high","critical"],default="medium")
    qs.add_parser("list")
    for a in ["status","audit-complete"]:x=qs.add_parser(a);x.add_argument("--task-id")
    x=qs.add_parser("transition");x.add_argument("state");x.add_argument("--reason",required=True);x.add_argument("--task-id")
    x=qs.add_parser("gate-add");x.add_argument("description");x.add_argument("--id");x.add_argument("--severity",choices=["HARD","REQUIRED","ADVISORY"],default="HARD");x.add_argument("--task-id")
    x=qs.add_parser("gate-prove");x.add_argument("id");x.add_argument("--evidence",required=True);x.add_argument("--task-id")
    x=qs.add_parser("gate-waive");x.add_argument("id");x.add_argument("--reason",required=True);x.add_argument("--evidence",required=True);x.add_argument("--task-id")
    x=qs.add_parser("risk-add");x.add_argument("description");x.add_argument("--id");x.add_argument("--severity",choices=["low","medium","high","critical"],required=True);x.add_argument("--mitigation",default="");x.add_argument("--task-id")
    x=qs.add_parser("risk-resolve");x.add_argument("id");x.add_argument("--resolution",required=True);x.add_argument("--task-id")
    x=qs.add_parser("decision-add");x.add_argument("statement");x.add_argument("--id");x.add_argument("--rationale",required=True);x.add_argument("--evidence",action="append");x.add_argument("--task-id")
    q=sp.add_parser("evidence");qs=q.add_subparsers(dest="action",required=True)
    for a in ["list","verify"]:x=qs.add_parser(a);x.add_argument("--task-id")
    x=qs.add_parser("add");x.add_argument("--kind",required=True);x.add_argument("--summary",required=True);x.add_argument("--result");x.add_argument("--path");x.add_argument("--verification",action="store_true");x.add_argument("--gate-id",action="append");x.add_argument("--timeout",type=float,default=60.0);x.add_argument("--expected-exit",type=int,default=0);x.add_argument("--task-id");x.add_argument("--argv",nargs=argparse.REMAINDER)
    q=sp.add_parser("tdd");qs=q.add_subparsers(dest="action",required=True)
    x=qs.add_parser("design");x.add_argument("--adapter",choices=["unittest"],required=True);x.add_argument("--test-id",required=True);x.add_argument("--test-path",action="append",required=True);x.add_argument("--oracle-path",action="append");x.add_argument("--task-id")
    x=qs.add_parser("baseline");x.add_argument("--semantic-reason",required=True);x.add_argument("--timeout",type=float,default=60.0);x.add_argument("--task-id")
    x=qs.add_parser("green");x.add_argument("--timeout",type=float,default=60.0);x.add_argument("--task-id")
    for action in ("status",):x=qs.add_parser(action);x.add_argument("--task-id")
    x=qs.add_parser("abort");x.add_argument("--reason",required=True);x.add_argument("--task-id")
    q=sp.add_parser("falsify");qs=q.add_subparsers(dest="action",required=True)
    x=qs.add_parser("run");x.add_argument("--family",required=True);x.add_argument("--adapter",choices=["unittest"],required=True);x.add_argument("--test-id",required=True);x.add_argument("--interpretation",required=True);x.add_argument("--timeout",type=float,default=60.0);x.add_argument("--task-id")
    x=qs.add_parser("complete");x.add_argument("--task-id")
    q=sp.add_parser("context");qs=q.add_subparsers(dest="action",required=True)
    for a in ["record","check"]:
        x=qs.add_parser(a);x.add_argument("path")
        if a=="record":x.add_argument("--conclusion",default="")
    x=qs.add_parser("external-record");x.add_argument("source");x.add_argument("--fingerprint",required=True);x.add_argument("--conclusion",default="");x.add_argument("--ttl-seconds",type=int)
    x=qs.add_parser("external-check");x.add_argument("source");x.add_argument("--fingerprint")
    q=sp.add_parser("subagent");qs=q.add_subparsers(dest="action",required=True)
    qs.add_parser("status")
    x=qs.add_parser("open");x.add_argument("--role",required=True);x.add_argument("--context-evidence",action="append");x.add_argument("--task-id")
    x=qs.add_parser("close");x.add_argument("--lease-id");x.add_argument("--summary",required=True);x.add_argument("--outcome",choices=["accepted","rejected","partial"],required=True);x.add_argument("--evidence",action="append");x.add_argument("--review-file");x.add_argument("--task-id")
    x=qs.add_parser("recover");x.add_argument("--task-id");x.add_argument("--reason",required=True);x.add_argument("--force",action="store_true")
    q=sp.add_parser("review");qs=q.add_subparsers(dest="action",required=True);x=qs.add_parser("complete");x.add_argument("--lease-id",required=True);x.add_argument("--task-id")
    q=sp.add_parser("law");qs=q.add_subparsers(dest="action",required=True);x=qs.add_parser("run");x.add_argument("files",nargs="*")
    q=sp.add_parser("module");qs=q.add_subparsers(dest="action",required=True);qs.add_parser("list");x=qs.add_parser("show");x.add_argument("id")
    x=qs.add_parser("verify");x.add_argument("id")
    q=sp.add_parser("bootstrap");qs=q.add_subparsers(dest="action",required=True);qs.add_parser("verify")
    q=sp.add_parser("manifest");qs=q.add_subparsers(dest="action",required=True);qs.add_parser("verify")
    sp.add_parser("audit")
    sp.add_parser("codex-verify")
    sp.add_parser("discover")
    q=sp.add_parser("policy");qs=q.add_subparsers(dest="action",required=True)
    for action in ("validate","compile","explain"):
        x=qs.add_parser(action);x.add_argument("contract");x.add_argument("--class",dest="classes",action="append");x.add_argument("--changed-path",dest="changed_paths",action="append");x.add_argument("--risk",choices=["low","medium","high","critical"],default="medium")
    q=sp.add_parser("precheck");qs=q.add_subparsers(dest="action",required=True);x=qs.add_parser("build");x.add_argument("contract");x.add_argument("--class",dest="classes",action="append");x.add_argument("--changed-path",dest="changed_paths",action="append");x.add_argument("--risk",choices=["low","medium","high","critical"],default="medium");x.add_argument("--test-contract-digest");x.add_argument("--oracle-digest")
    q=sp.add_parser("runtime");qs=q.add_subparsers(dest="action",required=True);x=qs.add_parser("migrate");x.add_argument("--apply",action="store_true")
    q=sp.add_parser("package");qs=q.add_subparsers(dest="action",required=True);x=qs.add_parser("build");x.add_argument("destination");x=qs.add_parser("verify");x.add_argument("destination")
    return p

def main(argv=None):
    args=build_parser().parse_args(argv)
    try:
        root=Path(args.root).resolve(strict=True) if args.root else find_root()
        marker=framework_dir(root)/"framework.toml"
        if not marker.is_file():raise RuntimeError("selected root is not an initialized Aegis project")
        try:
            with marker.open("rb") as stream: framework_marker=tomllib.load(stream)
        except (OSError,tomllib.TOMLDecodeError) as exc:
            raise RuntimeError(f"selected root has an invalid Aegis framework marker: {exc}") from exc
        framework_table=framework_marker.get("framework")
        if not isinstance(framework_table,dict) or not isinstance(framework_table.get("version"),str) or not framework_table["version"].strip():
            raise RuntimeError("selected root has an invalid Aegis framework marker contract")
        if args.cmd=="doctor":return cmd_doctor(root,args)
        if args.cmd=="shell":dump(choose_shell(args.purpose));return 0
        if args.cmd=="task":return cmd_task(root,args)
        if args.cmd=="evidence":return cmd_evidence(root,args)
        if args.cmd=="tdd":return cmd_tdd(root,args)
        if args.cmd=="falsify":return cmd_falsify(root,args)
        if args.cmd=="context":return cmd_context(root,args)
        if args.cmd=="subagent":return cmd_subagent(root,args)
        if args.cmd=="review":return cmd_review(root,args)
        if args.cmd=="law":return cmd_law(root,args)
        if args.cmd=="module":return cmd_module(root,args)
        if args.cmd=="bootstrap":
            ok,detail=bootstrap_verify(root);dump({"ok":ok,"detail":detail});return 0 if ok else 1
        if args.cmd=="manifest":return cmd_manifest(root,args)
        if args.cmd=="audit":
            issues=audit(root);dump({"ok":not issues,"issues":issues});return 0 if not issues else 1
        if args.cmd=="codex-verify":ok,d=codex_verify(root);dump({"ok":ok,**d});return 0 if ok else 1
        if args.cmd=="discover":artifact=discover_repository(root);path=write_discovery_artifact(root,artifact);dump({"artifact":artifact,"path":str(path)});return 0
        if args.cmd=="policy":return cmd_policy(root,args)
        if args.cmd=="precheck":return cmd_precheck(root,args)
        if args.cmd=="runtime":dump(migrate_runtime(root,apply=args.apply));return 0
        if args.cmd=="package":
            destination=Path(args.destination);destination=destination if destination.is_absolute() else root/destination
            if args.action=="build":dump(build_deployment_tree(root,destination));return 0
            report=verify_deployment_tree(destination);dump(report);return 0 if report.get("ok") else 1
    except KeyboardInterrupt:
        print(json.dumps({"ok":False,"error":"interrupted","code":"INTERRUPTED"}) if getattr(args,"json",False) else "error: interrupted",file=sys.stderr);return 130
    except (RuntimeError,OSError,FileNotFoundError,LockError,ValueError,json.JSONDecodeError) as e:
        if getattr(args,"json",False):print(json.dumps({"ok":False,"error":str(e),"code":type(e).__name__},sort_keys=True),file=sys.stderr)
        else:print(f"error: {e}",file=sys.stderr)
        return 2
    return 2

if __name__=="__main__":raise SystemExit(main())
