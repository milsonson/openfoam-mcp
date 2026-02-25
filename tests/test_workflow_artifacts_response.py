"""Tests for workflow payload artifact/portal extensions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools.workflow_tools import RunWorkflowFromPromptInput, openfoam_run_workflow_from_prompt


def test_workflow_returns_portal_and_artifacts(monkeypatch, tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_BASE_URL", "http://localhost:8080/artifacts")
    monkeypatch.setenv("OPENFOAM_MCP_PORTAL_BASE_URL", "http://localhost:8080/portal")

    case_path = tmp_path / "case"
    result = openfoam_run_workflow_from_prompt(
        RunWorkflowFromPromptInput(
            description="模拟水在直径5cm、长度50cm的管道中以1m/s流动",
            case_path=str(case_path),
            run_mesh=False,
            run_solver=False,
            auto_apply_stability_fixes=True,
            response_format="json",
        )
    )
    payload = json.loads(result)

    assert payload["job_id"]
    assert payload["access_token"]
    assert payload["portal_url"].startswith("http://localhost:8080/portal/")
    assert payload["manifest_url"].startswith("http://localhost:8080/artifacts/")
    assert "token=" in payload["portal_url"]
    assert "token=" in payload["delivery_url"]
    assert "token=" in payload["manifest_url"]
    assert isinstance(payload["artifacts"], list)
    assert len(payload["artifacts"]) >= 1
    assert all("token=" in item["url"] for item in payload["artifacts"])
    assert "kpi_summary" in payload
    assert "quality_report" in payload

    job_id = payload["job_id"]
    assert (artifact_root / job_id / "manifest.json").exists()
    assert (artifact_root / job_id / "kpi_summary.json").exists()
    assert (artifact_root / job_id / "quality_report.json").exists()
    assert (artifact_root / job_id / "case_bundle.tar.gz").exists()


def test_workflow_auto_allocates_case_path_when_omitted(monkeypatch, tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_BASE_URL", "http://localhost:8080/artifacts")
    monkeypatch.setenv("OPENFOAM_MCP_PORTAL_BASE_URL", "http://localhost:8080/portal")
    monkeypatch.setenv("OPENFOAM_MCP_ALLOWED_CASE_ROOTS", str(tmp_path))

    result = openfoam_run_workflow_from_prompt(
        RunWorkflowFromPromptInput(
            description="模拟水在直径5cm、长度50cm的管道中以1m/s流动",
            run_mesh=False,
            run_solver=False,
            auto_apply_stability_fixes=True,
            response_format="json",
        )
    )
    payload = json.loads(result)

    auto_case_path = Path(payload["case_path"])
    assert auto_case_path.is_absolute()
    assert auto_case_path.exists()
    assert auto_case_path.is_dir()
    assert str(auto_case_path).startswith(str(tmp_path))
    assert payload["delivery_url"].startswith("http://localhost:8080/portal/")
