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


def test_history_file_name_and_location(tmp_path):
    path = engagement.history_path()
    assert path.name == "shell_history"
    assert path.parent == tmp_path / "home"


def test_shell_parses_catalog_once_not_per_command(cmd, monkeypatch):
    from grimoire import catalog

    calls = []
    real = catalog._read_yaml
    monkeypatch.setattr(catalog, "_read_yaml", lambda p: (calls.append(p.name), real(p))[1])
    for line in ("list", "info nmap", "search whois", "info whois", "list -c web", "search zzzz"):
        cli.dispatch_line(cmd, line)
    cli._build_completer()  # what `shell` does on startup
    n_files = len(list(catalog.default_catalog_dir().glob("*.yaml")))
    assert len(calls) == n_files  # every file parsed exactly once across all of the above


# =====================  shell upgrade: prompt, toolbar, completion, history, shortcut  =====================
from pathlib import Path  # noqa: E402

SCOPE = {"authorization_ref": "ACME-42", "targets": "192.0.2.0/24", "ts": "2026-01-01T00:00:00+00:00"}


def plain(fragments):
    return "".join(text for _style, text in fragments)


def test_prompt_without_and_with_scope():
    assert plain(cli.prompt_fragments(None)) == "grimoire ❯ "
    assert plain(cli.prompt_fragments(SCOPE)) == "grimoire[ACME-42] ❯ "
    # real style fragments, not raw ANSI escape codes
    assert all("\x1b" not in text for _s, text in cli.prompt_fragments(SCOPE))
    assert {style for style, _ in cli.prompt_fragments(SCOPE)} == {"class:prompt", "class:scope", "class:arrow"}


def test_prompt_cannot_be_broken_by_markup_like_refs():
    weird = {"authorization_ref": "[bold]x</b>", "targets": "t"}
    assert plain(cli.prompt_fragments(weird)) == "grimoire[[bold]x</b>] ❯ "


def test_toolbar_text_is_a_pure_function():
    with_scope = cli.toolbar_text(SCOPE, 719, 143)
    assert "scope: ACME-42 → 192.0.2.0/24" in with_scope
    assert "719 tools" in with_scope and "143 installed" in with_scope
    none = cli.toolbar_text(None, 9, 5)
    assert "no scope set" in none and "9 tools" in none and "5 installed" in none
    assert cli.toolbar_text(SCOPE, 1, 1) == cli.toolbar_text(dict(SCOPE), 1, 1)  # deterministic


def test_toolbar_truncates_long_targets():
    long = {"authorization_ref": "R", "targets": "10.0.0.0/8," * 30}
    text = cli.toolbar_text(long, 1, 1)
    assert "…" in text and len(text) < 140


def test_welcome_lines():
    assert cli.welcome_lines("1.2.3", 719, 143, SCOPE) == [
        "grimoire 1.2.3", "719 tools · 143 installed", "scope: ACME-42 → 192.0.2.0/24",
    ]
    assert cli.welcome_lines("1.2.3", 9, 5, None)[2] == "no scope set"


# ---- bare-tool shortcut ----------------------------------------------------------
def test_bare_tool_name_shows_info_without_auditing(cmd, tmp_path, capsys):
    assert cli.dispatch_line(cmd, "nmap") is True
    out = capsys.readouterr().out
    assert "Examples" in out and "Network discovery" in out
    assert audit(tmp_path) == []  # read-only: no audit line


def test_bare_tool_name_is_case_insensitive(cmd, capsys):
    assert cli.dispatch_line(cmd, "NMAP") is True
    assert "Examples" in capsys.readouterr().out


def test_unknown_token_is_still_bad_input(cmd, tmp_path, capsys):
    assert cli.dispatch_line(cmd, "frobnicate") is True
    (rec,) = audit(tmp_path)
    assert rec["outcome"] == "bad_input" and rec["exit_code"] == 2 and rec["tool"] == "frobnicate"
    assert "frobnicate" in capsys.readouterr().out


