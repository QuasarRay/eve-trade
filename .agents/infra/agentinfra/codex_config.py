from __future__ import annotations
import codecs, hashlib, json, os, re, shutil, tomllib, uuid
from pathlib import Path
from .atomic import atomic_write_bytes
from .locks import FileLock
from .paths import framework_dir, install_state_dir, persistent_dir, runtime_dir
from .process import run_process
from .security import confined_path
from .transaction import FileTransaction, Mutation, recover_named_transactions

TOP={"model":"gpt-5.6-sol","model_reasoning_effort":"max"}
AGENTS={
    "enabled":True,
    "max_concurrent_threads_per_session":1,
    "max_depth":1,
    "default_subagent_model":"gpt-5.6-sol",
    "default_subagent_reasoning_effort":"max",
    "interrupt_message":True,
}
# MultiAgentV2 counts the root thread in its session cap.  Two total slots therefore means
# root + exactly one child.  Do not force-enable V2; current Codex builds can select it via
# model/runtime metadata and some releases have regressions when it is forced on explicitly.
V2={"max_concurrent_threads_per_session":2}
MARKER="# aegis-v4-managed-agent:"

class ConfigError(RuntimeError):pass

def _sha_bytes(data:bytes|None):return hashlib.sha256(data).hexdigest() if data is not None else None
def _sha_path(path:Path):return _sha_bytes(path.read_bytes()) if path.exists() else None
def _state_root(root:Path):return install_state_dir(root)/"codex"
def _journal_path(root:Path):return _state_root(root)/"install.json"
def _legacy_journal_path(root:Path):return runtime_dir(root)/"codex-install.json"
def _toml_value(v):
    if isinstance(v,bool):return "true" if v else "false"
    if isinstance(v,int):return str(v)
    return json.dumps(v)

def load_role_specs(root:Path):
    codex_root=framework_dir(root)/"modules"/"codex"
    reg=codex_root/"config"/"agents.toml"
    try:
        with reg.open("rb") as f:data=tomllib.load(f)
    except Exception as e:raise ConfigError(f"invalid Codex role registry: {e}") from e
    out={};slugs=set();reserved=set(AGENTS)|{"features","model","review_model"}
    for item in data.get("agent",[]):
        name=item.get("name");role=item.get("role");sandbox=item.get("sandbox_mode");desc=item.get("description")
        if not all(isinstance(x,str) and x for x in (name,role,sandbox,desc)):raise ConfigError("role registry entry missing required string fields")
        slug=item.get("slug",name)
        if not re.fullmatch(r"aegis_[a-z0-9_]{1,57}",name) or name in reserved:raise ConfigError(f"invalid or reserved Codex role name: {name!r}")
        if not isinstance(slug,str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}",slug):raise ConfigError(f"invalid Codex role slug: {slug!r}")
        if name in out:raise ConfigError(f"duplicate Codex role name: {name}")
        if slug in slugs:raise ConfigError(f"duplicate Codex role slug: {slug}")
        slugs.add(slug)
        if sandbox not in {"read-only","workspace-write"}:raise ConfigError(f"unsupported sandbox_mode for {name}: {sandbox}")
        if sandbox=="workspace-write" and name!="aegis_implementer":raise ConfigError(f"only aegis_implementer may default to workspace-write: {name}")
        module_root=codex_root.resolve()
        role_path=confined_path(module_root,role,must_exist=True,reject_symlinks=True)
        if not role_path.is_file():raise ConfigError(f"role instruction file missing: {role_path}")
        out[name]={"slug":slug,"description":desc,"role":role,"sandbox_mode":sandbox}
    if not out:raise ConfigError("Codex role registry is empty")
    return out

def _role_rel(name):return f"agents/{name.replace('_','-')}.toml"
def _role_tables(root:Path):return {name:{"description":spec["description"],"config_file":_role_rel(name)} for name,spec in load_role_specs(root).items()}
def _load_text(text:str):
    try:return tomllib.loads(text) if text.strip() else {}
    except Exception as e:raise ConfigError(f"malformed TOML: {e}") from e

