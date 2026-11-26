"""Load and query the YAML catalog (one file per category)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from .models import PHASE_ORDER, Phase, Tool


def default_catalog_dir() -> Path:
    env = os.environ.get("GRIMOIRE_CATALOG")
    if env:
        return Path(env)
    packaged = Path(__file__).parent / "catalog"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "catalog"


def load_catalog(directory: Path | None = None) -> dict[str, Tool]:
    """Return tools keyed by lowercase name. Raises ValueError on duplicates."""
    directory = directory or default_catalog_dir()
    tools: dict[str, Tool] = {}
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        for raw in data.get("tools", []):
            tool = Tool.model_validate(raw)
            key = tool.name.lower()
            if key in tools:
                raise ValueError(f"duplicate tool {tool.name!r} in {path.name}")
            tools[key] = tool
    return tools


def by_phase(tools: dict[str, Tool], category: Phase | None = None) -> dict[Phase, list[Tool]]:
    grouped: dict[Phase, list[Tool]] = {}
    for phase in PHASE_ORDER:
        if category and phase != category:
            continue
        members = sorted((t for t in tools.values() if t.category == phase), key=lambda t: t.name)
        if members:
            grouped[phase] = members
    return grouped


def search(tools: dict[str, Tool], keyword: str, limit: int = 10, cutoff: int = 60) -> list[Tool]:
    """Fuzzy search over names (weighted higher) and descriptions."""
    kw = keyword.lower()
    scored: list[tuple[float, Tool]] = []
    for tool in tools.values():
        name_score = fuzz.WRatio(kw, tool.name.lower())
        desc_score = 100.0 if kw in tool.description.lower() else fuzz.partial_ratio(kw, tool.description.lower()) * 0.8
        score = max(name_score, desc_score)
        if score >= cutoff:
            scored.append((score, tool))
    scored.sort(key=lambda s: (-s[0], s[1].name))
    return [t for _, t in scored[:limit]]
