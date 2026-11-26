from pathlib import Path

import pytest

from grimoire.catalog import by_phase, load_catalog, search
from grimoire.models import Phase

CATALOG = Path(__file__).resolve().parents[1] / "catalog"


@pytest.fixture(scope="module")
def tools():
    return load_catalog(CATALOG)


def test_loads_and_validates(tools):
    assert "nmap" in tools
    assert tools["nmap"].category is Phase.INFORMATION_GATHERING
    assert tools["nmap"].examples


def test_grouping_in_phase_order(tools):
    phases = list(by_phase(tools))
    assert phases == sorted(phases, key=list(Phase).index)


def test_category_filter(tools):
    assert set(by_phase(tools, Phase.WEB)) == {Phase.WEB}


def test_search_name_and_description(tools):
    assert search(tools, "nmap")[0].name == "nmap"
    assert any(t.name == "eyewitness" for t in search(tools, "screenshots"))


def test_duplicate_detection(tmp_path):
    entry = "tools:\n  - {name: x, category: web, binary: x, description: d}\n"
    (tmp_path / "a.yaml").write_text(entry)
    (tmp_path / "b.yaml").write_text(entry)
    with pytest.raises(ValueError):
        load_catalog(tmp_path)