def inspect(path:Path,root:Path|None=None):
    root=root or path.parent.parent
    if not path.exists():return {"exists":False,"conflicts":{},"values":{}}
    try:text,_,_=_decode_config(path.read_bytes())
    except UnicodeDecodeError as e:raise ConfigError("config is not UTF-8") from e
    data=_load_text(text);conflicts={}
    for k,v in TOP.items():
        if k in data and data[k]!=v:conflicts[k]=data[k]
    ad=data.get("agents",{})
    if not isinstance(ad,dict):conflicts["agents"]="not a table"
    else:
        for k,v in AGENTS.items():
            if k in ad and ad[k]!=v:conflicts[f"agents.{k}"]=ad[k]
        for name,want in _role_tables(root).items():
            got=ad.get(name)
            if got is not None and (not isinstance(got,dict) or any(got.get(k)!=v for k,v in want.items())):conflicts[f"agents.{name}"]=got
    features=data.get("features",{})
    if features is not None and not isinstance(features,dict):
        conflicts["features"]="not a table"
    elif isinstance(features,dict):
        v2=features.get("multi_agent_v2")
        if v2 is not None and not isinstance(v2,dict):conflicts["features.multi_agent_v2"]=v2
        elif isinstance(v2,dict):
            for k,v in V2.items():
                if k in v2 and v2[k]!=v:conflicts[f"features.multi_agent_v2.{k}"]=v2[k]
    return {"exists":True,"conflicts":conflicts,"values":data}

def render_new(root:Path):
    lines=[f"{k} = {_toml_value(v)}" for k,v in TOP.items()]
    lines += ["","[agents]"]+[f"{k} = {_toml_value(v)}" for k,v in AGENTS.items()]
    lines += ["","[features.multi_agent_v2]"]+[f"{k} = {_toml_value(v)}" for k,v in V2.items()]
    for name,spec in _role_tables(root).items():
        lines += ["",f"[agents.{name}]",f"description = {_toml_value(spec['description'])}",f"config_file = {_toml_value(spec['config_file'])}"]
    return "\n".join(lines)+"\n"

def _managed_conflicts(data,root:Path):
    out=[]
    for k,v in TOP.items():
        if k in data and data[k]!=v:out.append(k)
    ad=data.get("agents",{})
    if not isinstance(ad,dict):return out+["agents"]
    for k,v in AGENTS.items():
        if k in ad and ad[k]!=v:out.append("agents."+k)
    for n,want in _role_tables(root).items():
        got=ad.get(n)
        if got is not None and (not isinstance(got,dict) or any(got.get(k)!=v for k,v in want.items())):out.append("agents."+n)
    features=data.get("features",{})
    if features is not None and not isinstance(features,dict):out.append("features")
    elif isinstance(features,dict):
        v2=features.get("multi_agent_v2")
        if v2 is not None and not isinstance(v2,dict):out.append("features.multi_agent_v2")
        elif isinstance(v2,dict):
            for k,v in V2.items():
                if k in v2 and v2[k]!=v:out.append("features.multi_agent_v2."+k)
    return out

def merge_conservative(text:str,root:Path):
    if re.search(r"(?m)^\s*agents\s*=\s*\{",text):raise ConfigError("unsupported inline agents table")
    if re.search(r"(?m)^\s*features\s*=\s*\{",text):raise ConfigError("unsupported inline features table")
    if re.search(r"(?m)^\s*agents\.",text):raise ConfigError("unsupported dotted agents keys; use table form")
    if re.search(r"(?m)^\s*features\.multi_agent_v2\.",text):raise ConfigError("unsupported dotted multi_agent_v2 keys; use table form")
    data=_load_text(text);conflicts=_managed_conflicts(data,root)
    if conflicts:raise ConfigError("conflicting managed values: "+", ".join(conflicts))
    lines=text.splitlines();first_table=next((i for i,l in enumerate(lines) if re.match(r"^\s*\[",l)),len(lines))
    insert=[f"{k} = {_toml_value(v)}  # aegis-v4-managed" for k,v in TOP.items() if k not in data]
    lines[first_table:first_table]=insert+([""] if insert and first_table<len(lines) else [])
    astart=next((i for i,l in enumerate(lines) if re.match(r"^\s*\[agents\]\s*(#.*)?$",l)),None)
    if astart is None:
        nested=next((i for i,l in enumerate(lines) if re.match(r"^\s*\[agents\.",l)),None)
        block=["[agents]  # aegis-v4-managed-section"]+[f"{k} = {_toml_value(v)}  # aegis-v4-managed" for k,v in AGENTS.items()]+[""]
        if nested is None:
            if lines and lines[-1].strip():lines.append("")
            lines.extend(block[:-1])
        else:
            lines[nested:nested]=block
    else:
        end=next((i for i in range(astart+1,len(lines)) if re.match(r"^\s*\[",lines[i])),len(lines));existing=data.get("agents",{})
        lines[end:end]=[f"{k} = {_toml_value(v)}  # aegis-v4-managed" for k,v in AGENTS.items() if k not in existing]
    # Add/augment the V2 table without setting its `enabled` flag.  This lets the active Codex
    # runtime/model choose V1 vs V2 while still constraining V2 if selected.
    v2start=next((i for i,l in enumerate(lines) if re.match(r"^\s*\[features\.multi_agent_v2\]\s*(#.*)?$",l)),None)
    features=data.get("features",{}) if isinstance(data.get("features",{}),dict) else {}
    v2existing=features.get("multi_agent_v2",{}) if isinstance(features.get("multi_agent_v2",{}),dict) else {}
    if v2start is None:
        if lines and lines[-1].strip():lines.append("")
        lines += ["[features.multi_agent_v2]  # aegis-v4-managed-section"]+[f"{k} = {_toml_value(v)}  # aegis-v4-managed" for k,v in V2.items()]
    else:
        v2end=next((i for i in range(v2start+1,len(lines)) if re.match(r"^\s*\[",lines[i])),len(lines))
        lines[v2end:v2end]=[f"{k} = {_toml_value(v)}  # aegis-v4-managed" for k,v in V2.items() if k not in v2existing]
    ad=data.get("agents",{}) if isinstance(data.get("agents",{}),dict) else {}
    for name,spec in _role_tables(root).items():
        if name in ad:continue
        if lines and lines[-1].strip():lines.append("")
        lines += [f"[agents.{name}]  # aegis-v4-managed-role",f"description = {_toml_value(spec['description'])}",f"config_file = {_toml_value(spec['config_file'])}"]
    return "\n".join(lines).rstrip()+"\n"

