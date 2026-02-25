"""Container packaging tests for bundled OpenFOAM runtime."""

from __future__ import annotations

from pathlib import Path
import re


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


def test_start_script_includes_dummy_decomposer_library_path() -> None:
    script = Path("docker/start.sh").read_text(encoding="utf-8")
    assert "FOAM_DUMMY_LIB_DIR" in script


def test_start_script_checks_openfoam_commands() -> None:
    script = Path("docker/start.sh").read_text(encoding="utf-8")
    assert "command -v blockMesh" in script


def test_start_script_uses_system_python_explicitly() -> None:
    script = Path("docker/start.sh").read_text(encoding="utf-8")
    assert "exec /usr/bin/python3 run_server.py" in script


def test_start_script_temporarily_disables_nounset_when_sourcing_openfoam() -> None:
    script = Path("docker/start.sh").read_text(encoding="utf-8")
    assert re.search(
        r"set \+e\s*\n\s*set \+u\s*\n\s*# shellcheck source=/dev/null\s*\n\s*source \"\$\{OPENFOAM_BASHRC_PATH\}\"\s*\n\s*source_status=\$\?\s*\n\s*set -u\s*\n\s*set -e",
        script,
    )


def test_start_script_tolerates_openfoam_bashrc_source_failures() -> None:
    script = Path("docker/start.sh").read_text(encoding="utf-8")
    assert 'if [ "${source_status}" -ne 0 ]; then' in script
