from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".agents" / "infra"))

from agentinfra.process import run_process


def main() -> int:
    xonsh = shutil.which("xonsh")
    if not xonsh:
        print(json.dumps({"ok": False, "outcome": "UNAVAILABLE", "capability_status": "MISSING", "reason": "xonsh executable not found"}, sort_keys=True))
        return 2
    checks: list[dict] = []
    ok = True
    version_result = run_process([xonsh, "--no-rc", "--version"], cwd=ROOT, timeout=20, capture_limit=16_000)
    version_match = re.search(r"(?:xonsh/)?(\d+)\.(\d+)\.(\d+)", version_result.stdout + "\n" + version_result.stderr)
    compatible = bool(not version_result.timed_out and version_result.returncode == 0 and version_match and (0, 19, 0) <= tuple(map(int, version_match.groups())) < (1, 0, 0))
    checks.append({"kind": "version", "passed": compatible, "version": version_match.group(0) if version_match else None, "exit": version_result.returncode})
    ok &= compatible
    for path in (ROOT / ".agents" / "bin" / "agentctl.xsh", ROOT / ".agents" / "modules" / "xonsh" / "rc.xsh"):
        result = run_process([xonsh, "-n", str(path)], cwd=ROOT, timeout=20, capture_limit=16_000)
        passed = not result.timed_out and result.returncode == 0
        checks.append({"kind": "syntax", "file": str(path), "passed": passed, "exit": result.returncode, "stderr_sha256": result.stderr_sha256})
        ok &= passed
    with tempfile.TemporaryDirectory(prefix="aegis xonsh unicode λ ") as directory:
        work = Path(directory)
        argv = ["space and 'quotes'", "unicode-雪", "$HOME;$(echo nope)"]
        script = (
            "import json,os,sys; "
            f"print(json.dumps({{'argv':sys.argv[-{len(argv)}:],'cwd':os.getcwd(),'env':os.environ.get('AEGIS_XONSH_PROBE')}})); "
            "print('aegis-xonsh-stderr',file=sys.stderr); sys.exit(7)"
        )
        result = run_process([xonsh, "--no-rc", "-c", script, *argv], cwd=work, timeout=20, env={"AEGIS_XONSH_PROBE": "unicode-π"}, capture_limit=16_000)
        try:
            payload = json.loads(result.stdout)
        except Exception:
            payload = None
        passed = (
            not result.timed_out
            and result.returncode == 7
            and payload == {"argv": argv, "cwd": str(work.resolve()), "env": "unicode-π"}
            and "aegis-xonsh-stderr" in result.stderr
        )
        checks.append(
            {
                "kind": "live-invocation",
                "passed": passed,
                "exit": result.returncode,
                "timed_out": result.timed_out,
                "argv_preserved": bool(payload and payload.get("argv") == argv),
                "cwd_preserved": bool(payload and payload.get("cwd") == str(work.resolve())),
                "environment_preserved": bool(payload and payload.get("env") == "unicode-π"),
                "stderr_separate": "aegis-xonsh-stderr" in result.stderr,
                "stderr_preview": result.stderr,
            }
        )
        ok &= passed
    rc_path = ROOT / ".agents" / "modules" / "xonsh" / "rc.xsh"
    rc_code = (
        "import json,os,sys\n"
        f"p={str(rc_path)!r}\n"
        "before_path=tuple(map(str,__xonsh__.env.get('PATH',())))\n"
        "__xonsh__.env['AEGIS_XONSH_UNRELATED']='preserve-unicode-\\u96ea'\n"
        "execx(open(p,encoding='utf-8').read(),mode='exec',glbs=globals(),filename=p)\n"
        "first_alias=list(aliases['agentctl'])\n"
        "first_root=__xonsh__.env.get('AEGIS_ROOT')\n"
        "execx(open(p,encoding='utf-8').read(),mode='exec',glbs=globals(),filename=p)\n"
        "print(json.dumps({'first_alias':first_alias,'second_alias':list(aliases['agentctl']),"
        "'root':first_root,'second_root':__xonsh__.env.get('AEGIS_ROOT'),'cwd':os.getcwd(),"
        "'path_unchanged':before_path==tuple(map(str,__xonsh__.env.get('PATH',()))),"
        "'unrelated':__xonsh__.env.get('AEGIS_XONSH_UNRELATED'),'python':sys.executable}))\n"
    )
    rc_result = run_process([xonsh, "--no-rc", "-c", rc_code], cwd=ROOT, timeout=20, capture_limit=32_000)
    try:
        rc_payload = json.loads(rc_result.stdout)
    except Exception:
        rc_payload = {}
    expected_ctl = str((ROOT / ".agents" / "bin" / "agentctl.py").resolve())
    rc_passed = bool(
        rc_result.returncode == 0
        and not rc_result.timed_out
        and rc_payload.get("first_alias") == rc_payload.get("second_alias")
        and rc_payload.get("first_alias") == [rc_payload.get("python"), "-B", expected_ctl]
        and rc_payload.get("root") == rc_payload.get("second_root") == str(ROOT.resolve())
        and rc_payload.get("cwd") == str(ROOT.resolve())
        and rc_payload.get("path_unchanged") is True
        and rc_payload.get("unrelated") == "preserve-unicode-\u96ea"
    )
    checks.append({"kind": "rc-idempotence", "passed": rc_passed, "exit": rc_result.returncode, "details": rc_payload})
    ok &= rc_passed
    wrapper = ROOT / ".agents" / "bin" / "agentctl.xsh"
    with tempfile.TemporaryDirectory(prefix="aegis wrapper root unicode \u96ea ") as directory:
        test_agents = Path(directory) / ".agents"
        test_agents.mkdir()
        (test_agents / "framework.toml").write_text("[framework]\nversion = '4.0.0'\n", encoding="utf-8")
        wrapper_result = run_process(
            [xonsh, "--no-rc", str(wrapper), "--root", directory, "shell", "choose", "--purpose", "oneshot"],
            cwd=ROOT,
            timeout=30,
            capture_limit=32_000,
        )
        try:
            wrapper_payload = json.loads(wrapper_result.stdout)
        except Exception:
            wrapper_payload = {}
        wrapper_passed = bool(
            wrapper_result.returncode == 0
            and not wrapper_result.timed_out
            and wrapper_payload.get("shell") == "direct"
            and not wrapper_result.stderr
        )
        checks.append({"kind": "wrapper-live", "passed": wrapper_passed, "exit": wrapper_result.returncode, "argv_root": directory, "stderr": wrapper_result.stderr})
        ok &= wrapper_passed
    exit_result = run_process([xonsh, "--no-rc", str(wrapper), "definitely-not-a-command"], cwd=ROOT, timeout=20, capture_limit=32_000)
    exit_passed = exit_result.returncode == 2 and bool(exit_result.stderr)
    checks.append({"kind": "wrapper-exit-propagation", "passed": exit_passed, "exit": exit_result.returncode})
    ok &= exit_passed
    doctor_result = run_process([xonsh, "--no-rc", str(wrapper), "doctor"], cwd=ROOT, timeout=30, capture_limit=64_000)
    try:
        doctor = json.loads(doctor_result.stdout)
        python_version = tuple(map(int, doctor.get("python", "0.0").split(".")[:2]))
    except Exception:
        doctor = {}
        python_version = (0, 0)
    doctor_passed = bool(doctor_result.returncode == 0 and python_version >= (3, 11) and doctor.get("reasoning_default") == "max")
    checks.append({"kind": "wrapper-doctor", "passed": doctor_passed, "exit": doctor_result.returncode, "python": doctor.get("python")})
    ok &= doctor_passed
    print(json.dumps({"ok": ok, "outcome": "PASS" if ok else "FAIL", "capability_status": "AVAILABLE", "xonsh": xonsh, "checks": checks}, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