def render_role(root:Path,name:str):
    specs=load_role_specs(root);spec=specs[name]
    codex_root=framework_dir(root)/"modules"/"codex"
    base=(codex_root/"roles"/"BASE.md").read_text(encoding="utf-8").strip()
    role=(codex_root/spec["role"]).read_text(encoding="utf-8").strip()
    instructions=base+"\n\n"+role+"\n"
    return "\n".join([
        f"{MARKER} {name}",f"name = {_toml_value(name)}",f"description = {_toml_value(spec['description'])}",
        'model = "gpt-5.6-sol"','model_reasoning_effort = "max"',f"sandbox_mode = {_toml_value(spec['sandbox_mode'])}",
        f"developer_instructions = {_toml_value(instructions)}","",
    ])

_SCHEMA_KEYS = {
    "model",
    "model_reasoning_effort",
    "max_concurrent_threads_per_session",
    "max_depth",
    "default_subagent_model",
    "default_subagent_reasoning_effort",
    "multi_agent_v2",
}


def _binary_key_probe(path:Path, keys:set[str])->set[str]:
    wanted={key:key.encode("ascii") for key in keys};found=set();overlap=b""
    with path.open("rb") as stream:
        while True:
            chunk=stream.read(1024*1024)
            if not chunk:break
            block=overlap+chunk
            for key,raw in wanted.items():
                if key not in found and raw in block:found.add(key)
            if found==keys:break
            overlap=block[-256:]
    return found


def _current_codex_executable()->Path|None:
    command=shutil.which("codex")
    if command:
        candidate=Path(command)
        if candidate.is_file():return candidate
    if os.name=="nt":
        windows_apps=Path(os.environ.get("ProgramFiles",r"C:\Program Files"))/"WindowsApps"
        try:
            candidates=sorted(windows_apps.glob("OpenAI.Codex_*_x64__*\\app\\resources\\codex.exe"),key=lambda path:path.parent.parent.parent.name,reverse=True)
        except OSError:
            candidates=[]
        for candidate in candidates:
            if candidate.is_file():return candidate
    return None


