#!/usr/bin/env bash
set -euo pipefail

install_dir="${1:-${ENCORE_INSTALL:-$HOME/.encore}}"
encore_goroot="$install_dir/encore-go"
encore_runtime="$install_dir/runtimes/go"
encore_go="$encore_goroot/bin/go"
patch_sha256="11fb48b327695cbabd58422ffb3afdb579c3ed57d9653753aec812d3625b5904"
patch_url="https://github.com/golang/go/compare/go1.26.4...go1.26.5.patch"

for path in "$encore_go" "$encore_runtime/go.mod"; do
  if [[ ! -e "$path" ]]; then
    echo "Encore installation is missing required path: $path" >&2
    exit 1
  fi
done

python_cmd="$(command -v python3 || command -v python || true)"
if [[ -z "$python_cmd" ]]; then
  echo "Encore hardening requires Python for deterministic source and module metadata updates" >&2
  exit 1
fi

host_version="$(go version)"
if [[ "$host_version" != *"go1.26.5"* ]]; then
  echo "Encore hardening requires host Go 1.26.5, found: $host_version" >&2
  exit 1
fi

encore_version="$($encore_go version)"
rebuild_toolchain=false
case "$encore_version" in
  "go version go1.26.4-encore "*)
    patch_file="$(mktemp)"
    trap 'rm -f "$patch_file"' EXIT
    curl \
      --proto '=https' \
      --tlsv1.2 \
      --fail \
      --location \
      --silent \
      --show-error \
      --retry 5 \
      --retry-all-errors \
      --output "$patch_file" \
      "$patch_url"
    printf '%s  %s\n' "$patch_sha256" "$patch_file" | sha256sum --check --status
    git -C "$encore_goroot" apply --check --exclude=VERSION "$patch_file"
    git -C "$encore_goroot" apply --exclude=VERSION "$patch_file"
    rebuild_toolchain=true
    ;;
  "go version go1.26.5-encore "*)
    rebuild_toolchain=true
    ;;
  "go version go1.26.5 "*)
    ;;
  *)
    echo "unsupported Encore Go toolchain: $encore_version" >&2
    exit 1
    ;;
esac

if [[ "$rebuild_toolchain" == true ]]; then
  # Encore's non-semver release suffix makes patched versions compare older
  # than their upstream security baseline; the evidence message retains fork provenance.
  "$python_cmd" - "$encore_goroot/src/cmd/dist/build.go" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
old = 'return strings.TrimSuffix(b, "-encore") + "-encore"'
new = 'return strings.TrimSuffix(b, "-encore")'
if source.count(old) != 1:
    raise SystemExit(f"expected exactly one Encore release suffix expression in {path}")
path.write_text(source.replace(old, new), encoding="utf-8")
PY
  printf 'go1.26.5\n' >"$encore_goroot/VERSION"
  (
    cd "$encore_goroot/src"
    GOROOT_BOOTSTRAP="$(go env GOROOT)" ./make.bash
  )
fi

encore_version="$($encore_go version)"
if [[ "$encore_version" != "go version go1.26.5 "* ]]; then
  echo "Encore Go hardening produced an unexpected toolchain: $encore_version" >&2
  exit 1
fi

mapfile -t runtime_versions < <(
  cd "$encore_runtime"
  "$encore_go" mod edit -json | "$python_cmd" -c '
import json
import sys

document = json.load(sys.stdin)
versions = {entry["Path"]: entry["Version"] for entry in document.get("Require", [])}
print(versions.get("github.com/golang-jwt/jwt/v4", ""))
print(versions.get("golang.org/x/crypto", ""))
'
)
current_jwt="${runtime_versions[0]:-}"
current_crypto="${runtime_versions[1]:-}"
if [[ "$current_jwt" != "v4.5.0" && "$current_jwt" != "v4.5.2" ]]; then
  echo "unsupported Encore jwt/v4 dependency: $current_jwt" >&2
  exit 1
fi
if [[ "$current_crypto" != "v0.49.0" && "$current_crypto" != "v0.52.0" ]]; then
  echo "unsupported Encore x/crypto dependency: $current_crypto" >&2
  exit 1
fi

(
  cd "$encore_runtime"
  "$encore_go" mod edit -require=github.com/golang-jwt/jwt/v4@v4.5.2
  "$encore_go" mod edit -require=golang.org/x/crypto@v0.52.0
  for attempt in 1 2 3; do
    if GOTOOLCHAIN=local "$encore_go" mod download \
      github.com/golang-jwt/jwt/v4@v4.5.2 \
      golang.org/x/crypto@v0.52.0; then
      break
    fi
    if [[ "$attempt" == 3 ]]; then
      echo "failed to download hardened Encore runtime modules after $attempt attempts" >&2
      exit 1
    fi
    sleep "$((attempt * 2))"
  done
)

printf 'hardened Encore fork: %s, jwt/v4 v4.5.2, x/crypto v0.52.0\n' "$encore_version"
