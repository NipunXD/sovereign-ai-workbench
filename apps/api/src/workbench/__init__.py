"""Sovereign On-Premise Agentic AI Workbench.

An air-gapped agentic AI platform for confidential industrial work, built for
SIH 2026 problem statement 26117 (MRPL).

Layering (enforced by import-linter, see the root pyproject.toml):

    api -> agent -> tools -> {rag, ingest, artifacts, sandbox, router}
        -> providers -> security -> db -> core

Nothing below a layer may import from above it.
"""

__version__ = "0.1.0"