def probe_current_schema(executable:Path|None=None)->dict:
    candidate=executable or _current_codex_executable()
    if candidate is None or not candidate.is_file():
        return {"capability":"MISSING","available":False,"reason":"Codex executable not found","supported_keys":[]}
    version=None;execution="UNOBSERVABLE";error=None
    try:
        result=run_process([str(candidate),"--version"],cwd=candidate.parent,timeout=10,capture_limit=65536)
        if result.returncode==0 and not result.timed_out:
            version=result.stdout.strip();execution="AVAILABLE"
        else:error=f"exit={result.returncode} timeout={result.timed_out}"
    except (OSError,RuntimeError) as exc:error=str(exc)
    try:found=_binary_key_probe(candidate,_SCHEMA_KEYS)
    except OSError as exc:
        return {"capability":"UNOBSERVABLE","available":False,"path":str(candidate),"version":version,"reason":str(exc),"supported_keys":[]}
    missing=sorted(_SCHEMA_KEYS-found)
    # Binary string presence is useful static drift evidence, but it is not a
    # parser acceptance test and cannot establish effective layered config.
    # The documented effective-config surface is interactive (/debug-config),
    # so fail closed unless a future machine-readable CLI probe is implemented.
    return {
        "capability":"INCOMPATIBLE" if missing else "UNOBSERVABLE",
        "available":False,
        "path":str(candidate),
        "version":version or (candidate.parents[2].name if len(candidate.parents)>2 else candidate.name),
        "execution":execution,
        "execution_error":error,
        "supported_keys":[],
        "static_binary_key_evidence":sorted(found),
        "missing_keys":missing,
        "reason":(
            "required managed keys are absent from the installed binary"
            if missing else
            "binary key strings and --version do not prove that the current CLI parser accepts the managed config or that it is effective"
        ),
    }


def verify_managed_source(root:Path)->tuple[bool,dict]:
    """Verify shipped Codex configuration without claiming live effective behavior."""
    problems=[]
    managed=framework_dir(root)/"modules"/"codex"/"config"/"managed.toml"
    try:
        with managed.open("rb") as stream:data=tomllib.load(stream)
    except Exception as exc:return False,{"problems":[f"managed source malformed: {exc}"]}
    for key,value in TOP.items():
        if data.get(key)!=value:problems.append(f"source {key}={data.get(key)!r}")
    agents=data.get("agents",{})
    if not isinstance(agents,dict):problems.append("source agents is not a table");agents={}
    for key,value in AGENTS.items():
        if agents.get(key)!=value:problems.append(f"source agents.{key}={agents.get(key)!r}")
    features=data.get("features",{})
    v2=features.get("multi_agent_v2",{}) if isinstance(features,dict) else {}
    for key,value in V2.items():
        if not isinstance(v2,dict) or v2.get(key)!=value:problems.append(f"source features.multi_agent_v2.{key} mismatch")
    try:
        specs=load_role_specs(root)
        for name,spec in specs.items():
            role=tomllib.loads(render_role(root,name))
            if role.get("model")!=TOP["model"] or role.get("model_reasoning_effort")!=TOP["model_reasoning_effort"]:
                problems.append(f"rendered role {name} lowers model or reasoning")
            instructions=role.get("developer_instructions","")
            if "Never spawn or delegate" not in instructions:problems.append(f"rendered role {name} lacks nested-delegation prohibition")
            if spec["sandbox_mode"]=="workspace-write" and name!="aegis_implementer":problems.append(f"unexpected write-capable role {name}")
    except Exception as exc:problems.append(f"role source verification failed: {exc}")
    probe=probe_current_schema()
    if probe.get("capability")=="INCOMPATIBLE":problems.append("installed Codex schema lacks required managed keys")
    return not problems,{"problems":problems,"schema_probe":probe,"live_effective":{"outcome":"UNAVAILABLE","capability_status":"UNOBSERVABLE","detail":"Codex executable/config schema inspection cannot expose effective spawned-child model, reasoning, depth, or live concurrency metadata."}}


def _decode_config(raw:bytes)->tuple[str,bool,str]:
    bom=raw.startswith(codecs.BOM_UTF8);payload=raw[len(codecs.BOM_UTF8):] if bom else raw
    try:text=payload.decode("utf-8")
    except UnicodeDecodeError as exc:raise ConfigError("config is not UTF-8") from exc
    newline="\r\n" if b"\r\n" in payload else "\n"
    return text,bom,newline


def _encode_config(text:str,bom:bool,newline:str)->bytes:
    normalized="\n".join(text.splitlines())+("\n" if text.endswith(("\n","\r")) else "")
    if newline!="\n":normalized=normalized.replace("\n",newline)
    return (codecs.BOM_UTF8 if bom else b"")+normalized.encode("utf-8")


