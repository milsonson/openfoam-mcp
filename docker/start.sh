#!/usr/bin/env bash
set -euo pipefail

OPENFOAM_ROOT="${OPENFOAM_ROOT:-/opt/openfoam11}"
FOAM_PLATFORM_LIB_DIR="${OPENFOAM_ROOT}/platforms/linux64GccDPInt32Opt/lib"
FOAM_MPI_LIB_DIR="${FOAM_PLATFORM_LIB_DIR}/openmpi-system"
DEFAULT_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if [ ! -d "${OPENFOAM_ROOT}" ]; then
  echo "ERROR: OpenFOAM root directory not found: ${OPENFOAM_ROOT}" >&2
  exit 1
fi

export WM_PROJECT="${WM_PROJECT:-OpenFOAM}"
export WM_PROJECT_VERSION="${WM_PROJECT_VERSION:-11}"
export WM_PROJECT_DIR="${WM_PROJECT_DIR:-${OPENFOAM_ROOT}}"
export FOAM_APPBIN="${FOAM_APPBIN:-${OPENFOAM_ROOT}/platforms/linux64GccDPInt32Opt/bin}"
export FOAM_TUTORIALS="${FOAM_TUTORIALS:-${OPENFOAM_ROOT}/tutorials}"
export LD_LIBRARY_PATH="${FOAM_PLATFORM_LIB_DIR}:${FOAM_MPI_LIB_DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export PATH="${FOAM_APPBIN}:${OPENFOAM_ROOT}/bin:${PATH:-${DEFAULT_PATH}}"

if ! command -v blockMesh >/dev/null 2>&1; then
  echo "ERROR: blockMesh is not available after OpenFOAM env setup." >&2
  exit 1
fi

# Cloud Run injects PORT at runtime.
if [[ -n "${PORT:-}" && -z "${OPENFOAM_MCP_PORT:-}" ]]; then
  export OPENFOAM_MCP_PORT="${PORT}"
fi

export OPENFOAM_MCP_HOST="${OPENFOAM_MCP_HOST:-0.0.0.0}"
export OPENFOAM_MCP_PORT="${OPENFOAM_MCP_PORT:-8080}"
export OPENFOAM_MCP_TRANSPORT="${OPENFOAM_MCP_TRANSPORT:-streamable-http}"
export OPENFOAM_MCP_ARTIFACT_DIR="${OPENFOAM_MCP_ARTIFACT_DIR:-/app/artifacts}"
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export PYTHONPATH="${PYTHONPATH:-${ROOT_DIR}/src}"

exec /usr/bin/python3 run_server.py
