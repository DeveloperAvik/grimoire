import subprocess

import pytest
from typer.testing import CliRunner

from grimoire import cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_real_binaries(monkeypatch):
    # nothing is installed, and no subprocess may ever run
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    def boom(*a, **k):
        raise AssertionError("subprocess must not run in tests")

    monkeypatch.setattr(subprocess, "run", boom)


def test_list_exits_zero():
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0
    assert "nmap" in result.output


def test_info_seeded_tool():
    result = runner.invoke(cli.app, ["info", "nmap"])
    assert result.exit_code == 0
    assert "Examples" in result.output


def test_info_typo_and_search_miss():
    assert runner.invoke(cli.app, ["info", "nmapp"]).exit_code == 0
    assert runner.invoke(cli.app, ["search", "zzzzqqqq"]).exit_code == 1