def _plan(root:Path):
    config=confined_path(root,".codex/config.toml",reject_symlinks=True);old_raw=config.read_bytes() if config.exists() else None
    if old_raw is None:new_raw=render_new(root).encode("utf-8")
    else:
        old_text,bom,newline=_decode_config(old_raw);new_raw=_encode_config(merge_conservative(old_text,root),bom,newline)
    changes=[(config,old_raw,new_raw)]
    for name in load_role_specs(root):
        p=confined_path(root,Path(".codex")/_role_rel(name),reject_symlinks=True);desired=render_role(root,name).encode("utf-8");oldrole=p.read_bytes() if p.exists() else None
        if oldrole is not None and oldrole!=desired:
            try:oldtext=oldrole.decode("utf-8")
            except UnicodeDecodeError as exc:raise ConfigError(f"role file is not UTF-8: {p}") from exc
            if not oldtext.startswith(MARKER):raise ConfigError(f"refusing to overwrite unmanaged role file: {p}")
        changes.append((p,oldrole,desired))
    return changes


def _load_latest_journal(root:Path):
    path=_journal_path(root);legacy=False
    if not path.exists() and _legacy_journal_path(root).exists():path=_legacy_journal_path(root);legacy=True
    if not path.exists():raise ConfigError("no Codex install journal; refusing destructive guesswork")
    try:data=json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:raise ConfigError(f"invalid Codex install journal: {exc}") from exc
    if legacy:
        raise ConfigError("Codex recovery metadata exists only under disposable runtime; migrate before uninstall")
    if data.get("schema")!=3 or not isinstance(data.get("files"),list):raise ConfigError("unsupported Codex install journal schema")
    return data,path


def _journal_bytes(journal:dict)->bytes:return (json.dumps(journal,indent=2,sort_keys=True)+"\n").encode("utf-8")


def install(root:Path,dry_run=True,*,schema_probe:dict|None=None):
    root=root.resolve(strict=True);recover_named_transactions(persistent_dir(root)/"transactions",expected_root=root,names=("codex-install","codex-uninstall"));probe=schema_probe or probe_current_schema()
    if not probe.get("available"):raise ConfigError(f"current Codex schema is not compatible/observable: {probe}")
    initial=_plan(root);changed=[item for item in initial if item[1]!=item[2]]
    report={"dry_run":dry_run,"changed":[str(p) for p,old,new in changed],"unchanged":[str(p) for p,old,new in initial if old==new],"schema_probe":probe}
    if dry_run or not changed:return report
    lock=FileLock(persistent_dir(root)/"locks"/"codex-install.lock","codex-adapter-install");lock.acquire();created=[]
    try:
        fresh=_plan(root)
        if [(str(p),_sha_bytes(old or b"") if old is not None else None,_sha_bytes(new)) for p,old,new in fresh] != [(str(p),_sha_bytes(old or b"") if old is not None else None,_sha_bytes(new)) for p,old,new in initial]:
            raise ConfigError("Codex destinations changed during install planning")
        prior=None
        if _journal_path(root).exists():prior,_=_load_latest_journal(root)
        prior_by_path={rec["path"]:rec for rec in prior.get("files",[])} if prior else {}
        records=[];mutations=[];state_root=_state_root(root)
        for p,old,new in fresh:
            rel=p.relative_to(root).as_posix();previous=prior_by_path.get(rel);mode=(p.stat().st_mode&0o777) if p.exists() else None
            if previous:
                if _sha_bytes(old) != previous.get("installed_sha256"):raise ConfigError(f"managed Codex file drifted before upgrade: {p}")
                original_sha=previous.get("original_sha256");backup_rel=previous.get("backup");created_file=previous.get("created_file");original_mode=previous.get("original_mode")
            else:
                original_sha=_sha_bytes(old);created_file=old is None;original_mode=mode;backup_rel=None
                if old is not None:
                    backup=state_root/"backups"/f"original-{uuid.uuid4().hex}.bin";backup.parent.mkdir(parents=True,exist_ok=True)
                    atomic_write_bytes(backup,old,root=root,mode=0o600);created.append(backup);backup_rel=backup.relative_to(root).as_posix()
            records.append({"path":rel,"created_file":created_file,"original_sha256":original_sha,"installed_sha256":_sha_bytes(new),"original_mode":original_mode,"backup":backup_rel})
            if old!=new:mutations.append(Mutation(p,new,expected_sha256=_sha_bytes(old),expected_exists=old is not None,mode=mode))
        journal={"schema":3,"framework_version":(framework_dir(root)/"VERSION").read_text(encoding="utf-8").strip(),"schema_probe":probe,"files":records,"upgrade_count":int(prior.get("upgrade_count",0) if prior else 0)+(1 if prior else 0)}
        mutations.append(Mutation(_journal_path(root),_journal_bytes(journal),expected_sha256=_sha_path(_journal_path(root)),expected_exists=_journal_path(root).exists(),mode=0o600))
        FileTransaction(root,mutations,state_dir=persistent_dir(root)/"transactions",name="codex-install").commit(retain=False)
        _legacy_journal_path(root).unlink(missing_ok=True);report.update({"journal":str(_journal_path(root)),"dry_run":False});return report
    except BaseException:
        if not _journal_path(root).exists():
            for path in created:path.unlink(missing_ok=True)
        raise
    finally:lock.release()


