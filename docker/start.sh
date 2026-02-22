#!/usr/bin/env sh
set -eu

# Cloud Run injects PORT at runtime.
if [ -n "${PORT:-}" ] && [ -z "${OPENFOAM_MCP_PORT:-}" ]; then
  export OPENFOAM_MCP_PORT="${PORT}"
fi

export OPENFOAM_MCP_HOST="${OPENFOAM_MCP_HOST:-0.0.0.0}"
export OPENFOAM_MCP_PORT="${OPENFOAM_MCP_PORT:-8080}"
export OPENFOAM_MCP_TRANSPORT="${OPENFOAM_MCP_TRANSPORT:-streamable-http}"
export OPENFOAM_MCP_ARTIFACT_DIR="${OPENFOAM_MCP_ARTIFACT_DIR:-/app/artifacts}"
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export PYTHONPATH="${PYTHONPATH:-${ROOT_DIR}/src}"

exec python3 run_server.py

