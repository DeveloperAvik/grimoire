"""Tests for scripts/ingest_kali.py transforms and for curated-over-generated precedence.

No apt/dpkg is invoked: only the pure functions and fixture text are exercised.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

from grimoire.catalog import load_catalog
from grimoire.models import KNOWN_CATEGORY_ORDER, Tool, category_sort_key

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
def test_script_and_model_agree_on_category_order(ingest):
    # the script is standalone, so its copy of the list must not drift
    assert ingest.KNOWN_CATEGORY_ORDER == KNOWN_CATEGORY_ORDER
    assert ingest.category_sort_key("zzz") == category_sort_key("zzz")


def test_fallback_metapackages_are_wellformed(ingest):
    for meta in ingest.FALLBACK_METAPACKAGES:
        suffix = meta.removeprefix("kali-tools-")
        assert meta.startswith("kali-tools-") and suffix in KNOWN_CATEGORY_ORDER


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
        ("hashcat", "passwords", False, True),
        ("whois", "information-gathering", False, False),  # known passive
        ("dnsrecon", "information-gathering", False, True),  # unsure -> true
        ("lynis", "vulnerability", False, True),  # unsure -> true
        ("ghidra", "reverse-engineering", False, False),
        ("autopsy", "forensics", False, False),
        ("cherrytree", "reporting", False, False),
        # wider category set: local/defensive/analysis -> unscoped
        ("binwalk", "crypto-stego", False, False),
        ("chipsec", "hardware", False, False),
        ("evil-winrm-docs", "windows-resources", False, False),
        *[(f"x-{c}", c, False, False) for c in ("detect", "identify", "protect", "respond", "recover")],
        # wireless family, fuzzing, android, uncategorized and unknown categories stay scoped
        *[(f"x-{c}", c, False, True) for c in ("wireless", "802-11", "bluetooth", "rfid", "sdr", "voip")],
        ("afl", "fuzzing", False, True),
        ("apktool", "android", False, True),
        ("mystery", "uncategorized", False, True),
        ("newthing", "brand-new-kali-category", False, True),  # unsure -> true
        ("theharvester", "uncategorized", False, False),  # passive allowlist beats category
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
    # metapackage names are kept (discover() filters them); <virtual> and other kinds are not
    assert ingest.parse_depends(DEPENDS) == ["burpsuite", "gobuster", "kali-tools-top10"]
    assert ingest.parse_depends(DEPENDS, ("Recommends",)) == ["ignored"]
    assert ingest.parse_depends(DEPENDS, ("Depends", "Recommends"))[-1] == "ignored"


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
    assert tool.category == "web" and tool.requires_auth_scope and not tool.interactive


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


# ---- dynamic discovery: metapackage closure from kali-linux-everything ----------------
# Shape: everything -> large -> default -> kali-tools-deep   (two metapackage levels below
# `large`), with a cycle default -> large and default -> everything.
APT = {
    "kali-linux-everything": """kali-linux-everything
  Depends: kali-linux-large
  Depends: kali-tools-web
  Depends: kali-tools-passwords
  Depends: kali-tools-802-11
  Depends: kali-tools-top10
  Depends: kali-tools-newcat
  Depends: kali-tools-ghost
  Depends: kali-tools-post-exploitation
  Depends: <virtual-thing>
  Depends: kali-defaults
  Depends: loose-tool
""",
    "kali-linux-large": """kali-linux-large
  Depends: kali-linux-default
  Depends: kali-tools-sdr
  Depends: extra-tool
  Depends: libfoo1
  Depends: python3-bar
  Depends: loose-tool
""",
    "kali-linux-default": """kali-linux-default
  Depends: kali-linux-large
  Depends: kali-linux-everything
  Depends: kali-linux-core
  Depends: kali-tools-deep
  Depends: default-only-tool
  Depends: kali-defaults
""",
    "kali-tools-deep": "kali-tools-deep\n  Depends: deeptool\n  Depends: gobuster\n",
    "kali-tools-web": """kali-tools-web
  Depends: burpsuite
  Depends: gobuster
  Recommends: ffuf
  Suggests: ignored-suggestion
  Depends: kali-tools-top10
  Depends: kali-tools-nested-in-web
""",
    "kali-tools-nested-in-web": "kali-tools-nested-in-web\n  Depends: nestedtool\n",
    "kali-tools-passwords": """kali-tools-passwords
  Depends: hashcat
  Depends: burpsuite
