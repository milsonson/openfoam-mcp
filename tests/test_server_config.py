"""Tests for cloud-oriented server config parsing."""

from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.web.config import (
    build_default_base_urls,
    load_server_config,
    normalize_public_host,
    parse_transport,
)


def test_parse_transport_accepts_supported_values() -> None:
    assert parse_transport("sse") == "sse"
    assert parse_transport("streamable-http") == "streamable-http"
    assert parse_transport("stdio") == "stdio"


def test_parse_transport_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        parse_transport("websocket")


def test_normalize_public_host_handles_bind_addresses() -> None:
    assert normalize_public_host("0.0.0.0") == "127.0.0.1"
    assert normalize_public_host("::") == "127.0.0.1"
    assert normalize_public_host("example.com") == "example.com"


def test_build_default_base_urls_uses_host_and_port() -> None:
    artifacts_url, portal_url = build_default_base_urls("example.com", 9000)
    assert artifacts_url == "http://example.com:9000/artifacts"
    assert portal_url == "http://example.com:9000/portal"


def test_load_server_config_reads_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENFOAM_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("OPENFOAM_MCP_PORT", "8123")
    monkeypatch.setenv("OPENFOAM_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("OPENFOAM_MCP_ARTIFACT_DIR", str(tmp_path / "artifacts"))

    cfg = load_server_config()
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 8123
    assert cfg.transport == "streamable-http"
    assert cfg.public_host == "127.0.0.1"
    assert cfg.artifact_dir == (tmp_path / "artifacts").resolve()


def test_load_server_config_uses_secure_default_artifact_dir(monkeypatch) -> None:
    monkeypatch.delenv("OPENFOAM_MCP_ARTIFACT_DIR", raising=False)
    cfg = load_server_config()
    app_root = Path("/app")
    # Match runtime logic: prefer /app only when writable.
    if app_root.exists() and os.access(app_root, os.W_OK):
        assert cfg.artifact_dir == Path("/app/artifacts").resolve()
    else:
        assert cfg.artifact_dir == Path("/tmp/openfoam-mcp-artifacts").resolve()
