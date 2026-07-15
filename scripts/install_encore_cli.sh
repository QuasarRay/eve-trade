#!/usr/bin/env bash
set -euo pipefail

: "${ENCORE_CLI_VERSION:?ENCORE_CLI_VERSION is required}"
: "${ENCORE_CLI_SHA256:?ENCORE_CLI_SHA256 is required}"

case "$(uname -sm)" in
  "Linux x86_64") target="linux_amd64" ;;
  *)
    echo "unsupported Encore CI target: $(uname -sm)" >&2
    exit 1
    ;;
esac

install_dir="${ENCORE_INSTALL:-$HOME/.encore}"
archive_dir="$(mktemp -d)"
archive="$archive_dir/encore.tar.gz"
trap 'rm -rf "$archive_dir"' EXIT

curl \
  --proto '=https' \
  --tlsv1.2 \
  --fail \
  --location \
  --silent \
  --show-error \
  --output "$archive" \
  "https://d2f391esomvqpi.cloudfront.net/encore-${ENCORE_CLI_VERSION}-${target}.tar.gz"

printf '%s  %s\n' "$ENCORE_CLI_SHA256" "$archive" | sha256sum --check --status
mkdir -p "$install_dir"
tar -xzf "$archive" -C "$install_dir"
chmod +x "$install_dir/bin/encore"
"$install_dir/bin/encore" --help >/dev/null
printf 'installed Encore CLI %s from verified archive\n' "$ENCORE_CLI_VERSION"
