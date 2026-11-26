import json
import subprocess

import pytest
from typer.testing import CliRunner

from grimoire import cli, engagement

runner = CliRunner()


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    def boom(*a, **k):
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(subprocess, "run", boom)
    return tmp_path / "home"


def audit_lines(home):
    path = home / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def installed(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")


def accept_and_scope():
    engagement.record_acceptance()
    engagement.save_scope("TICKET-1", "192.0.2.0/24")


def test_banner_gates_first_run(sandbox):
    result = runner.invoke(cli.app, ["run", "nmap", "--dry-run"], input="no\n")
    assert result.exit_code == 1
    assert not engagement.has_accepted()
    (rec,) = audit_lines(sandbox)
    assert rec["outcome"] == "banner_declined" and rec["exit_code"] == 1
    assert rec["tool"] == "nmap" and rec["binary"] is None and rec["scope_ref"] is None
    assert rec["argv"] == ["nmap"] and rec["dry_run"] is True

    result = runner.invoke(cli.app, ["run", "whois", "--dry-run", "-y"], input="I AGREE\n")
    assert engagement.has_accepted()


def test_yes_flag_never_accepts_banner(sandbox):
    result = runner.invoke(cli.app, ["run", "nmap", "-y", "--dry-run"], input="\n")
    assert result.exit_code == 1
    assert not engagement.has_accepted()
    assert [r["outcome"] for r in audit_lines(sandbox)] == ["banner_declined"]


def test_dry_run_logs_and_does_not_execute(sandbox, monkeypatch):
    installed(monkeypatch)
    accept_and_scope()
    result = runner.invoke(cli.app, ["run", "nmap", "-sV", "192.0.2.10", "--dry-run"])
    assert result.exit_code == 0
    assert "nmap -sV 192.0.2.10" in result.output
    assert "[dry-run] not executed" in result.output
    (rec,) = audit_lines(sandbox)
    assert rec["outcome"] == "dry_run"
    assert rec["argv"] == ["nmap", "-sV", "192.0.2.10"]
    assert rec["dry_run"] is True and rec["scope_ref"] == "TICKET-1"


def test_interactive_tool_refused(sandbox, monkeypatch, tmp_path):
    cat = tmp_path / "cat"
    cat.mkdir()
    (cat / "x.yaml").write_text(
        "tools:\n  - {name: fw, category: c2, binary: fw, description: d, interactive: true}\n"
    )
    monkeypatch.setenv("GRIMOIRE_CATALOG", str(cat))
    installed(monkeypatch)
    engagement.record_acceptance()
    result = runner.invoke(cli.app, ["run", "fw", "-y"])
    assert result.exit_code == 0
    assert "directly" in result.output
    (rec,) = audit_lines(sandbox)
    assert rec["outcome"] == "refused_interactive"


def test_not_installed_exits_1(sandbox):
    engagement.record_acceptance()
    result = runner.invoke(cli.app, ["run", "nmap", "-y"])
    assert result.exit_code == 1
    assert "apt install" in result.output
    (rec,) = audit_lines(sandbox)
    assert rec["outcome"] == "not_installed" and rec["tool"] == "nmap"


def test_declined_confirmation_is_audited(sandbox, monkeypatch):
    installed(monkeypatch)
    accept_and_scope()
    result = runner.invoke(cli.app, ["run", "nmap", "192.0.2.10"], input="n\n")
    assert result.exit_code == 0
    (rec,) = audit_lines(sandbox)
    assert rec["outcome"] == "aborted"


def test_scope_prompted_once(sandbox, monkeypatch):
    installed(monkeypatch)
    engagement.record_acceptance()
    runner.invoke(cli.app, ["run", "nmap", "--dry-run"], input="CT-9\n10.0.0.0/8\n")
    assert engagement.load_scope()["authorization_ref"] == "CT-9"
    result = runner.invoke(cli.app, ["run", "nmap", "--dry-run"])
    assert result.exit_code == 0 and "CT-9" in result.output


def test_successful_run_is_audited(sandbox, monkeypatch):
    installed(monkeypatch)
    accept_and_scope()
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.invoke(cli.app, ["run", "nmap", "-sV", "192.0.2.10", "-y"])
    assert result.exit_code == 0
    assert calls[0][0] == ["nmap", "-sV", "192.0.2.10"]
    assert "shell" not in calls[0][1] and "capture_output" not in calls[0][1]
    (rec,) = audit_lines(sandbox)
    assert rec["outcome"] == "executed" and rec["exit_code"] == 0 and rec["dry_run"] is False


def test_unknown_tool_audited(sandbox):
    engagement.record_acceptance()
    result = runner.invoke(cli.app, ["run", "zzzzqqqq", "-y"])
    assert result.exit_code == 1
    (rec,) = audit_lines(sandbox)
    assert rec["outcome"] == "not_found" and rec["tool"] == "zzzzqqqq" and rec["binary"] is None


def test_launch_failure_audited(sandbox, monkeypatch):
    installed(monkeypatch)
    accept_and_scope()

    def fail(argv, **kw):
        raise FileNotFoundError("gone")

    monkeypatch.setattr(subprocess, "run", fail)
    result = runner.invoke(cli.app, ["run", "nmap", "-y"])
    assert result.exit_code == 127
    (rec,) = audit_lines(sandbox)
    assert rec["outcome"] == "launch_failed" and rec["exit_code"] == 127


def test_banner_eof_is_declined(sandbox):
    result = runner.invoke(cli.app, ["run", "nmap"], input="")
    assert result.exit_code == 1
    assert [r["outcome"] for r in audit_lines(sandbox)] == ["banner_declined"]


def test_scope_missing_audited(sandbox, monkeypatch):
    installed(monkeypatch)
    engagement.record_acceptance()
    result = runner.invoke(cli.app, ["run", "nmap", "-sV", "192.0.2.10"], input="\n\n")
    assert result.exit_code == 1
    assert engagement.load_scope() is None
    (rec,) = audit_lines(sandbox)
    assert rec["outcome"] == "scope_missing" and rec["exit_code"] == 1
    assert rec["tool"] == "nmap" and rec["binary"] == "nmap" and rec["scope_ref"] is None
    assert rec["argv"] == ["nmap", "-sV", "192.0.2.10"]


def test_scope_eof_is_missing(sandbox, monkeypatch):
    installed(monkeypatch)
    engagement.record_acceptance()
    result = runner.invoke(cli.app, ["run", "nmap"], input="")
    assert result.exit_code == 1
    assert [r["outcome"] for r in audit_lines(sandbox)] == ["scope_missing"]


def test_confirm_eof_is_aborted(sandbox, monkeypatch):
    installed(monkeypatch)
    accept_and_scope()
    result = runner.invoke(cli.app, ["run", "nmap"], input="")
    assert result.exit_code == 0
    assert [r["outcome"] for r in audit_lines(sandbox)] == ["aborted"]


SCHEMA = {"ts", "tool", "binary", "argv", "cwd", "scope_ref", "dry_run", "outcome", "exit_code"}


def test_every_outcome_has_full_schema(sandbox, monkeypatch):
    # banner_declined, not_found, not_installed, dry_run: all must share one key set
    runner.invoke(cli.app, ["run", "nmap"], input="no\n")
    engagement.record_acceptance()
    runner.invoke(cli.app, ["run", "zzzzqqqq", "-y"])
    runner.invoke(cli.app, ["run", "nmap", "-y"])
    installed(monkeypatch)
    accept_and_scope()
    runner.invoke(cli.app, ["run", "nmap", "--dry-run"])
    recs = audit_lines(sandbox)
    assert [r["outcome"] for r in recs] == ["banner_declined", "not_found", "not_installed", "dry_run"]
    assert all(set(r) == SCHEMA for r in recs)