def uninstall(root:Path,dry_run=True):
    root=root.resolve(strict=True);recover_named_transactions(persistent_dir(root)/"transactions",expected_root=root,names=("codex-install","codex-uninstall"));journal,journal_path=_load_latest_journal(root);actions=[]
    for rec in journal["files"]:
        p=confined_path(root,rec["path"],reject_symlinks=True)
        if _sha_path(p)!=rec.get("installed_sha256"):raise ConfigError(f"installed file changed since install; refusing to overwrite: {p}")
        backup_bytes=None
        if not rec.get("created_file"):
            backup_rel=rec.get("backup")
            if not isinstance(backup_rel,str):raise ConfigError(f"required backup metadata missing: {p}")
            backup=confined_path(root,backup_rel,must_exist=True,reject_symlinks=True);backup_bytes=backup.read_bytes()
            if _sha_bytes(backup_bytes)!=rec.get("original_sha256"):raise ConfigError(f"backup integrity failure: {backup}")
        actions.append((p,rec,backup_bytes))
    report={"dry_run":dry_run,"restore_or_remove":[str(p) for p,_,_ in actions],"journal":str(journal_path)}
    if dry_run:return report
    lock=FileLock(persistent_dir(root)/"locks"/"codex-install.lock","codex-adapter-uninstall");lock.acquire()
    try:
        for p,rec,_ in actions:
            if _sha_path(p)!=rec.get("installed_sha256"):raise ConfigError(f"installed file changed before uninstall write: {p}")
        mutations=[]
        for p,rec,backup_bytes in actions:
            mutations.append(Mutation(p,None if rec.get("created_file") else backup_bytes,expected_sha256=rec["installed_sha256"],expected_exists=True,mode=rec.get("original_mode")))
            if rec.get("backup"):
                backup=confined_path(root,rec["backup"],must_exist=True,reject_symlinks=True);mutations.append(Mutation(backup,None,expected_sha256=_sha_path(backup),expected_exists=True))
        mutations.append(Mutation(journal_path,None,expected_sha256=_sha_path(journal_path),expected_exists=True))
        FileTransaction(root,mutations,state_dir=persistent_dir(root)/"transactions",name="codex-uninstall").commit(retain=False)
        return report
    finally:lock.release()

def verify_static(root:Path):
    path=root/".codex"/"config.toml"
    if not path.exists():return False,{"missing":str(path)}
    info=inspect(path,root);data=info["values"];problems=[]
    for k,v in TOP.items():
        if data.get(k)!=v:problems.append(f"{k}={data.get(k)!r}")
    ad=data.get("agents",{})
    for k,v in AGENTS.items():
        if ad.get(k)!=v:problems.append(f"agents.{k}={ad.get(k)!r}")
    v2=data.get("features",{}).get("multi_agent_v2",{}) if isinstance(data.get("features",{}),dict) else {}
    for k,v in V2.items():
        if not isinstance(v2,dict) or v2.get(k)!=v:problems.append(f"features.multi_agent_v2.{k}={v2.get(k) if isinstance(v2,dict) else v2!r}")
    for n,want in _role_tables(root).items():
        if ad.get(n)!=want:problems.append(f"agents.{n} missing/mismatched")
        rp=root/".codex"/want["config_file"]
        if not rp.is_file():problems.append(f"role file missing: {rp}");continue
        try:rd=tomllib.loads(rp.read_text(encoding="utf-8"))
        except Exception as e:problems.append(f"role file malformed {rp}: {e}");continue
        if rd.get("model")!="gpt-5.6-sol":problems.append(f"{n}.model={rd.get('model')!r}")
        if rd.get("model_reasoning_effort")!="max":problems.append(f"{n}.model_reasoning_effort={rd.get('model_reasoning_effort')!r}")
        if "Never spawn or delegate" not in rd.get("developer_instructions",""):problems.append(f"{n} missing no-delegation instruction")
    return not problems,{"problems":problems,"note":"Static verification cannot prove effective live Codex child configuration. Current Codex releases may have host/version-specific custom-agent application bugs; verify effective child metadata when the client exposes it."}
