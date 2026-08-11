"""Build and verify the exact main-branch release in uncredentialed Dagger stages.

The workflow publishes prebuilt images in a separate inline credential boundary;
this checked-out Python module never receives the registry token.
"""
from __future__ import annotations

import os
import sys

from _common import REPO_ROOT, sh, stage, with_docker_socket, with_env, with_source
from _images import DOCKER_CLI_IMAGE, GOLANG_IMAGE, PYTHON_BOOKWORM_IMAGE

ENCORE_VERSION = "1.57.9"
ENCORE_SHA256 = "dfd43dcd456f91414a823315480da921333e6d1e3535ab48c47c09225d022af5"
KUBECTL_VERSION = "v1.33.0"
KUBECTL_SHA256 = "9efe8d3facb23e1618cba36fb1c4e15ac9dc3ed5a2c2e18109e4a66b2bac12dc"


def _docker_cli(dag):
    return dag.container().from_(DOCKER_CLI_IMAGE).with_entrypoint([])


def _docker_sh(container, script: str):
    """Run POSIX shell in the pinned Alpine Docker CLI image (which has no bash)."""
    return container.with_exec(["sh", "-euc", script])


def _release_context() -> tuple[str, str, str]:
    if os.environ.get("GITHUB_EVENT_NAME") != "push" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("release.py may only run for a push to refs/heads/main")
    sha = os.environ.get("GITHUB_SHA")
    verified_sha = os.environ.get("VERIFIED_SHA")
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").lower()
    if not sha or not owner or not verified_sha:
        raise RuntimeError("release requires GITHUB_SHA, VERIFIED_SHA and GITHUB_REPOSITORY_OWNER")
    if verified_sha != sha:
        raise RuntimeError(f"release provenance mismatch: verified {verified_sha!r} != checkout {sha!r}")
    return sha, verified_sha, owner


