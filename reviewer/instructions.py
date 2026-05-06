from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InstructionFile:
    """A loaded instruction file (e.g. AGENTS.md, CLAUDE.md)."""

    path: str
    content: str
