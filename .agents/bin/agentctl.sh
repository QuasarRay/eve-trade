#!/usr/bin/env sh
set -eu
if ! command -v python3 >/dev/null 2>&1; then
    echo "Aegis requires python3 version 3.11 or newer" >&2
    exit 127
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "Aegis requires python3 version 3.11 or newer" >&2
    exit 126
fi
exec python3 -B "$(dirname "$0")/agentctl.py" "$@"
