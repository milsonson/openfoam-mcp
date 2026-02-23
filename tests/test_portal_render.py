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


def test_render_portal_html_avoids_innerhtml_based_rendering() -> None:
    """Portal script should avoid innerHTML for manifest-driven data rendering."""
    html = render_portal_html({"job_id": "job_xss", "status": "completed"})
    assert "innerHTML" not in html


def test_render_portal_html_validates_download_url_scheme() -> None:
    """Portal should only allow http/https artifact URLs in download links."""
    html = render_portal_html({"job_id": "job_scheme", "status": "completed"})
    assert "function safeDownloadUrl" in html
    assert 'parsed.protocol === "http:" || parsed.protocol === "https:"' in html


def test_render_portal_html_escapes_script_breakout_payload() -> None:
    """Manifest JSON should escape </script> to avoid script-breakout injection."""
    html = render_portal_html(
        {
            "job_id": "job_escape",
            "status": "completed",
            "warnings": ["</script><script>alert(1)</script>"],
        }
    )
    assert "</script><script>alert(1)</script>" not in html
    assert "<\\/script><script>alert(1)<\\/script>" in html
