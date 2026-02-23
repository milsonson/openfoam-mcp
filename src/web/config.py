"""Runtime configuration helpers for OpenFOAM MCP cloud deployments."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


_ALLOWED_TRANSPORTS = {"sse", "streamable-http", "stdio"}


@dataclass(frozen=True)
class OpenFOAMServerConfig:
    """Runtime/server settings resolved from environment variables."""

    host: str
    port: int
    transport: str
    sse_path: str
    streamable_http_path: str
    public_host: str
    artifact_dir: Path
    artifact_base_url: str
    portal_base_url: str
    artifact_ttl_seconds: int | None
    artifact_max_jobs: int | None


def _parse_optional_non_negative_int_env(var_name: str, default_value: int) -> int | None:
    raw_value = os.getenv(var_name)
    if raw_value is None:
        return default_value

    normalized = raw_value.strip().lower()
    if normalized in {"none", "off", "false", "0"}:
        return None

    value = int(raw_value)
    if value < 0:
        raise ValueError(f"{var_name} 必须 >= 0，当前值: {value}")
    return value


def normalize_public_host(host: str) -> str:
    """Convert bind-only host values to a client-visible default."""
    if host in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return host


def _url_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def parse_transport(raw_transport: str | None) -> str:
    """Validate and normalize transport type."""
    transport = (raw_transport or "sse").strip().lower()
    if transport not in _ALLOWED_TRANSPORTS:
        options = ", ".join(sorted(_ALLOWED_TRANSPORTS))
        raise ValueError(f"OPENFOAM_MCP_TRANSPORT 必须是以下之一: {options}")
    return transport


def build_default_base_urls(public_host: str, port: int) -> tuple[str, str]:
    """Build default artifact and portal base URLs."""
    host = _url_host(normalize_public_host(public_host))
    prefix = f"http://{host}:{port}"
    return (f"{prefix}/artifacts", f"{prefix}/portal")


def load_server_config() -> OpenFOAMServerConfig:
    """Load and validate server configuration from environment variables."""
    host = os.getenv("OPENFOAM_MCP_HOST", "127.0.0.1")
    port = int(os.getenv("OPENFOAM_MCP_PORT", os.getenv("PORT", "8000")))
    transport = parse_transport(os.getenv("OPENFOAM_MCP_TRANSPORT", "sse"))
    sse_path = os.getenv("OPENFOAM_MCP_SSE_PATH", "/sse")
    streamable_http_path = os.getenv("OPENFOAM_MCP_STREAMABLE_HTTP_PATH", "/mcp")
    public_host = os.getenv("OPENFOAM_MCP_PUBLIC_HOST", normalize_public_host(host))

    artifact_default_dir = "/app/artifacts"
    app_root = Path("/app")
    if not app_root.exists() or not os.access(app_root, os.W_OK):
        artifact_default_dir = "/tmp/openfoam-mcp-artifacts"

    artifact_dir = Path(
        os.getenv("OPENFOAM_MCP_ARTIFACT_DIR", artifact_default_dir)
    ).resolve()
    artifact_default_url, portal_default_url = build_default_base_urls(public_host, port)
    artifact_base_url = os.getenv("OPENFOAM_MCP_ARTIFACT_BASE_URL", artifact_default_url)
    portal_base_url = os.getenv("OPENFOAM_MCP_PORTAL_BASE_URL", portal_default_url)

    artifact_ttl_seconds = _parse_optional_non_negative_int_env(
        "OPENFOAM_MCP_ARTIFACT_TTL_SECONDS",
        86400,
    )
    artifact_max_jobs = _parse_optional_non_negative_int_env(
        "OPENFOAM_MCP_ARTIFACT_MAX_JOBS",
        500,
    )

    return OpenFOAMServerConfig(
        host=host,
        port=port,
        transport=transport,
        sse_path=sse_path,
        streamable_http_path=streamable_http_path,
        public_host=public_host,
        artifact_dir=artifact_dir,
        artifact_base_url=artifact_base_url.rstrip("/"),
        portal_base_url=portal_base_url.rstrip("/"),
        artifact_ttl_seconds=artifact_ttl_seconds,
        artifact_max_jobs=artifact_max_jobs,
    )