def test_known_commands_are_not_shadowed(cmd, tmp_path):
    # `list` etc. keep their meaning; a tool name only applies to non-commands
    assert cli.dispatch_line(cmd, "list") is True
    assert audit(tmp_path) == []


def test_tool_name_with_extra_args_is_not_executed(cmd, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert cli.dispatch_line(cmd, "nmap -sV 192.0.2.10") is True
    out = capsys.readouterr().out
    assert "run nmap" in out  # tip pointing at `run`
    assert [r["outcome"] for r in audit(tmp_path)] == ["bad_input"]  # existing behavior kept


# ---- history ---------------------------------------------------------------------
def write_history(path, commands):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"\n# 2026-01-01 00:00:{i % 60:02d}.000000\n+{c}\n" for i, c in enumerate(commands)))


def test_history_entries_parse_filehistory_format(tmp_path):
    p = tmp_path / "h"
    p.write_text("\n# t1\n+list\n\n# t2\n+run nmap\n+  --dry-run\n\n# t3\n+info nmap\n")
    assert cli.history_entries(p) == ["list", "run nmap\n  --dry-run", "info nmap"]
    assert cli.history_entries(tmp_path / "missing") == []


def test_history_command_prints_last_20_numbered(cmd, capsys):
    write_history(engagement.history_path(), [f"cmd{i}" for i in range(1, 31)])
    assert cli.dispatch_line(cmd, "history") is True
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 20
    assert lines[0].split() == ["11", "cmd11"] and lines[-1].split() == ["30", "cmd30"]


def test_history_with_few_entries_and_empty(cmd, capsys, tmp_path):
    assert cli.dispatch_line(cmd, "history") is True
    assert "No history" in capsys.readouterr().out
    write_history(engagement.history_path(), ["a", "b"])
    cli.dispatch_line(cmd, "history")
    assert [ln.split() for ln in capsys.readouterr().out.splitlines() if ln.strip()] == [["1", "a"], ["2", "b"]]


def test_history_is_read_only_and_not_audited(cmd, tmp_path):
    write_history(engagement.history_path(), ["list"])
    cli.dispatch_line(cmd, "history")
    assert audit(tmp_path) == []


# ---- completion ------------------------------------------------------------------
def completions(completer, text):
    return list(completer.get_completions(Document(text), CompleteEvent()))


def make_tools():
    return {
        "nmap": Tool(name="nmap", category="information-gathering", binary="nmap", description="d"),
        "burpsuite": Tool(name="burpsuite", category="web", binary="burpsuite", description="d"),
        "zed": Tool(name="zed", category="zzz-new-category", binary="zed", description="d"),
    }


def test_tool_completions_carry_category_and_install_meta():
    c = cli._build_completer(make_tools(), {"nmap": True, "burpsuite": False, "zed": False})
    got = {x.text: x.display_meta_text for x in completions(c, "info ")}
    assert got["nmap"] == "information-gathering · installed"
    assert got["burpsuite"] == "web · missing"
    run_got = {x.text: x.display_meta_text for x in completions(c, "run ")}
    assert run_got == got
    fuzzy = {x.text for x in completions(c, "info nm")}
    assert "nmap" in fuzzy  # the Fuzzy wrapper still works, and keeps the meta
    assert [x.display_meta_text for x in completions(c, "info nm") if x.text == "nmap"] == ["information-gathering · installed"]


def test_list_dash_c_completes_known_and_catalog_categories():
    c = cli._build_completer(make_tools(), {})
    for flag in ("-c", "--category"):
        names = {x.text for x in completions(c, f"list {flag} ")}
        assert set(KNOWN) <= names  # every known category
        assert "zzz-new-category" in names  # plus categories present in the catalog
    assert [x.text for x in completions(c, "list -c wir")][:1] == ["wireless"]
    meta = {x.text: x.display_meta_text for x in completions(c, "list -c ")}
    assert meta["web"] == "1 tools" and meta["wireless"] == "0 tools"


def test_history_and_other_commands_complete():
    c = cli._build_completer(make_tools(), {})
    assert {x.text for x in completions(c, "hi")} >= {"history"}
    assert {x.text for x in completions(c, "")} >= {"info", "run", "list", "history", "update", "exit"}


