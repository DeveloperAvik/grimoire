from pathlib import Path

import pytest

from grimoire.catalog import by_phase, load_catalog, search
from grimoire.models import KNOWN_CATEGORY_ORDER, Tool, category_sort_key

CATALOG = Path(__file__).resolve().parents[1] / "catalog"


@pytest.fixture(scope="module")
def tools():
    return load_catalog(CATALOG)


def test_loads_and_validates(tools):
    assert "nmap" in tools
    assert tools["nmap"].category == "information-gathering"
    assert tools["nmap"].examples


def test_grouping_in_phase_order(tools):
    cats = list(by_phase(tools))
    assert cats == sorted(cats, key=category_sort_key)


def test_category_filter(tools):
    assert set(by_phase(tools, "web")) == {"web"}
    assert set(by_phase(tools, "WEB")) == {"web"}


def test_search_name_and_description(tools):
    assert search(tools, "nmap")[0].name == "nmap"
    assert any(t.name == "eyewitness" for t in search(tools, "screenshots"))


def test_duplicate_detection(tmp_path):
    entry = "tools:\n  - {name: x, category: web, binary: x, description: d}\n"
    (tmp_path / "a.yaml").write_text(entry)
    (tmp_path / "b.yaml").write_text(entry)
    with pytest.raises(ValueError):
        load_catalog(tmp_path)


def _tool(name, category):
    return Tool(name=name, category=category, binary=name, description="d")


def test_unknown_categories_sort_after_known_alphabetically():
    tools = {t.name: t for t in [
        _tool("a", "zzz-new"), _tool("b", "web"), _tool("c", "aaa-new"),
        _tool("d", "information-gathering"), _tool("e", "uncategorized"),
    ]}
    assert list(by_phase(tools)) == ["information-gathering", "web", "uncategorized", "aaa-new", "zzz-new"]


def test_known_order_is_kill_chain_ish_and_unique():
    assert len(set(KNOWN_CATEGORY_ORDER)) == len(KNOWN_CATEGORY_ORDER)
    assert KNOWN_CATEGORY_ORDER[0] == "information-gathering"
    assert KNOWN_CATEGORY_ORDER.index("c2") < KNOWN_CATEGORY_ORDER.index("reporting")
    assert KNOWN_CATEGORY_ORDER[-1] == "uncategorized"


@pytest.mark.parametrize("good", ["802-11", "sdr", "windows-resources", "Web"])
def test_category_accepts_kali_suffixes(good):
    assert _tool("x", good).category == good.lower()


@pytest.mark.parametrize("bad", ["", "has space", "../etc", "a--b", "-lead", "under_score"])
def test_category_rejects_unsafe_values(bad):
    with pytest.raises(ValueError):
        _tool("x", bad)


# ---- memoization ------------------------------------------------------------
import os  # noqa: E402

from grimoire import catalog as catalog_mod  # noqa: E402


@pytest.fixture
def counting(monkeypatch):
    calls = []
    real = catalog_mod._read_yaml

    def spy(path):
        calls.append(path.name)
        return real(path)

    monkeypatch.setattr(catalog_mod, "_read_yaml", spy)
    return calls


def _mk(tmp_path, name="web.yaml", tool="x"):
    (tmp_path / name).write_text(
        f"tools:\n  - {{name: {tool}, category: web, binary: {tool}, description: d}}\n"
    )
    return tmp_path / name


def test_repeat_loads_hit_the_cache(tmp_path, counting):
    _mk(tmp_path)
    first = catalog_mod.load_catalog(tmp_path)
    second = catalog_mod.load_catalog(tmp_path)
    assert second is first
    assert counting == ["web.yaml"]  # parsed once, not twice


def test_edit_invalidates_cache(tmp_path, counting):
    f = _mk(tmp_path)
    first = catalog_mod.load_catalog(tmp_path)
    f.write_text(f.read_text().replace("name: x", "name: y").replace("binary: x", "binary: y"))
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))  # guarantee a newer mtime
    second = catalog_mod.load_catalog(tmp_path)
    assert second is not first and set(second) == {"y"}
    assert counting.count("web.yaml") == 2


def test_touch_alone_invalidates(tmp_path, counting):
    f = _mk(tmp_path)
    catalog_mod.load_catalog(tmp_path)
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    catalog_mod.load_catalog(tmp_path)
    assert counting.count("web.yaml") == 2


def test_adding_or_removing_files_invalidates(tmp_path):
    _mk(tmp_path)
    assert set(catalog_mod.load_catalog(tmp_path)) == {"x"}
    extra = _mk(tmp_path, "database.yaml", "z")
    assert set(catalog_mod.load_catalog(tmp_path)) == {"x", "z"}
    extra.unlink()
    assert set(catalog_mod.load_catalog(tmp_path)) == {"x"}
    (tmp_path / "generated").mkdir()
    _mk(tmp_path / "generated", "web.yaml", "g")
    assert set(catalog_mod.load_catalog(tmp_path)) == {"x", "g"}


def test_directories_are_cached_independently(tmp_path, counting):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    _mk(a, tool="one"), _mk(b, tool="two")
    assert set(catalog_mod.load_catalog(a)) == {"one"}
    assert set(catalog_mod.load_catalog(b)) == {"two"}
    assert set(catalog_mod.load_catalog(a)) == {"one"}
    assert len(counting) == 2


def test_uses_libyaml_loader_when_available():
    import yaml

    assert catalog_mod.SafeLoader is getattr(yaml, "CSafeLoader", yaml.SafeLoader)
