"""Artifact storage helpers for OpenFOAM workflow outputs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import logging
import os
import re
import shutil
import tarfile
import time
from typing import Any, TypedDict

from .config import load_server_config


_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
logger = logging.getLogger(__name__)
_DEFAULT_SUPPORTED_SUFFIXES = {
    ".json",
    ".csv",
    ".txt",
    ".log",
    ".ndjson",
    ".png",
    ".gif",
    ".mp4",
    ".tar",
    ".gz",
    ".zst",
    ".html",
}
_AUTO_CLEANUP_INTERVAL_SECONDS = 300.0
_last_auto_cleanup_at = 0.0


class ArtifactMetadata(TypedDict):
    """Serializable metadata for a downloadable artifact."""

    name: str
    type: str
    path: str
    url: str
    size_bytes: int
    created_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_root() -> Path:
    return load_server_config().artifact_dir


def ensure_job_id(job_id: str) -> str:
    """Validate a job identifier used in artifact paths."""
    normalized = job_id.strip()
    if not _SAFE_JOB_ID_RE.fullmatch(normalized):
        raise ValueError(
            "job_id 非法，仅允许 [A-Za-z0-9][A-Za-z0-9_-]{0,127}"
        )
    return normalized


def create_job_artifact_dir(job_id: str) -> Path:
    """Create and return a job-specific artifact directory."""
    _auto_cleanup_if_needed()

    safe_job_id = ensure_job_id(job_id)
    job_dir = (_artifact_root() / safe_job_id).resolve()
    root = _artifact_root().resolve()
    if not job_dir.is_relative_to(root):
        raise ValueError("job 目录越界，不在 artifact 根目录内")
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def _auto_cleanup_if_needed() -> None:
    """Periodically enforce TTL/max-jobs cleanup in long-running processes."""
    global _last_auto_cleanup_at

    now = time.monotonic()
    if now - _last_auto_cleanup_at < _AUTO_CLEANUP_INTERVAL_SECONDS:
        return

    cfg = load_server_config()
    cleanup_old_job_dirs(
        ttl_seconds=cfg.artifact_ttl_seconds,
        max_jobs=cfg.artifact_max_jobs,
    )
    _last_auto_cleanup_at = now


def safe_resolve_artifact_path(relative_path: str) -> Path:
    """Resolve a relative artifact path and prevent directory traversal."""
    root = _artifact_root().resolve()
    candidate = (root / relative_path).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValueError("非法路径：超出 artifact 根目录")
    return candidate


def build_artifact_url(relative_path: str) -> str:
    """Build a public artifact URL from a root-relative path."""
    base = load_server_config().artifact_base_url.rstrip("/")
    normalized = relative_path.lstrip("/")
    return f"{base}/{normalized}"


def _artifact_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".tar", ".gz", ".zst"}:
        return "bundle"
    if suffix in {".png", ".gif", ".mp4"}:
        return "preview"
    if suffix == ".csv":
        return "timeseries"
    if suffix == ".json":
        return "json"
    if suffix in {".log", ".txt", ".ndjson"}:
        return "log"
    if suffix == ".html":
        return "html"
    return "file"


def list_job_artifacts(job_id: str) -> list[ArtifactMetadata]:
    """List files under a job artifact directory with metadata."""
    safe_job_id = ensure_job_id(job_id)
    job_dir = (_artifact_root() / safe_job_id).resolve()
    root = _artifact_root().resolve()
    if not job_dir.exists():
        return []

    artifacts: list[ArtifactMetadata] = []
    for path in sorted(job_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        stat = path.stat()
        artifacts.append(
            {
                "name": path.name,
                "type": _artifact_type_for(path),
                "path": rel,
                "url": build_artifact_url(rel),
                "size_bytes": int(stat.st_size),
                "created_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        )
    return artifacts


def write_job_manifest(job_id: str, payload: dict[str, Any]) -> Path:
    """Persist job manifest.json and return its path."""
    import json

    job_dir = create_job_artifact_dir(job_id)
    manifest_path = job_dir / "manifest.json"
    body = dict(payload)
    body.setdefault("generated_at", _utc_now_iso())
    body["job_id"] = ensure_job_id(job_id)
    manifest_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def append_job_event(job_id: str, event: dict[str, Any]) -> Path:
    """Append one JSON event line to events.ndjson for a job."""
    import json

    safe_job_id = ensure_job_id(job_id)
    job_dir = create_job_artifact_dir(safe_job_id)
    event_path = job_dir / "events.ndjson"
    line = json.dumps(event, ensure_ascii=False)
    try:
        with event_path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
        return event_path
    except OSError as exc:
        logger.exception("写入事件失败 job_id=%s file=%s", safe_job_id, event_path)
        raise RuntimeError(f"写入作业事件失败: {exc}") from exc


def read_job_event_lines(job_id: str, limit: int = 500) -> list[str]:
    """Read recent serialized JSON event lines from events.ndjson."""
    safe_job_id = ensure_job_id(job_id)
    event_path = (_artifact_root() / safe_job_id / "events.ndjson").resolve()
    root = _artifact_root().resolve()
    if not event_path.is_relative_to(root):
        raise ValueError("Invalid job path")
    if not event_path.exists():
        return []

    lines = [line.strip() for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit > 0:
        return lines[-limit:]
    return lines


def load_job_manifest(job_id: str) -> dict[str, Any]:
    """Load manifest.json for a job."""
    import json

    manifest_path = (_artifact_root() / ensure_job_id(job_id) / "manifest.json").resolve()
    root = _artifact_root().resolve()
    if not manifest_path.is_relative_to(root):
        raise ValueError("job 路径非法")
    if not manifest_path.exists():
        raise FileNotFoundError("未找到 manifest.json")
    raw = manifest_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("manifest 格式非法")
    return data


def create_job_bundle(job_id: str, case_path: str | Path) -> Path:
    """Create a tar.gz bundle from case directory for download."""
    case_dir = Path(case_path).resolve()
    if not case_dir.exists() or not case_dir.is_dir():
        raise FileNotFoundError(f"案例目录不存在: {case_dir}")

    safe_job_id = ensure_job_id(job_id)
    job_dir = create_job_artifact_dir(safe_job_id)
    bundle_path = job_dir / "case_bundle.tar.gz"
    tmp_bundle_path = job_dir / ".case_bundle.tar.gz.tmp"

    tmp_bundle_path.unlink(missing_ok=True)
    try:
        with tarfile.open(tmp_bundle_path, mode="w:gz") as tar:
            tar.add(case_dir, arcname=case_dir.name)
        tmp_bundle_path.replace(bundle_path)
        logger.info(
            "打包案例成功 job_id=%s case_path=%s bundle=%s",
            safe_job_id,
            case_dir,
            bundle_path,
        )
        return bundle_path
    except Exception:
        tmp_bundle_path.unlink(missing_ok=True)
        bundle_path.unlink(missing_ok=True)
        logger.exception("打包案例失败 job_id=%s case_path=%s", safe_job_id, case_dir)
        raise


def cleanup_old_job_dirs(
    ttl_seconds: int | None,
    max_jobs: int | None,
) -> int:
    """Delete old job directories by TTL and count limit."""
    root = _artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    dirs = [p for p in root.iterdir() if p.is_dir()]
    removed = 0
    now = datetime.now(timezone.utc).timestamp()

    if ttl_seconds is not None:
        for path in list(dirs):
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                continue
            if now - mtime > ttl_seconds:
                shutil.rmtree(path, ignore_errors=True)
                logger.info("清理过期作业目录 path=%s", path)
                removed += 1
                dirs.remove(path)

    if max_jobs is not None and len(dirs) > max_jobs:
        dirs.sort(key=lambda p: p.stat().st_mtime)
        for path in dirs[: len(dirs) - max_jobs]:
            shutil.rmtree(path, ignore_errors=True)
            logger.info("按数量限制清理作业目录 path=%s", path)
            removed += 1

    return removed


def is_supported_artifact_file(path: Path) -> bool:
    """Check if an artifact file suffix is allowed for direct serving."""
    suffix = path.suffix.lower()
    if suffix in _DEFAULT_SUPPORTED_SUFFIXES:
        return True
    # Support double-suffix bundles like .tar.gz
    if path.name.endswith(".tar.gz") or path.name.endswith(".tar.zst"):
        return True
    return False


def touch_path(path: Path) -> None:
    """Refresh mtime for cleanup accounting."""
    now = datetime.now(timezone.utc).timestamp()
    os.utime(path, times=(now, now), follow_symlinks=False)
