"""Shared file IO and command resolution helpers."""

import os
import tempfile
from pathlib import Path
from shutil import which
from typing import Optional


def atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically to avoid partial/corrupted files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_path).replace(path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def resolve_openfoam_command(command: str) -> Optional[str]:
    """Resolve command from PATH first, then FOAM_APPBIN."""
    if "/" in command or "\\" in command or ".." in command:
        return None

    path = which(command)
    if path:
        return path

    foam_appbin = os.environ.get("FOAM_APPBIN")
    if foam_appbin:
        candidate = Path(foam_appbin) / command
        if candidate.is_file():
            return str(candidate)

    return None
