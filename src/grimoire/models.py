"""Validated data model for a catalog entry."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# Display order, roughly kill-chain. Categories are open strings (Kali's
# kali-tools-<suffix> names); ones not listed here sort after these, alphabetically.
KNOWN_CATEGORY_ORDER = [
    "information-gathering",
    "vulnerability",
    "web",
    "database",
    "passwords",
    "wireless",
    "802-11",
    "bluetooth",
    "rfid",
    "sdr",
    "voip",
    "fuzzing",
    "exploitation",
    "sniffing-spoofing",
    "post-exploitation",
    "c2",
    "social-engineering",
    "reverse-engineering",
    "crypto-stego",
    "forensics",
    "hardware",
    "android",
    "windows-resources",
    "detect",
    "identify",
    "protect",
    "respond",
    "recover",
    "reporting",
    "uncategorized",
]

# Safe for filenames and CLI flags: lowercase words joined by single hyphens.
CATEGORY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def category_sort_key(category: str) -> tuple[int, str]:
    """Known categories in KNOWN_CATEGORY_ORDER, then unknown ones alphabetically."""
    try:
        return (KNOWN_CATEGORY_ORDER.index(category), category)
    except ValueError:
        return (len(KNOWN_CATEGORY_ORDER), category)


class Example(BaseModel):
    cmd: str
    desc: str


class Tool(BaseModel):
    name: str
    category: str
    binary: str
    install: str = ""
    description: str
    common_flags: dict[str, str] = Field(default_factory=dict)
    examples: list[Example] = Field(default_factory=list)
    cheatsheet_url: str | None = None
    interactive: bool = False
    requires_auth_scope: bool = False
    source: str = "curated"  # "generated" for entries produced by scripts/ingest_kali.py

    @field_validator("category")
    @classmethod
    def _valid_category(cls, value: str) -> str:
        value = value.strip().lower()
        if not CATEGORY_RE.match(value):
            raise ValueError(f"invalid category {value!r}: use lowercase words joined by hyphens")
        return value
