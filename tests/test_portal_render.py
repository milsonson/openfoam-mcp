"""Tests for portal HTML rendering."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.web.portal import render_portal_html


def test_render_portal_html_contains_key_fields() -> None:
    manifest = {
        "job_id": "job_test_1",
        "status": "completed_with_warnings",
        "template_id": "pipe_flow",
        "solver": "simpleFoam",
        "artifacts": [
            {"name": "case_bundle.tar.gz", "type": "bundle", "url": "http://localhost/artifacts/job/case_bundle.tar.gz"}
        ],
        "kpi_summary": {
            "converged": True,
            "final_residual_fields": ["U", "p"],
        },
        "quality_report": {
            "preflight_overall": "degraded",
            "validation_passed": True,
        },
        "warnings": ["preflight_warnings"],
        "failures": [],
    }

    html = render_portal_html(manifest)
    assert "OpenFOAM Simulation Portal" in html
    assert "job_test_1" in html
    assert "case_bundle.tar.gz" in html
    assert "preflight_warnings" in html
