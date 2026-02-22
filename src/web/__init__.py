"""Web/runtime helpers for cloud-style OpenFOAM MCP deployment."""

from .config import OpenFOAMServerConfig, load_server_config
from .artifacts import (
    append_job_event,
    build_artifact_url,
    cleanup_old_job_dirs,
    create_job_artifact_dir,
    create_job_bundle,
    list_job_artifacts,
    load_job_manifest,
    read_job_event_lines,
    safe_resolve_artifact_path,
    write_job_manifest,
)
from .events import publish_job_event, subscribe_job_events
from .portal import render_portal_html
from .routes import register_custom_routes

__all__ = [
    "OpenFOAMServerConfig",
    "load_server_config",
    "append_job_event",
    "build_artifact_url",
    "cleanup_old_job_dirs",
    "create_job_artifact_dir",
    "create_job_bundle",
    "list_job_artifacts",
    "load_job_manifest",
    "read_job_event_lines",
    "safe_resolve_artifact_path",
    "write_job_manifest",
    "publish_job_event",
    "subscribe_job_events",
    "render_portal_html",
    "register_custom_routes",
]
