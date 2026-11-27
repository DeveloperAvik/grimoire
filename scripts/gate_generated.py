#!/usr/bin/env python3
"""Pre-commit gate for catalog/generated/. Read-only: never commits or pushes.

Exit 0 = scope gate passed and uncategorized looks clean; exit 1 = do not commit.
Run from the repo root:  python3 scripts/gate_generated.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from ingest_kali import UNSCOPED_CATEGORIES, UNSCOPED_PACKAGES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "catalog" / "generated"
SUSPECT_PREFIXES = ("lib", "python3-", "gnome-", "x11-", "systemd-", "perl-", "ruby-", "golang-",
                    "linux-", "firmware-", "fonts-", "xserver-", "xfce", "kde-", "qt", "gir1.2-")
POLLUTION_LIMIT = 0.10  # more than 10% non-tools in uncategorized => polluted


def main() -> int:
    files = sorted(GEN.glob("*.yaml"))
    if not files:
        print(f"no generated YAML in {GEN}")
        return 1
    tools = []
    for f in files:
        for t in (yaml.safe_load(f.read_text()) or {}).get("tools", []):
            tools.append(t)
    counts = Counter(t["category"] for t in tools)

    print("== per-category counts ==")
    for cat, n in sorted(counts.items()):
        print(f"  {cat:<28}{n}")
    print(f"  {'TOTAL':<28}{len(tools)}")

    offenders = [
        (t["name"], t["category"]) for t in tools
        if t.get("requires_auth_scope") is False
        and t["category"] not in UNSCOPED_CATEGORIES
        and t["name"].lower() not in UNSCOPED_PACKAGES
    ]
    print("\n== scope gate ==")
    if offenders:
        print("FAIL: requires_auth_scope=false outside the allowed set:")
        for name, cat in offenders:
            print(f"  {name}  ({cat})")
    else:
        print("PASS")

    unc = sorted(t["name"] for t in tools if t["category"] == "uncategorized")
    bad = [n for n in unc if n.startswith(SUSPECT_PREFIXES)]
    print(f"\n== uncategorized: {len(unc)} entries, {len(bad)} look like non-tools ==")
    for n in unc:
        print(f"  {n}{'   <-- suspect' if n in bad else ''}")
    polluted = bool(unc) and len(bad) / len(unc) > POLLUTION_LIMIT

    ok = not offenders and not polluted
    if polluted:
        print("\nuncategorized looks POLLUTED: tune the ingest filters before committing.")
    print("\nGATE:", "PASS (safe to run pytest, then commit)" if ok else "FAIL (do not commit)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
