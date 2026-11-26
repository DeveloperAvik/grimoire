"""Load and query the YAML catalog (one file per category)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from rapidfuzz import fuzz

try:  # libyaml bindings are ~8x faster; fall back to pure Python when absent
    from yaml import CSafeLoader as SafeLoader
except ImportError:  # pragma: no cover - depends on the PyYAML build
    from yaml import SafeLoader

from .models import Tool, category_sort_key


def default_catalog_dir() -> Path:
    env = os.environ.get("GRIMOIRE_CATALOG")
    if env:
        return Path(env)
    packaged = Path(__file__).parent / "catalog"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "catalog"


def _read_yaml(path: Path) -> dict:
    """Parse one catalog file (the single place YAML is parsed)."""
    return yaml.load(path.read_text(), Loader=SafeLoader) or {}


def _load_tier(paths: list[Path], source: str) -> dict[str, Tool]:
    """Load one tier of YAML files. Duplicate names within a tier are an error."""
    tools: dict[str, Tool] = {}
    for path in paths:
        data = _read_yaml(path)
        for raw in data.get("tools", []):
            tool = Tool.model_validate(raw)
            if source == "generated":
                tool = tool.model_copy(update={"source": source})
            key = tool.name.lower()
            if key in tools:
                raise ValueError(f"duplicate tool {tool.name!r} in {path.name}")
            tools[key] = tool
    return tools


_CACHE: dict[Path, tuple[tuple, dict[str, Tool]]] = {}


def _signature(directory: Path) -> tuple:
    """Changes whenever a catalog file is added, removed or edited."""
    files = [*sorted(directory.glob("*.yaml")), *sorted((directory / "generated").glob("*.yaml"))]
    sig = []
    for f in files:
        st = f.stat()
        sig.append((str(f.relative_to(directory)), st.st_mtime_ns, st.st_size))
    return tuple(sig)


def clear_cache() -> None:
    _CACHE.clear()


def load_catalog(directory: Path | None = None) -> dict[str, Tool]:
    """Return tools keyed by lowercase name. Treat the result as read-only.

    Two tiers: curated (`<dir>/*.yaml`) and generated (`<dir>/generated/*.yaml`).
    A curated entry wins over a generated one with the same name; duplicates
    within a tier raise ValueError.

    Results are memoized per catalog directory and invalidated when any catalog
    file is added, removed or modified, so a long-lived process (the REPL)
    parses once but still sees edits.
    """
    directory = (directory or default_catalog_dir()).resolve()
    sig = _signature(directory)
    cached = _CACHE.get(directory)
    if cached and cached[0] == sig:
        return cached[1]
    tools = _load_tier(sorted(directory.glob("*.yaml")), "curated")
    generated = _load_tier(sorted((directory / "generated").glob("*.yaml")), "generated")
    for key, tool in generated.items():
        tools.setdefault(key, tool)
    _CACHE[directory] = (sig, tools)
    return tools


def by_phase(tools: dict[str, Tool], category: str | None = None) -> dict[str, list[Tool]]:
    """Group tools by category in display order (known first, unknown alphabetical)."""
    wanted = category.strip().lower() if category else None
    grouped: dict[str, list[Tool]] = {}
    for tool in tools.values():
        if wanted and tool.category != wanted:
            continue
        grouped.setdefault(tool.category, []).append(tool)
    return {
        cat: sorted(members, key=lambda t: t.name)
        for cat, members in sorted(grouped.items(), key=lambda kv: category_sort_key(kv[0]))
    }


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
