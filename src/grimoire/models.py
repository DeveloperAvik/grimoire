"""Validated data model for a catalog entry."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Phase(str, Enum):
    """Kali's tool categories, in rough kill-chain display order."""

    INFORMATION_GATHERING = "information-gathering"
    VULNERABILITY_ANALYSIS = "vulnerability-analysis"
    WEB = "web"
    DATABASE = "database"
    PASSWORD_ATTACKS = "password-attacks"
    WIRELESS = "wireless"
    REVERSE_ENGINEERING = "reverse-engineering"
    EXPLOITATION = "exploitation"
    SNIFFING_SPOOFING = "sniffing-spoofing"
    POST_EXPLOITATION = "post-exploitation"
    FORENSICS = "forensics"
    SOCIAL_ENGINEERING = "social-engineering"
    C2 = "c2"
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
    source: str = "curated"  # "generated" for entries produced by scripts/ingest_kali.py
