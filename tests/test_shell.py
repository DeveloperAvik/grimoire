import pytest
import typer
import typer.main
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from grimoire import cli


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)


@pytest.fixture
def cmd():
    return typer.main.get_command(cli.app)


def test_known_command_runs(cmd, capsys):
    assert cli.dispatch_line(cmd, "info nmap") is True
    assert "Examples" in capsys.readouterr().out


def test_unknown_command_handled(cmd, capsys):
    assert cli.dispatch_line(cmd, "frobnicate --now") is True
    assert "frobnicate" in capsys.readouterr().out


def test_typer_exit_does_not_propagate(cmd):
    # `search` with no match raises typer.Exit(1); the shell must survive it
    assert cli.dispatch_line(cmd, "search zzzzqqqq") is True


def test_blank_bad_quote_and_meta(cmd):
    assert cli.dispatch_line(cmd, "   ") is True
    assert cli.dispatch_line(cmd, "info 'unterminated") is True
    assert cli.dispatch_line(cmd, "help") is True
    assert cli.dispatch_line(cmd, "shell") is True
    assert cli.dispatch_line(cmd, "exit") is False
    assert cli.dispatch_line(cmd, "quit") is False


def test_completer_offers_tool_names():
    completer = _completions("info nm")
    assert "nmap" in completer


def _completions(text):
    c = cli._build_completer()
    return [x.text for x in c.get_completions(Document(text), CompleteEvent())]


class Boom:
    def __init__(self, exc):
        self.exc = exc

    def main(self, **kw):
        raise self.exc


@pytest.mark.parametrize("exc", [typer.Abort(), typer.Exit(3), SystemExit(2), RuntimeError("bug")])
def test_any_failure_keeps_shell_alive(exc):
    assert cli.dispatch_line(Boom(exc), "whatever") is True


# --- audit invariant: every run attempt / bad shell line writes exactly one line ---
import json  # noqa: E402

from grimoire import engagement  # noqa: E402


def audit(tmp_path):
    path = tmp_path / "home" / "audit.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_unparseable_line_logs_bad_input(cmd, tmp_path):
    assert cli.dispatch_line(cmd, "run nmap 'unterminated") is True
    (rec,) = audit(tmp_path)
    assert rec["outcome"] == "bad_input" and rec["exit_code"] == 2
    assert rec["tool"] is None and rec["argv"] is None and rec["binary"] is None
    assert rec["dry_run"] is False and rec["scope_ref"] is None
    assert set(rec) == {"ts", "tool", "binary", "argv", "cwd", "scope_ref", "dry_run", "outcome", "exit_code"}


def test_usage_error_logs_bad_input_and_message_is_shown(cmd, tmp_path, capsys):
    # `run` with no tool name fails in the parser, before run()'s body can log anything
    assert cli.dispatch_line(cmd, "run") is True
    out = capsys.readouterr().out
    assert "Missing argument" in out  # format_message still works on this Typer version
    (rec,) = audit(tmp_path)
    assert rec["outcome"] == "bad_input" and rec["tool"] == "run" and rec["argv"] == ["run"]


def test_run_via_shell_writes_exactly_one_line(cmd, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    engagement.record_acceptance()
    engagement.save_scope("T-1", "192.0.2.0/24")
    assert cli.dispatch_line(cmd, "run nmap -sV 192.0.2.10 --dry-run") is True
    (rec,) = audit(tmp_path)
    assert rec["outcome"] == "dry_run"


def test_read_only_commands_write_no_audit_line(cmd, tmp_path):
    for line in ("list", "info nmap", "search whois", "help"):
        cli.dispatch_line(cmd, line)
    assert audit(tmp_path) == []