async def build(dag) -> None:
    sha, _, owner = _release_context()

    encore_ref = f"ghcr.io/{owner}/eve-trade-encore-backend:{sha}"
    settlement_ref = f"ghcr.io/{owner}/eve-trade-trade-settlement:{sha}"
    quilkin_ref = f"ghcr.io/{owner}/eve-trade-quilkin:{sha}"

    # Assemble the build environment only from digest-pinned images: Python's
    # full Bookworm image supplies Python/curl/git, while Go and Docker are
    # copied from their separately pinned images.
    go_toolchain = dag.container().from_(GOLANG_IMAGE)
    docker_tools = _docker_cli(dag)
    encore = dag.container().from_(PYTHON_BOOKWORM_IMAGE)
    encore = encore.with_directory("/usr/local/go", go_toolchain.directory("/usr/local/go"))
    encore = encore.with_file("/usr/local/bin/docker", docker_tools.file("/usr/local/bin/docker"))
    encore = with_source(encore, dag, include_git=True)
    encore = with_docker_socket(encore, dag)
    encore = with_env(encore, {
        "ENCORE_CLI_VERSION": ENCORE_VERSION,
        "ENCORE_CLI_SHA256": ENCORE_SHA256,
        "ENCORE_INSTALL": "/opt/encore",
        "GOPATH": "/go",
        "GOTOOLCHAIN": "local",
    })
    encore = encore.with_env_variable(
        "PATH",
        "/opt/encore/bin:/go/bin:/usr/local/go/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    )
    encore = sh(encore, [
        'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
        'ENCORE_INSTALL=/opt/encore bash scripts/install_encore_cli.sh',
        'bash scripts/harden_encore_install.sh /opt/encore',
    ])
    encore = sh(encore, [
        f"GOFLAGS=-mod=mod encore build docker --config infra/encore/self-host.nsq.json {encore_ref}",
        f'docker image inspect "{encore_ref}" >/dev/null',
    ])
    await encore.sync()

    # Build the remaining images locally in the pinned Docker CLI image.  This
    # container has source and the host Docker socket, but never receives a token.
    images = _docker_cli(dag)
    images = with_source(images, dag, include_git=False)
    images = with_docker_socket(images, dag)
    images = with_env(images)
    images = _docker_sh(images, f'''
      docker buildx version
      docker buildx build --platform linux/amd64 --load --file distributed-backend/docker/trade-settlement.Dockerfile --tag "{settlement_ref}" .
      docker buildx build --platform linux/amd64 --load --file distributed-backend/docker/quilkin.Dockerfile --tag "{quilkin_ref}" .
      docker image inspect "{settlement_ref}" >/dev/null
      docker image inspect "{quilkin_ref}" >/dev/null
    ''')
    await images.sync()


async def verify(dag) -> None:
    _, verified_sha, _ = _release_context()
    image_lock = REPO_ROOT / "release-image-lock.json"
    if not image_lock.is_file():
        raise RuntimeError("isolated publisher did not produce release-image-lock.json")
    # Render and verify release manifests without registry credentials.  PyYAML
    # and kubectl are independently hash-verified before use.
    verifier = with_source(dag.container().from_(PYTHON_BOOKWORM_IMAGE), dag, include_git=False)
    verifier = with_env(verifier, {"VERIFIED_SHA": verified_sha})
    verifier = sh(verifier, [
        "python -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: -r .github/dagger/requirements-release.txt",
        f"python - <<'PYKUBECTL'\nimport hashlib\nimport urllib.request\nfrom pathlib import Path\nurl = 'https://dl.k8s.io/release/{KUBECTL_VERSION}/bin/linux/amd64/kubectl'\ntarget = Path('/usr/local/bin/kubectl')\nwith urllib.request.urlopen(url, timeout=60) as response, target.open('wb') as stream:\n    digest = hashlib.sha256()\n    total = 0\n    while chunk := response.read(1024 * 1024):\n        total += len(chunk)\n        if total > 128 * 1024 * 1024:\n            raise SystemExit('kubectl download exceeds size limit')\n        digest.update(chunk)\n        stream.write(chunk)\nif digest.hexdigest() != '{KUBECTL_SHA256}':\n    target.unlink(missing_ok=True)\n    raise SystemExit('kubectl checksum mismatch')\ntarget.chmod(0o755)\nPYKUBECTL",
        "kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod > /tmp/eve-trade-prod-template.yaml",
        "python scripts/render_release_kubernetes.py --manifest /tmp/eve-trade-prod-template.yaml --image-lock release-image-lock.json --output release-kubernetes.yaml --repository \"$GITHUB_REPOSITORY\" --sha \"$GITHUB_SHA\"",
        "python scripts/verify_rendered_kubernetes.py release-kubernetes.yaml",
        "python - <<'PYVERIFY'\nimport hashlib, json, os\nfrom pathlib import Path\ndef sha256(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()\nPath('release-verification.json').write_text(json.dumps({\n  'repository': os.environ['GITHUB_REPOSITORY'],\n  'merge_sha': os.environ['GITHUB_SHA'],\n  'verified_sha': os.environ['VERIFIED_SHA'],\n  'verification_run_id': os.environ['GITHUB_RUN_ID'],\n  'verification_run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],\n  'image_lock_sha256': sha256('release-image-lock.json'),\n  'manifest_sha256': sha256('release-kubernetes.yaml'),\n}, indent=2, sort_keys=True) + '\\n', encoding='utf-8')\nPYVERIFY",
    ])
    await verifier.sync()
    for name in ("release-verification.json", "release-image-lock.json", "release-kubernetes.yaml"):
        await verifier.file(f"/src/{name}").export(str(REPO_ROOT / name))


if __name__ == "__main__":
    modes = {"build": build, "verify": verify}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        raise SystemExit(f"usage: {sys.argv[0]} <{'|'.join(modes)}>")
    selected = sys.argv[1]
    stage(f"release-{selected}", modes[selected])
