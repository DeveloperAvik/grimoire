"""Validated data model for a catalog entry."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Phase(str, Enum):
    """Kill-chain phases, in display order."""

    RECON = "recon"
    SCANNING = "scanning"
    EXPLOITATION = "exploitation"
    POST_EXPLOITATION = "post-exploitation"
    C2 = "c2"
    EXFIL = "exfil"
    REPORTING = "reporting"


PHASE_ORDER = list(Phase)


class Example(BaseModel):
    cmd: str
    desc: str


class Tool(BaseModel):
    name: str
    category: Phase
    binary: str
    install: str = ""
    description: str
    common_flags: dict[str, str] = Field(default_factory=dict)
    examples: list[Example] = Field(default_factory=list)
    cheatsheet_url: str | None = None
    interactive: bool = False
    requires_auth_scope: bool = False
