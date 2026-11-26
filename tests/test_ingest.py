"""Tests for scripts/ingest_kali.py transforms and for curated-over-generated precedence.

No apt/dpkg is invoked: only the pure functions and fixture text are exercised.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

from grimoire.catalog import load_catalog
from grimoire.models import Phase, Tool

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ingest_kali.py"


@pytest.fixture(scope="module")
def ingest():
    spec = importlib.util.spec_from_file_location("ingest_kali", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("tests must not call apt/dpkg")

    monkeypatch.setattr(subprocess, "run", boom)


# ---- mapping ---------------------------------------------------------------
def test_every_mapped_category_is_a_valid_phase(ingest):
    valid = {p.value for p in Phase}
    assert set(ingest.METAPACKAGE_CATEGORIES.values()) <= valid
    assert ingest.C2_PACKAGES  # c2 is reachable only via overrides
    assert "c2" in valid


def test_c2_override(ingest):
    assert ingest.resolve_category("sliver", "post-exploitation") == "c2"
    assert ingest.resolve_category("nmap", "information-gathering") == "information-gathering"


# ---- classifier ------------------------------------------------------------
@pytest.mark.parametrize(
    "pkg,cat,interactive,scope",
    [
        ("metasploit-framework", "exploitation", True, True),
        ("armitage", "exploitation", True, True),
        ("some-new-c2", "c2", True, True),  # anything in c2 is interactive
        ("sqlmap", "database", False, True),
        ("hashcat", "password-attacks", False, True),
        ("whois", "information-gathering", False, False),  # known passive
        ("dnsrecon", "information-gathering", False, True),  # unsure -> true
        ("lynis", "vulnerability-analysis", False, True),  # unsure -> true
        ("ghidra", "reverse-engineering", False, False),
        ("autopsy", "forensics", False, False),
        ("cherrytree", "reporting", False, False),
    ],
)
def test_classify(ingest, pkg, cat, interactive, scope):
    assert ingest.classify(pkg, cat) == (interactive, scope)


# ---- parsers ---------------------------------------------------------------
DEPENDS = """kali-tools-web
  Depends: burpsuite
  Depends: <httpie>
    httpie
 |Depends: gobuster
  Depends: kali-tools-top10
  Depends: burpsuite
  Recommends: ignored
"""

SHOW = """Package: gobuster
Version: 3.6
Homepage: https://github.com/OJ/gobuster
Description: Directory/File, DNS and VHost busting tool
 Longer text that must not be included.
"""

DPKG = """/.
/usr
/usr/bin
/usr/bin/gobuster
/usr/sbin/helper
/usr/share/doc/gobuster
/usr/bin/sub/dir
"""


def test_parse_depends(ingest):
    assert ingest.parse_depends(DEPENDS) == ["burpsuite", "gobuster"]


def test_parse_show(ingest):
    assert ingest.parse_show(SHOW) == {
        "description": "Directory/File, DNS and VHost busting tool",
        "homepage": "https://github.com/OJ/gobuster",
    }


def test_binaries_and_pick(ingest):
    bins = ingest.binaries_from_dpkg(DPKG)
    assert bins == ["gobuster", "helper"]
    assert ingest.pick_binary("gobuster", bins) == "gobuster"
    assert ingest.pick_binary("pkg-name", ["tool-a", "tool-b"]) == "tool-a"
    assert ingest.pick_binary("not-installed", []) == "not-installed"
    assert ingest.pick_binary("metasploit-framework", []) == "msfconsole"


# ---- entry builder ---------------------------------------------------------
def test_entry_is_metadata_only_and_valid(ingest):
    d = ingest.package_metadata_dict(
        "gobuster", "web", "Directory busting tool", "https://example.org/g", ["gobuster"]
    )
    assert d["examples"] == [] and d["common_flags"] == {}
    assert d["install"] == "apt install gobuster"
    assert d["cheatsheet_url"] == "https://example.org/g"
    assert d["source"] == "generated"
    tool = Tool.model_validate(d)  # must satisfy the real model
    assert tool.category is Phase.WEB and tool.requires_auth_scope and not tool.interactive


def test_entry_without_homepage_or_description(ingest):
    d = ingest.package_metadata_dict("foo", "forensics", "", "", [])
    assert "cheatsheet_url" not in d and d["binary"] == "foo" and d["description"]
    Tool.model_validate(d)


def test_write_tree_only_touches_generated(ingest, tmp_path):
    curated = tmp_path / "web.yaml"
    curated.write_text("tools: []\n")
    gen = tmp_path / "generated"
    gen.mkdir()
    (gen / "stale.yaml").write_text("tools: []\n")
    entry = ingest.package_metadata_dict("gobuster", "web", "d", "", [])
    ingest.write_tree({"web": [entry]}, gen)
    assert curated.read_text() == "tools: []\n"
    assert not (gen / "stale.yaml").exists()
    assert yaml.safe_load((gen / "web.yaml").read_text())["tools"][0]["name"] == "gobuster"
    assert load_catalog(tmp_path)["gobuster"].source == "generated"


# ---- load_catalog precedence ----------------------------------------------
def _write(path, name, desc, **extra):
    fields = {"name": name, "category": "web", "binary": name, "description": desc, **extra}
    path.write_text(yaml.safe_dump({"tools": [fields]}))


def test_curated_wins_over_generated(tmp_path):
    (tmp_path / "generated").mkdir()
    _write(tmp_path / "web.yaml", "gobuster", "curated desc", examples=[{"cmd": "gobuster dir", "desc": "x"}])
    _write(tmp_path / "generated" / "web.yaml", "gobuster", "generated desc", source="generated")
    _write(tmp_path / "generated" / "database.yaml", "sqlmap", "only generated")
    tools = load_catalog(tmp_path)
    assert tools["gobuster"].description == "curated desc"
    assert tools["gobuster"].source == "curated" and tools["gobuster"].examples
    assert tools["sqlmap"].source == "generated"
    assert len(tools) == 2


def test_duplicates_within_a_tier_still_raise(tmp_path):
    (tmp_path / "generated").mkdir()
    _write(tmp_path / "a.yaml", "x", "d")
    _write(tmp_path / "b.yaml", "x", "d")
    with pytest.raises(ValueError):
        load_catalog(tmp_path)
    (tmp_path / "b.yaml").unlink()
    _write(tmp_path / "generated" / "a.yaml", "y", "d")
    _write(tmp_path / "generated" / "b.yaml", "y", "d")
    with pytest.raises(ValueError):
        load_catalog(tmp_path)


def test_generated_tier_is_optional(tmp_path):
    _write(tmp_path / "web.yaml", "x", "d")
    assert set(load_catalog(tmp_path)) == {"x"}