""",
    "kali-tools-802-11": "kali-tools-802-11\n  Depends: aircrack-ng\n",
    "kali-tools-sdr": "kali-tools-sdr\n  Depends: gqrx-sdr\n",
    "kali-tools-top10": "kali-tools-top10\n  Depends: nmap\n  Depends: hydra\n",
    "kali-tools-newcat": "kali-tools-newcat\n  Depends: newtool\n  Depends: gobuster\n",
    "kali-tools-post-exploitation": "kali-tools-post-exploitation\n  Depends: sliver\n  Depends: mimikatz\n",
    # intentionally absent: kali-tools-ghost (unreadable), kali-linux-core (never expanded)
}


def make_reader(ingest, table):
    calls = []

    def read(pkg, kinds):
        calls.append(pkg)
        text = table.get(pkg)
        return None if text is None else ingest.parse_depends(text, kinds)

    read.calls = calls
    return read


def test_discovery_categories_and_dedup(ingest):
    reader = make_reader(ingest, APT)
    mapping, warnings = ingest.discover(reader)
    assert mapping["gobuster"] == "web"  # also in newcat and deep -> web is first in display order
    assert mapping["ffuf"] == "web"  # Recommends of a kali-tools-* counts
    assert mapping["burpsuite"] == "web"  # also in passwords -> first by display order
    assert mapping["hashcat"] == "passwords"
    assert mapping["aircrack-ng"] == "802-11"  # suffix with digits and hyphen
    assert mapping["newtool"] == "newcat"  # unknown categories are kept, not dropped
    assert mapping["gqrx-sdr"] == "sdr"  # kali-tools-sdr reached via kali-linux-large
    assert mapping["sliver"] == "c2" and mapping["mimikatz"] == "post-exploitation"
    assert "ignored-suggestion" not in mapping


def test_deeply_nested_kali_tools_is_found_and_categorized(ingest):
    mapping, _ = ingest.discover(make_reader(ingest, APT))
    # everything -> large -> default -> kali-tools-deep: two metapackage levels below `large`
    assert mapping["deeptool"] == "deep"  # categorized by its suffix, NOT dumped in uncategorized
    # a kali-tools-* nested inside another kali-tools-* is also followed
    assert mapping["nestedtool"] == "nested-in-web"


def test_discovery_uncategorized_and_filtering(ingest):
    mapping, _ = ingest.discover(make_reader(ingest, APT))
    assert mapping["loose-tool"] == "uncategorized"  # listed by everything, under no kali-tools-*
    assert mapping["extra-tool"] == "uncategorized"  # via kali-linux-large
    assert mapping["default-only-tool"] == "uncategorized"  # via the deeper kali-linux-default
    assert not any(p.startswith(("kali-", "libfoo", "python3-")) for p in mapping)  # metas + noise
    assert "nmap" not in mapping and "hydra" not in mapping  # top10 is a selection, not a category
    assert "virtual-thing" not in mapping and "<virtual-thing>" not in mapping


def test_closure_terminates_on_cycles_and_reads_each_metapackage_once(ingest):
    reader = make_reader(ingest, APT)
    ingest.discover(reader)  # default -> large and default -> everything are cycles
    assert len(reader.calls) == len(set(reader.calls))  # visited set: no metapackage read twice


def test_members_are_expanded_one_level_only(ingest):
    reader = make_reader(ingest, APT)
    ingest.discover(reader)
    assert all(c.startswith("kali-") for c in reader.calls)  # only metapackages are ever read
    for member in ("gobuster", "burpsuite", "deeptool", "loose-tool", "libfoo1"):
        assert member not in reader.calls
    assert "kali-linux-core" not in reader.calls  # base-system metapackage: not expanded
    assert "kali-tools-top10" not in reader.calls  # cross-cutting selection: not expanded


def test_unreadable_metapackages_warn_and_skip(ingest):
    mapping, warnings = ingest.discover(make_reader(ingest, APT))
    assert any("kali-tools-ghost" in w for w in warnings)
    assert not any("kali-linux-core" in w for w in warnings)  # deliberately never read
    assert "hashcat" in mapping and "deeptool" in mapping  # everything else still processed


def test_fallback_when_everything_is_unreadable(ingest):
    table = {k: v for k, v in APT.items() if k != "kali-linux-everything"}
    mapping, warnings = ingest.discover(make_reader(ingest, table))
    assert warnings[0].startswith("!!! WARNING") and "kali-linux-everything" in warnings[0]
    assert mapping["gobuster"] == "web" and mapping["hashcat"] == "passwords"
    assert "loose-tool" not in mapping  # only the enumerated metapackages, no uncategorized
    assert "aircrack-ng" not in mapping  # 802-11 is not in the fallback list


def test_discovery_with_nothing_readable(ingest):
    mapping, warnings = ingest.discover(lambda pkg, kinds: None)
    assert mapping == {} and warnings


def test_bad_category_name_is_not_used_as_filename(ingest, tmp_path):
    with pytest.raises(ValueError):
        ingest.write_tree({"../evil": []}, tmp_path / "generated")
    mapping, warnings = ingest.discover(
        make_reader(ingest, {"kali-linux-everything": "x\n  Depends: kali-tools-Bad_Name\n"})
    )
    assert mapping == {} and any("unusable category" in w for w in warnings)


def test_parse_show_many_and_installed(ingest):
    text = """Package: aaa
Description: First tool
 more
Homepage: https://a.example

Package: aaa
Description: older stanza

Package: bbb
Description-en: Second tool
"""
    got = ingest.parse_show_many(text)
    assert got["aaa"] == {"description": "First tool", "homepage": "https://a.example"}
    assert got["bbb"]["description"] == "Second tool" and got["bbb"]["homepage"] == ""
    assert ingest.parse_installed("nmap ii \nhydra rc \nlibx:amd64 ii \n") == {"nmap", "libx"}