# ---- startup: one which() pass, session wiring ---------------------------------------
from grimoire.models import KNOWN_CATEGORY_ORDER as KNOWN, Tool  # noqa: E402


class FakeSession:
    instances = []

    def __init__(self, **kw):
        self.kw = kw
        self.prompts = []
        self.script = iter(["", "help", "   ", "exit"])
        FakeSession.instances.append(self)

    def prompt(self, message, **kw):
        self.prompts.append((message, kw))
        return next(self.script)


@pytest.fixture
def fake_session(monkeypatch):
    import prompt_toolkit

    FakeSession.instances.clear()
    monkeypatch.setattr(prompt_toolkit, "PromptSession", FakeSession)
    return FakeSession


def test_installed_count_is_computed_once_not_per_prompt(fake_session, monkeypatch):
    calls = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: calls.append(name) or None)
    cli.shell()
    (session,) = fake_session.instances
    assert len(session.prompts) == 4  # several prompts/renders happened...
    tools = cli._load()
    assert len(calls) == len(tools)  # ...but which() ran exactly once per cataloged tool, at startup


def test_toolbar_and_prompt_are_precomputed_values_not_callables(fake_session, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    cli.shell()
    (session,) = fake_session.instances
    total = len(cli._load())
    for message, kw in session.prompts:
        assert isinstance(message, list)  # plain fragments: nothing re-evaluated per keystroke
        assert plain(kw["bottom_toolbar"]) == cli.toolbar_text(None, total, total)  # all "installed"


def test_prompt_picks_up_scope_recorded_mid_session(fake_session, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    engagement.save_scope("ACME-42", "192.0.2.0/24")
    cli.shell()
    (session,) = fake_session.instances
    assert plain(session.prompts[0][0]) == "grimoire[ACME-42] ❯ "
    assert "scope: ACME-42" in plain(session.prompts[0][1]["bottom_toolbar"])


def test_session_wiring_autosuggest_keybinding_history_style(fake_session, monkeypatch):
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.keys import Keys

    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    cli.shell()
    kw = fake_session.instances[0].kw
    assert isinstance(kw["auto_suggest"], AutoSuggestFromHistory)
    assert kw["completer"] is not None and kw["style"] is not None
    assert any(b.keys == (Keys.ControlL,) for b in kw["key_bindings"].bindings)
    assert kw["history"].filename == str(engagement.history_path())
    assert oct(engagement.history_path().stat().st_mode & 0o777) == "0o600"


def test_ctrl_l_clears_screen_without_exiting():
    from prompt_toolkit.keys import Keys

    calls = []

    class Renderer:
        def clear(self):
            calls.append("clear")

    class App:
        renderer = Renderer()

    class Event:
        app = App()

    kb = cli._build_key_bindings()
    (binding,) = [b for b in kb.bindings if b.keys == (Keys.ControlL,)]
    binding.handler(Event())
    assert calls == ["clear"]  # clears and returns; it does not exit the app


def test_welcome_banner_is_printed_on_entry(fake_session, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    cli.shell()
    out = capsys.readouterr().out
    assert f"grimoire {cli.__version__}" in out
    assert f"{len(cli._load())} tools · 0 installed" in out
    assert "no scope set" in out and "Type help for commands, exit to quit." in out


def test_keyboard_interrupt_and_eof_behaviour_kept(monkeypatch):
    import prompt_toolkit

    seq = iter([KeyboardInterrupt(), "exit"])

    class Sess(FakeSession):
        def prompt(self, message, **kw):
            item = next(seq)
            if isinstance(item, BaseException):
                raise item
            return item

    monkeypatch.setattr(prompt_toolkit, "PromptSession", Sess)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    cli.shell()  # Ctrl-C cancelled the line and the loop continued to "exit"

    class Eof(FakeSession):
        def prompt(self, message, **kw):
            raise EOFError

    monkeypatch.setattr(prompt_toolkit, "PromptSession", Eof)
    cli.shell()  # Ctrl-D exits cleanly
