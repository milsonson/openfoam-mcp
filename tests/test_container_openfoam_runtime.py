"""Container packaging tests for bundled OpenFOAM runtime."""

from __future__ import annotations

from pathlib import Path


def test_dockerfile_installs_openfoam_package() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "openfoam11" in dockerfile


def test_start_script_exports_openfoam_runtime_env() -> None:
    script = Path("docker/start.sh").read_text(encoding="utf-8")
    assert "OPENFOAM_ROOT" in script
    assert "WM_PROJECT_DIR" in script
    assert "FOAM_APPBIN" in script
    assert "LD_LIBRARY_PATH" in script
    assert "FOAM_TUTORIALS" in script


def test_start_script_checks_openfoam_commands() -> None:
    script = Path("docker/start.sh").read_text(encoding="utf-8")
    assert "command -v blockMesh" in script


def test_start_script_uses_system_python_explicitly() -> None:
    script = Path("docker/start.sh").read_text(encoding="utf-8")
    assert "exec /usr/bin/python3 run_server.py" in script
