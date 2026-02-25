"""Tests for MCP tool metadata quality."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.server as server


def _openfoam_tools():
    tools = asyncio.run(server.mcp.list_tools())
    return [tool for tool in tools if tool.name.startswith("openfoam_")]


def test_openfoam_tools_have_non_empty_description() -> None:
    tools = _openfoam_tools()
    assert tools, "Expected at least one registered openfoam_* MCP tool."

    missing = [tool.name for tool in tools if not (tool.description or "").strip()]
    assert not missing, (
        "The following tools need meaningful description metadata: "
        + ", ".join(sorted(missing))
    )

