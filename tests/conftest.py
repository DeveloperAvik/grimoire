import pytest

from grimoire import catalog


@pytest.fixture(autouse=True)
def fresh_catalog_cache():
    catalog.clear_cache()
    yield
    catalog.clear_cache()
