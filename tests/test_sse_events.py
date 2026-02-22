"""Tests for job status and SSE event endpoints."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.web.artifacts import append_job_event, create_job_artifact_dir, write_job_manifest
from src.web.routes import register_custom_routes


def _build_client() -> TestClient:
    mcp = FastMCP("openfoam-mcp-events-test")
    register_custom_routes(mcp)
    return TestClient(mcp.streamable_http_app())


def test_job_status_and_events_replay(monkeypatch, tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_BASE_URL", "http://localhost:8080/artifacts")

    create_job_artifact_dir("job_2")
    write_job_manifest(
        "job_2",
        {
            "status": "running",
            "stage": "solver",
            "template_id": "pipe_flow",
            "portal_url": "http://localhost:8080/portal/job_2",
        },
    )
    append_job_event(
        "job_2",
        {
            "job_id": "job_2",
            "stage": "solver",
            "status": "running",
            "message": "solver stage started",
            "timestamp": "2026-02-22T00:00:00+00:00",
        },
    )

    client = _build_client()

    status_resp = client.get("/jobs/job_2/status")
    assert status_resp.status_code == 200
    payload = status_resp.json()
    assert payload["status"] == "running"
    assert payload["stage"] == "solver"
    assert isinstance(payload.get("artifacts"), list)

    events_resp = client.get("/jobs/job_2/events?replay_only=true")
    assert events_resp.status_code == 200
    assert "text/event-stream" in events_resp.headers.get("content-type", "")
    assert "solver stage started" in events_resp.text
    assert "data:" in events_resp.text

