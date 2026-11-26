#!/usr/bin/env python3
"""Generate grimoire catalog metadata for the whole kali-linux-everything tool set.

Run this on a Kali box. It is NOT a grimoire runtime dependency.

METADATA ONLY: names, descriptions, binaries, categories, install commands and
homepages come from apt/dpkg. No examples, flags or attack commands are ever
generated; those entries rely on grimoire's tldr/man/--help fallback.

Discovery is dynamic. Metapackages (kali-linux-* and kali-tools-*) are expanded
to a closure starting at kali-linux-everything, with a visited set so cycles
terminate. Tool packages are expanded exactly ONE level from each kali-tools-*
metapackage (direct Depends+Recommends), never apt's transitive --recurse, so
shared libraries cannot flood the catalog:

  kali-linux-everything -> kali-linux-* -> ... -> kali-tools-<suffix>   (closure)
  kali-tools-<suffix>   --Depends+Recommends--> member tools            (category <suffix>)
  tools listed by a kali-linux-* metapackage but by no kali-tools-*     (uncategorized)

Writes catalog/generated/<category>.yaml and nothing else (curated
catalog/*.yaml files are never touched).

Needs: Python 3.11+, PyYAML (preinstalled on Kali), apt-cache, dpkg-query, dpkg.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Callable

import yaml

ROOT_METAPACKAGE = "kali-linux-everything"

# Display order. Keep in sync with grimoire.models.KNOWN_CATEGORY_ORDER
# (a test asserts they are identical; this script stays standalone).
KNOWN_CATEGORY_ORDER = [
    "information-gathering", "vulnerability", "web", "database", "passwords",
    "wireless", "802-11", "bluetooth", "rfid", "sdr", "voip", "fuzzing",
    "exploitation", "sniffing-spoofing", "post-exploitation", "c2",
    "social-engineering", "reverse-engineering", "crypto-stego", "forensics",
    "hardware", "android", "windows-resources", "detect", "identify", "protect",
    "respond", "recover", "reporting", "uncategorized",
]
CATEGORY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Used only when kali-linux-everything cannot be read (not Kali, metapackage absent).
FALLBACK_METAPACKAGES = [
    "kali-tools-information-gathering", "kali-tools-vulnerability", "kali-tools-web",
    "kali-tools-database", "kali-tools-passwords", "kali-tools-wireless",
    "kali-tools-reverse-engineering", "kali-tools-exploitation",
    "kali-tools-sniffing-spoofing", "kali-tools-post-exploitation",
    "kali-tools-forensics", "kali-tools-social-engineering", "kali-tools-reporting",
]

# kali-tools-* lists that are cross-cutting selections, not real categories.
EXCLUDED_SUFFIXES = {"top10"}

# Base-system metapackages: never expanded (they would pull the OS into "uncategorized").
NON_TOOL_METAPACKAGES = {"kali-linux-core", "kali-linux-firmware"}

# Loose (uncategorized) candidates with these prefixes are infrastructure, not tools.
# Not applied to members of kali-tools-* metapackages, which are trusted as listed.
NON_TOOL_PREFIXES = ("lib", "python3-", "linux-", "firmware-", "fonts-", "xserver-")

# Packages recategorised to c2 regardless of the metapackage that lists them.
C2_PACKAGES = {"sliver", "powershell-empire", "starkiller"}

# Frameworks/servers/REPLs: documented and launched directly, never wrapped by `run`.
INTERACTIVE_PACKAGES = {"metasploit-framework", "sliver", "powershell-empire", "starkiller", "armitage"}

# When the package name is not the launcher binary.
BINARY_OVERRIDES = {"metasploit-framework": "msfconsole"}

# requires_auth_scope is TRUE by default ("when unsure, true"). It is false only for
# clearly local / defensive / analysis categories and for the passive-recon allowlist.
UNSCOPED_CATEGORIES = {
    "reverse-engineering", "forensics", "reporting", "crypto-stego", "hardware",
    "windows-resources", "detect", "identify", "protect", "respond", "recover",
}
UNSCOPED_PACKAGES = {"whois", "theharvester", "maltego", "spiderfoot", "recon-ng", "metagoofil"}

BIN_DIRS = {"/usr/bin", "/usr/sbin"}

# read(package, kinds) -> direct dependency names, or None if the package can't be read
Reader = Callable[[str, tuple[str, ...]], "list[str] | None"]


# --------------------------------------------------------------------------
# pure transforms (unit-tested; no subprocess)
# --------------------------------------------------------------------------
def category_sort_key(category: str) -> tuple[int, str]:
    try:
        return (KNOWN_CATEGORY_ORDER.index(category), category)
    except ValueError:
        return (len(KNOWN_CATEGORY_ORDER), category)


def resolve_category(package: str, category: str) -> str:
    return "c2" if package in C2_PACKAGES else category


def classify(package: str, category: str) -> tuple[bool, bool]:
    """Return (interactive, requires_auth_scope)."""
    interactive = package in INTERACTIVE_PACKAGES or category == "c2"
    scope = not (category in UNSCOPED_CATEGORIES or package in UNSCOPED_PACKAGES)
    return interactive, scope


def parse_depends(text: str, kinds: tuple[str, ...] = ("Depends",)) -> list[str]:
    """Package names from `apt-cache depends` output for the given dependency kinds.

    Skips <virtual> names. Keeps metapackage names; callers decide what to do with them.
    """
    wanted = tuple(f"{k}:" for k in kinds)
    names: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("|").strip()
        if not stripped.startswith(wanted):
            continue
        name = stripped.split(":", 1)[1].strip()
        if not name or name.startswith("<"):
            continue
        if name not in names:
            names.append(name)
    return names


def is_metapackage(name: str) -> bool:
    return name.startswith(("kali-linux-", "kali-tools-"))


def discover(read: Reader) -> tuple[dict[str, str], list[str]]:
    """Map package -> category, starting from kali-linux-everything.

    Metapackages are expanded to a fixpoint (visited set; cycles are harmless).
    Category comes only from kali-tools-<suffix> membership, which is read one
    level (Depends+Recommends). Returns (mapping, warnings).
    """
    warnings: list[str] = []
    visited: set[str] = set()
    queue: deque[str] = deque()
    members: dict[str, list[str]] = {}  # tool -> kali-tools suffixes listing it
    loose: list[str] = []  # tools listed by kali-linux-* metapackages

    def enqueue(name: str) -> None:
        if name in visited or name in NON_TOOL_METAPACKAGES:
            return
        if name.startswith("kali-tools-"):
            suffix = name.removeprefix("kali-tools-")
            if suffix in EXCLUDED_SUFFIXES:
                return
            if not CATEGORY_RE.match(suffix):
                visited.add(name)
                warnings.append(f"warning: {name} gives an unusable category name; skipping")
                return
        visited.add(name)
        queue.append(name)

    def absorb(meta: str, deps: list[str]) -> None:
        for dep in deps:
            if is_metapackage(dep):
                enqueue(dep)
            elif dep.startswith("kali-"):
                continue  # kali-defaults, kali-desktop-*, ...: not tools
            elif meta.startswith("kali-tools-"):
                members.setdefault(dep, []).append(meta.removeprefix("kali-tools-"))
            else:
                loose.append(dep)

    root = read(ROOT_METAPACKAGE, ("Depends",))
    if root is None:
        warnings.append(
            f"!!! WARNING: cannot read {ROOT_METAPACKAGE} (not a Kali system, or the metapackage "
            "is absent). Falling back to the enumerated kali-tools-* list; tools outside those "
            "metapackages will be MISSING from the catalog. !!!"
        )
        for meta in FALLBACK_METAPACKAGES:
            enqueue(meta)
    else:
        visited.add(ROOT_METAPACKAGE)
        absorb(ROOT_METAPACKAGE, root)

    while queue:
        meta = queue.popleft()
        kinds = ("Depends", "Recommends") if meta.startswith("kali-tools-") else ("Depends",)
        deps = read(meta, kinds)
        if deps is None:
            warnings.append(f"warning: could not read {meta}; skipping")
            continue
        absorb(meta, deps)

    mapping: dict[str, str] = {}
    for pkg, suffixes in members.items():  # dedup: first category by display order wins
        mapping[pkg] = resolve_category(pkg, min(suffixes, key=category_sort_key))
    for pkg in loose:  # reachable via kali-linux-* but under no kali-tools-*
        if pkg not in mapping and not pkg.startswith(NON_TOOL_PREFIXES):
            mapping[pkg] = resolve_category(pkg, "uncategorized")
    return mapping, warnings


def parse_show(text: str) -> dict[str, str]:
    """description (first line) and homepage from one `apt-cache show` stanza."""
    out = {"description": "", "homepage": ""}
    for line in text.splitlines():
        if line.startswith(("Description:", "Description-en:")) and not out["description"]:
            out["description"] = line.split(":", 1)[1].strip()
        elif line.startswith("Homepage:") and not out["homepage"]:
            out["homepage"] = line.split(":", 1)[1].strip()
        if out["description"] and out["homepage"]:
            break
    return out


def parse_show_many(text: str) -> dict[str, dict[str, str]]:
    """Per-package metadata from multi-package `apt-cache show` output (first stanza wins)."""
    result: dict[str, dict[str, str]] = {}
    for stanza in re.split(r"\n\s*\n", text):
        match = re.search(r"(?m)^Package:\s*(\S+)", stanza)
        if match and match.group(1) not in result:
            result[match.group(1)] = parse_show(stanza)
    return result


def parse_installed(text: str) -> set[str]:
    """Installed package names from `dpkg-query -W -f '${Package} ${db:Status-Abbrev}\\n'`."""
    installed = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("ii"):
            installed.add(parts[0].split(":")[0])
    return installed


def binaries_from_dpkg(text: str) -> list[str]:
    """Executable names directly in /usr/bin or /usr/sbin from `dpkg -L` output."""
    found = []
    for line in text.splitlines():
        parent, _, base = line.strip().rpartition("/")
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
    package: str, category: str, description: str, homepage: str, binaries: list[str]
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
def _run(args: list[str], timeout: int = 60) -> str | None:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def apt_depends(package: str, kinds: tuple[str, ...]) -> list[str] | None:
    args = ["apt-cache", "depends", "--no-suggests", "--no-conflicts", "--no-breaks",
            "--no-replaces", "--no-enhances"]
    if "Recommends" not in kinds:
        args.append("--no-recommends")
    out = _run([*args, package])
    return None if out is None else parse_depends(out, kinds)


def show_metadata(packages: list[str], chunk: int = 100) -> dict[str, dict[str, str]]:
    """Batched `apt-cache show`; falls back to one-by-one if a chunk contains an unknown name."""
    result: dict[str, dict[str, str]] = {}
    for i in range(0, len(packages), chunk):
        part = packages[i : i + chunk]
        out = _run(["apt-cache", "show", *part])
        if out is not None:
            result.update(parse_show_many(out))
            continue
        for pkg in part:
            single = _run(["apt-cache", "show", pkg])
            if single:
                result.update(parse_show_many(single))
    return result


def installed_packages() -> set[str]:
    out = _run(["dpkg-query", "-W", "-f", r"${Package} ${db:Status-Abbrev}\n"])
    return parse_installed(out) if out else set()


def write_tree(entries_by_category: dict[str, list[dict]], out_dir: Path) -> None:
    """Replace the generated tree. Only touches *.yaml directly inside out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.yaml"):
        old.unlink()
    for category, entries in entries_by_category.items():
        if not CATEGORY_RE.match(category):  # category becomes a filename
            raise ValueError(f"unsafe category name {category!r}")
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

    mapping, warnings = discover(apt_depends)
    for w in warnings:
        print(w, file=sys.stderr)
    if not mapping:
        print("nothing collected (is this a Kali system with apt-cache?); leaving existing files alone", file=sys.stderr)
        return 1

    packages = sorted(mapping)
    print(f"discovered {len(packages)} packages; reading metadata...", file=sys.stderr)
    meta = show_metadata(packages)
    installed = installed_packages()
    entries: dict[str, list[dict]] = {}
    for pkg in packages:
        info = meta.get(pkg, {"description": "", "homepage": ""})
        files = _run(["dpkg", "-L", pkg]) if pkg in installed else None
        bins = binaries_from_dpkg(files) if files else []
        entries.setdefault(mapping[pkg], []).append(
            package_metadata_dict(pkg, mapping[pkg], info["description"], info["homepage"], bins)
        )
    write_tree(dict(sorted(entries.items(), key=lambda kv: category_sort_key(kv[0]))), args.out)
    print(f"wrote {len(packages)} entries across {len(entries)} categories to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
