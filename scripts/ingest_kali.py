#!/usr/bin/env python3
"""Generate grimoire catalog metadata from Kali's metapackages.

Run this on a Kali box. It is NOT a grimoire runtime dependency.

METADATA ONLY: names, descriptions, binaries, categories, install commands and
homepages come from apt/dpkg. No examples, flags or attack commands are ever
generated; those entries rely on grimoire's tldr/man/--help fallback.

Writes catalog/generated/<category>.yaml and nothing else (curated
catalog/*.yaml files are never touched).

Needs: Python 3.11+, PyYAML (preinstalled on Kali), apt-cache, dpkg.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

# kali-tools-* metapackage -> grimoire category (grimoire.models.Phase values).
# Order matters: a package listed in several metapackages keeps its first category.
METAPACKAGE_CATEGORIES: dict[str, str] = {
    "kali-tools-information-gathering": "information-gathering",
    "kali-tools-vulnerability": "vulnerability-analysis",
    "kali-tools-web": "web",
    "kali-tools-database": "database",
    "kali-tools-passwords": "password-attacks",
    "kali-tools-wireless": "wireless",
    "kali-tools-reverse-engineering": "reverse-engineering",
    "kali-tools-exploitation": "exploitation",
    "kali-tools-sniffing-spoofing": "sniffing-spoofing",
    "kali-tools-post-exploitation": "post-exploitation",
    "kali-tools-forensics": "forensics",
    "kali-tools-social-engineering": "social-engineering",
    "kali-tools-reporting": "reporting",
}

# Packages recategorised to c2 regardless of the metapackage that lists them.
C2_PACKAGES = {"sliver", "powershell-empire", "starkiller"}

# Frameworks/servers/REPLs: documented and launched directly, never wrapped by `run`.
INTERACTIVE_PACKAGES = {"metasploit-framework", "sliver", "powershell-empire", "starkiller", "armitage"}

# When the package name is not the launcher binary.
BINARY_OVERRIDES = {"metasploit-framework": "msfconsole"}

# Categories that act on targets -> requires_auth_scope true.
SCOPED_CATEGORIES = {
    "web", "database", "password-attacks", "wireless", "exploitation",
    "sniffing-spoofing", "post-exploitation", "c2", "social-engineering",
}
# Categories that work on local artifacts or documents -> false.
UNSCOPED_CATEGORIES = {"reverse-engineering", "forensics", "reporting"}
# information-gathering / vulnerability-analysis default to true (they probe
# targets) unless the package is a known passive/OSINT or local tool. Heuristic:
# when unsure, true. Extend this set as you review the generated output.
UNSCOPED_PACKAGES = {"whois", "theharvester", "maltego", "spiderfoot", "recon-ng", "metagoofil"}

BIN_DIRS = {"/usr/bin", "/usr/sbin"}


# --------------------------------------------------------------------------
# pure transforms (unit-tested; no subprocess)
# --------------------------------------------------------------------------
def resolve_category(package: str, metapackage_category: str) -> str:
    return "c2" if package in C2_PACKAGES else metapackage_category


def classify(package: str, category: str) -> tuple[bool, bool]:
    """Return (interactive, requires_auth_scope)."""
    interactive = package in INTERACTIVE_PACKAGES or category == "c2"
    if category in UNSCOPED_CATEGORIES or package in UNSCOPED_PACKAGES:
        scope = False
    else:
        scope = True  # SCOPED_CATEGORIES, and the default when unsure
    return interactive, scope


def parse_depends(text: str) -> list[str]:
    """Package names from `apt-cache depends` output (skips <virtual> and kali- metapackages)."""
    names: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("|").strip()
        if not stripped.startswith("Depends:"):
            continue
        name = stripped.split(":", 1)[1].strip()
        if not name or name.startswith("<") or name.startswith("kali-"):
            continue
        if name not in names:
            names.append(name)
    return names


def parse_show(text: str) -> dict[str, str]:
    """description (first line) and homepage from `apt-cache show` output."""
    out = {"description": "", "homepage": ""}
    for line in text.splitlines():
        if line.startswith(("Description:", "Description-en:")) and not out["description"]:
            out["description"] = line.split(":", 1)[1].strip()
        elif line.startswith("Homepage:") and not out["homepage"]:
            out["homepage"] = line.split(":", 1)[1].strip()
        if out["description"] and out["homepage"]:
            break
    return out


def binaries_from_dpkg(text: str) -> list[str]:
    """Executable names directly in /usr/bin or /usr/sbin from `dpkg -L` output."""
    found = []
    for line in text.splitlines():
        path = line.strip()
        parent, _, base = path.rpartition("/")
        if parent in BIN_DIRS and base:
            found.append(base)
    return sorted(set(found))


def pick_binary(package: str, binaries: list[str]) -> str:
    if package in BINARY_OVERRIDES:
        return BINARY_OVERRIDES[package]
    if package in binaries or not binaries:
        return package
    return binaries[0]


def package_metadata_dict(
    package: str,
    category: str,
    description: str,
    homepage: str,
    binaries: list[str],
) -> dict:
    """Build one catalog entry (a dict matching grimoire.models.Tool)."""
    interactive, scope = classify(package, category)
    entry = {
        "name": package,
        "category": category,
        "binary": pick_binary(package, binaries),
        "install": f"apt install {package}",
        "description": description or f"Kali package {package}.",
        "common_flags": {},
        "examples": [],
        "interactive": interactive,
        "requires_auth_scope": scope,
        "source": "generated",
    }
    if homepage:
        entry["cheatsheet_url"] = homepage
    return entry


# --------------------------------------------------------------------------
# apt/dpkg I/O (list args, no shell, timeouts)
# --------------------------------------------------------------------------
def _run(args: list[str], timeout: int = 30) -> str | None:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def depends_of(metapackage: str) -> list[str] | None:
    out = _run([
        "apt-cache", "depends", "--no-recommends", "--no-suggests", "--no-conflicts",
        "--no-breaks", "--no-replaces", "--no-enhances", metapackage,
    ])
    return None if out is None else parse_depends(out)


def collect(package: str, category: str) -> dict:
    show = parse_show(_run(["apt-cache", "show", package]) or "")
    installed_files = _run(["dpkg", "-L", package])  # None when not installed
    binaries = binaries_from_dpkg(installed_files) if installed_files else []
    return package_metadata_dict(package, category, show["description"], show["homepage"], binaries)


def write_tree(entries_by_category: dict[str, list[dict]], out_dir: Path) -> None:
    """Replace the generated tree. Only touches *.yaml directly inside out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.yaml"):
        old.unlink()
    for category, entries in entries_by_category.items():
        entries.sort(key=lambda e: e["name"])
        text = yaml.safe_dump({"tools": entries}, sort_keys=False, allow_unicode=True, width=100)
        (out_dir / f"{category}.yaml").write_text(text)


def main() -> int:
    default_out = Path(__file__).resolve().parents[1] / "catalog" / "generated"
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=default_out, help=f"output dir (default: {default_out})")
    args = ap.parse_args()
    if args.out.name != "generated":
        print("refusing to write: output directory must be named 'generated'", file=sys.stderr)
        return 2

    seen: set[str] = set()
    entries: dict[str, list[dict]] = {}
    for meta, meta_category in METAPACKAGE_CATEGORIES.items():
        packages = depends_of(meta)
        if packages is None:
            print(f"warning: could not read {meta}; skipping", file=sys.stderr)
            continue
        for pkg in packages:
            if pkg in seen:
                continue
            seen.add(pkg)
            category = resolve_category(pkg, meta_category)
            entries.setdefault(category, []).append(collect(pkg, category))
    if not entries:
        print("nothing collected (is this a Kali system with apt-cache?); leaving existing files alone", file=sys.stderr)
        return 1
    write_tree(entries, args.out)
    total = sum(len(v) for v in entries.values())
    print(f"wrote {total} entries across {len(entries)} categories to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
