"""Tests for workflow artifact storage helpers."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.web.artifacts import (
    append_job_event,
    cleanup_old_job_dirs,
    create_job_bundle,
    create_job_artifact_dir,
    list_job_artifacts,
    load_job_manifest,
    read_job_event_lines,
    safe_resolve_artifact_path,
    write_job_manifest,
)


def test_create_job_dir_and_manifest(monkeypatch, tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_BASE_URL", "http://localhost:8080/artifacts")

    job_dir = create_job_artifact_dir("job_abc123")
    assert job_dir.exists()
    assert job_dir.is_dir()

    manifest = {"status": "completed", "template_id": "pipe_flow"}
    manifest_path = write_job_manifest("job_abc123", manifest)
    assert manifest_path.exists()
    assert manifest_path.parent == job_dir

    artifacts = list_job_artifacts("job_abc123")
    assert len(artifacts) == 1
    assert artifacts[0]["name"] == "manifest.json"
    assert artifacts[0]["url"] == "http://localhost:8080/artifacts/job_abc123/manifest.json"


def test_safe_resolve_artifact_path_blocks_traversal(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(tmp_path / "artifacts"))

    with pytest.raises(ValueError):
        safe_resolve_artifact_path("../../etc/passwd")


def test_cleanup_old_job_dirs_by_ttl(monkeypatch, tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))

    old_job = create_job_artifact_dir("job_old")
    new_job = create_job_artifact_dir("job_new")
    (old_job / "manifest.json").write_text("{}", encoding="utf-8")
    (new_job / "manifest.json").write_text("{}", encoding="utf-8")

    now = time.time()
    os.utime(old_job, (now - 7200, now - 7200))
    os.utime(new_job, (now, now))

    removed = cleanup_old_job_dirs(ttl_seconds=3600, max_jobs=None)
    assert removed >= 1
    assert not old_job.exists()
    assert new_job.exists()


def test_cleanup_old_job_dirs_by_max_jobs(monkeypatch, tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))

    create_job_artifact_dir("job_1")
    time.sleep(0.01)
    create_job_artifact_dir("job_2")
    time.sleep(0.01)
    create_job_artifact_dir("job_3")

    removed = cleanup_old_job_dirs(ttl_seconds=None, max_jobs=2)
    assert removed == 1
    remaining = sorted([p.name for p in artifact_root.iterdir() if p.is_dir()])
    assert remaining == ["job_2", "job_3"]


def test_create_job_bundle_cleans_partial_file_on_failure(monkeypatch, tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "system").mkdir()
    (case_dir / "system" / "controlDict").write_text("application simpleFoam;\n", encoding="utf-8")

    job_dir = create_job_artifact_dir("job_bundle_fail")
    tmp_bundle = job_dir / ".case_bundle.tar.gz.tmp"
    final_bundle = job_dir / "case_bundle.tar.gz"

    class _BrokenTar:
        def __enter__(self):
            tmp_bundle.write_text("partial", encoding="utf-8")
            raise RuntimeError("boom")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("src.web.artifacts.tarfile.open", lambda *_a, **_k: _BrokenTar())

    with pytest.raises(RuntimeError, match="boom"):
        create_job_bundle("job_bundle_fail", case_dir)

    assert not tmp_bundle.exists()
    assert not final_bundle.exists()


def test_append_job_event_wraps_oserror(monkeypatch, tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))

    def _fail_open(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", _fail_open)

    with pytest.raises(RuntimeError, match="写入作业事件失败"):
        append_job_event("job_event_fail", {"status": "running"})


def test_read_job_event_lines_reads_tail_without_path_read_text(monkeypatch, tmp_path) -> None:
    """Event replay tail read should not rely on full-file Path.read_text()."""
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))

    job_dir = create_job_artifact_dir("job_tail")
    event_path = job_dir / "events.ndjson"
    lines = [f'{{"idx": {idx}}}' for idx in range(1200)]
    event_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _fail_read_text(self, *args, **kwargs):
        raise AssertionError("Path.read_text should not be used for event replay tail")

    monkeypatch.setattr(Path, "read_text", _fail_read_text)

    tail = read_job_event_lines("job_tail", limit=3)
    assert tail == ['{"idx": 1197}', '{"idx": 1198}', '{"idx": 1199}']


def test_load_job_manifest_reports_invalid_json_as_value_error(monkeypatch, tmp_path) -> None:
    """Corrupted manifest JSON should raise a normalized ValueError."""
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(artifact_root))

    job_dir = create_job_artifact_dir("job_bad_manifest")
    (job_dir / "manifest.json").write_text("{bad json", encoding="utf-8")

    with pytest.raises(ValueError, match="manifest"):
        load_job_manifest("job_bad_manifest")
