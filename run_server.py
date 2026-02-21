#!/usr/bin/env python3
"""Entry point for OpenFOAM MCP Server."""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.server import main

if __name__ == "__main__":
    main()
