"""Tests for health/artifact/portal custom routes."""

from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.web.artifacts import create_job_artifact_dir, write_job_manifest
from src.web.routes import register_custom_routes


def _build_client() -> TestClient:
    mcp = FastMCP("openfoam-mcp-test")
    register_custom_routes(mcp)
    app = mcp.streamable_http_app()
    return TestClient(app)


def test_health_route() -> None:
    client = _build_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_artifact_and_portal_routes(monkeypatch, tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_BASE_URL", "http://localhost:8080/artifacts")

    job_dir = create_job_artifact_dir("job_1")
    payload = {
        "status": "completed",
        "template_id": "pipe_flow",
        "kpi_summary": {"converged": True},
        "quality_report": {"preflight_overall": "ready"},
        "warnings": [],
        "failures": [],
    }
    write_job_manifest("job_1", payload)
    (job_dir / "sample.txt").write_text("hello", encoding="utf-8")

    client = _build_client()

    portal_resp = client.get("/portal/job_1")
    assert portal_resp.status_code == 200
    assert "OpenFOAM Simulation Portal" in portal_resp.text
    assert "job_1" in portal_resp.text

    file_resp = client.get("/artifacts/job_1/sample.txt")
    assert file_resp.status_code == 200
    assert file_resp.text == "hello"

    invalid_resp = client.get("/artifacts/../../etc/passwd")
    assert invalid_resp.status_code in {400, 404}
