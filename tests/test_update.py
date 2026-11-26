"""Tests for `grimoire update`. No real git, pip or network: a fake runner is injected."""

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire import __version__, cli, updater
from grimoire.updater import GitError, Result

runner_cli = CliRunner()


@pytest.fixture(autouse=True)
def no_real_subprocess(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess must not run in tests")

    monkeypatch.setattr(subprocess, "run", boom)


# ---- pure logic ---------------------------------------------------------------
def test_is_dirty_and_changed_paths():
    assert updater.is_dirty("") is False
    assert updater.is_dirty("\n") is False
    porcelain = " M src/grimoire/cli.py\n?? catalog/generated/\nR  old.py -> new.py\nA  tests/x.py\n"
    assert updater.is_dirty(porcelain) is True
    assert updater.changed_paths(porcelain) == [
        "src/grimoire/cli.py", "catalog/generated/", "old.py -> new.py", "tests/x.py",
    ]


def test_commits_behind():
    assert updater.commits_behind("3\n") == 3
    assert updater.commits_behind("0") == 0
    with pytest.raises(ValueError):
        updater.commits_behind("fatal: bad revision")


def test_needs_reinstall():
    assert updater.needs_reinstall(["README.md", "pyproject.toml"]) is True
    assert updater.needs_reinstall(["src/grimoire/cli.py", "catalog/web.yaml"]) is False
    assert updater.needs_reinstall(["docs/pyproject.toml"]) is False  # only the root file counts
    assert updater.needs_reinstall([]) is False


def test_find_repo_root(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "src" / "grimoire"
    pkg.mkdir(parents=True)
    (repo / ".git").mkdir()
    assert updater.find_repo_root(pkg / "cli.py") == repo.resolve()
    assert updater.find_repo_root(pkg) == repo.resolve()


def test_find_repo_root_accepts_worktree_git_file(tmp_path):
    (tmp_path / ".git").write_text("gitdir: /elsewhere\n")
    assert updater.find_repo_root(tmp_path / "x.py") == tmp_path.resolve()


def test_find_repo_root_none_without_git(tmp_path, monkeypatch):
    nogit = tmp_path / "site-packages" / "grimoire"
    nogit.mkdir(parents=True)
    # make sure no ancestor of tmp_path is a repo for this assertion
    assert not any((p / ".git").exists() for p in (nogit, *nogit.parents))
    assert updater.find_repo_root(nogit / "cli.py") is None


# ---- orchestration with a fake runner --------------------------------------------
ROOT = Path("/fake/repo")
OLD, NEW = "a" * 40, "b" * 40


class FakeGit:
    """Maps (git subcommand) -> Result or a callable; records every call."""

    def __init__(self, **responses):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, cmd, timeout):
        self.calls.append(cmd)
        if cmd[0] != "git":
            key = "pip"
        else:
            assert cmd[1:3] == ["-C", str(ROOT)], cmd  # always targets the repo root
            key = cmd[3]
        resp = self.responses.get(key, Result(0, ""))
        if isinstance(resp, Exception):
            raise resp
        if callable(resp):
            return resp(cmd)
        return resp

    def subcommands(self):
        return [c[3] if c[0] == "git" else "pip" for c in self.calls]


def collect():
    lines: list[str] = []
    return lines, lines.append


def revparse_sequence(*values):
    """`rev-parse` is used for the branch name and for HEAD before/after the pull."""
    it = iter(values)
    return lambda cmd: Result(0, next(it) + "\n")


def test_not_a_git_checkout():
    out, echo = collect()
    fake = FakeGit()
    assert updater.run_update(fake, None, False, echo) == 0
    assert fake.calls == []  # nothing is run
    assert "not installed from a git checkout" in " ".join(out) and "pip install --upgrade" in " ".join(out)


def test_dirty_tree_refuses_and_never_fetches_or_pulls():
    out, echo = collect()
    fake = FakeGit(status=Result(0, " M catalog/web.yaml\n?? catalog/generated/\n"))
    assert updater.run_update(fake, ROOT, False, echo) == 1
    assert fake.subcommands() == ["status"]  # no fetch, no pull, nothing else
    text = "\n".join(out)
    assert "catalog/web.yaml" in text and "catalog/generated/" in text and "stash" in text


def test_already_up_to_date():
    out, echo = collect()
    fake = FakeGit(**{"rev-parse": Result(0, "main\n"), "rev-list": Result(0, "0\n")})
    assert updater.run_update(fake, ROOT, False, echo) == 0
    assert "Already up to date" in "\n".join(out)
    assert "pull" not in fake.subcommands() and "pip" not in fake.subcommands()


def test_behind_pulls_and_reinstalls_when_pyproject_changed():
    out, echo = collect()
    fake = FakeGit(
        **{
            "rev-parse": revparse_sequence("main", OLD, NEW),
            "rev-list": Result(0, "2\n"),
            "log": Result(0, "bbbbbbb second\nccccccc first\n"),
            "diff": Result(0, "README.md\npyproject.toml\n"),
        }
    )
    assert updater.run_update(fake, ROOT, False, echo, python="/venv/bin/python") == 0
    assert fake.subcommands() == ["status", "rev-parse", "fetch", "rev-list", "log", "rev-parse", "pull", "rev-parse", "diff", "pip"]
    pull = next(c for c in fake.calls if c[3] == "pull")
    assert pull[4:] == ["--ff-only", "origin", "main"]  # fast-forward only, never merge/reset
    assert fake.calls[-1] == ["/venv/bin/python", "-m", "pip", "install", "-e", str(ROOT)]
    text = "\n".join(out)
    assert "2 new commits" in text and "bbbbbbb second" in text
    assert "aaaaaaa -> bbbbbbb" in text and "Done" in text


def test_behind_skips_reinstall_when_pyproject_unchanged():
    out, echo = collect()
    fake = FakeGit(
        **{
            "rev-parse": revparse_sequence("main", OLD, NEW),
            "rev-list": Result(0, "1\n"),
            "log": Result(0, "bbbbbbb tweak\n"),
            "diff": Result(0, "src/grimoire/cli.py\ncatalog/web.yaml\n"),
        }
    )
    assert updater.run_update(fake, ROOT, False, echo) == 0
    assert "pip" not in fake.subcommands()
    assert "1 new commit " in "\n".join(out) + " "  # singular


def test_ff_only_failure_stops_without_merge_or_reset():
    out, echo = collect()
    fake = FakeGit(
        **{
            "rev-parse": revparse_sequence("main", OLD),
            "rev-list": Result(0, "1\n"),
            "log": Result(0, "bbbbbbb x\n"),
            "pull": Result(128, "", "fatal: Not possible to fast-forward, aborting.\n"),
        }
    )
    assert updater.run_update(fake, ROOT, False, echo) == 1
    assert set(fake.subcommands()) <= {"status", "rev-parse", "fetch", "rev-list", "log", "pull"}
    assert not any(c in fake.subcommands() for c in ("merge", "reset", "checkout", "rebase", "pip"))
    assert "diverged" in "\n".join(out) and "manually" in "\n".join(out)


def test_check_reports_without_pulling():
    out, echo = collect()
    fake = FakeGit(**{"rev-parse": Result(0, "main\n"), "rev-list": Result(0, "2\n"), "log": Result(0, "b x\nc y\n")})
    assert updater.run_update(fake, ROOT, True, echo) == 0
    assert "pull" not in fake.subcommands() and "pip" not in fake.subcommands()
    assert "Update available" in "\n".join(out)


def test_check_on_dirty_tree_still_reports_but_warns():
    out, echo = collect()
    fake = FakeGit(
        status=Result(0, " M x\n"),
        **{"rev-parse": Result(0, "main\n"), "rev-list": Result(0, "1\n"), "log": Result(0, "b x\n")},
    )
    assert updater.run_update(fake, ROOT, True, echo) == 0
    assert "pull" not in fake.subcommands()
    assert "uncommitted" in "\n".join(out)


def test_non_main_branch_warns_but_proceeds():
    out, echo = collect()
    fake = FakeGit(**{"rev-parse": Result(0, "dev\n"), "rev-list": Result(0, "0\n")})
    assert updater.run_update(fake, ROOT, False, echo) == 0
    assert "branch 'dev'" in "\n".join(out)
    rl = next(c for c in fake.calls if c[3] == "rev-list")
    assert rl[-1] == "HEAD..origin/dev"


def test_detached_head_refuses():
    out, echo = collect()
    fake = FakeGit(**{"rev-parse": Result(0, "HEAD\n")})
    assert updater.run_update(fake, ROOT, False, echo) == 1
    assert "detached" in "\n".join(out) and "fetch" not in fake.subcommands()


@pytest.mark.parametrize(
    "failure",
    [OSError("git not found"), subprocess.TimeoutExpired("git", 120), Result(128, "", "fatal: no remote\n")],
)
def test_fetch_failures_are_reported_cleanly(failure):
    out, echo = collect()
    fake = FakeGit(**{"rev-parse": Result(0, "main\n"), "fetch": failure})
    assert updater.run_update(fake, ROOT, False, echo) == 1
    assert "Update failed" in "\n".join(out)
    assert "pull" not in fake.subcommands()


def test_reinstall_failure_is_reported():
    out, echo = collect()
    fake = FakeGit(
        **{
            "rev-parse": revparse_sequence("main", OLD, NEW),
            "rev-list": Result(0, "1\n"),
            "log": Result(0, "b x\n"),
            "diff": Result(0, "pyproject.toml\n"),
            "pip": Result(1, "", "boom"),
        }
    )
    assert updater.run_update(fake, ROOT, False, echo) == 1
    assert "reinstall failed" in "\n".join(out) and "pip install -e" in "\n".join(out)


def test_garbled_rev_list_output_is_reported():
    out, echo = collect()
    fake = FakeGit(**{"rev-parse": Result(0, "main\n"), "rev-list": Result(0, "garbage\n")})
    assert updater.run_update(fake, ROOT, False, echo) == 1
    assert "Update failed" in "\n".join(out)


# ---- CLI wiring ---------------------------------------------------------------
def test_version_flag():
    result = runner_cli.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"grimoire {__version__}"
    assert __version__ and __version__ != "0+unknown"  # installed, so metadata resolves


def test_update_command_and_alias_outside_a_checkout(monkeypatch):
    monkeypatch.setattr(updater, "find_repo_root", lambda start: None)
    for args in (["update"], ["update", "--check"], ["--update"]):
        result = runner_cli.invoke(cli.app, args)
        assert result.exit_code == 0, args
        assert "not installed from a git checkout" in result.output


def test_update_cli_uses_injected_runner_path(monkeypatch):
    fake = FakeGit(status=Result(0, " M a\n"))
    monkeypatch.setattr(updater, "find_repo_root", lambda start: ROOT)
    monkeypatch.setattr(updater, "subprocess_runner", fake)
    result = runner_cli.invoke(cli.app, ["update"])
    assert result.exit_code == 1 and "Refusing to update" in result.output
    assert fake.subcommands() == ["status"]


def test_update_help_lists_check():
    result = runner_cli.invoke(cli.app, ["update", "--help"])
    assert result.exit_code == 0 and "--check" in result.output


# ---- reinstall interpreter ------------------------------------------------------
def behind_with_pyproject_change():
    return FakeGit(
        **{
            "rev-parse": revparse_sequence("main", OLD, NEW),
            "rev-list": Result(0, "1\n"),
            "log": Result(0, "b x\n"),
            "diff": Result(0, "pyproject.toml\n"),
        }
    )


def test_reinstall_defaults_to_sys_executable_not_a_bare_python():
    out, echo = collect()
    fake = behind_with_pyproject_change()
    assert updater.run_update(fake, ROOT, False, echo) == 0  # no python= passed: the default is used
    pip_cmd = fake.calls[-1]
    assert pip_cmd == [sys.executable, "-m", "pip", "install", "-e", str(ROOT)]
    assert pip_cmd[0] not in ("python", "python3", "pip")  # never resolved from PATH
    assert Path(pip_cmd[0]).is_absolute()


def test_reinstall_follows_sys_executable_at_call_time(monkeypatch):
    # resolved when called, not frozen at import time
    monkeypatch.setattr(sys, "executable", "/some/other/venv/bin/python")
    fake = behind_with_pyproject_change()
    updater.run_update(fake, ROOT, False, lambda m: None)
    assert fake.calls[-1][0] == "/some/other/venv/bin/python"


# ---- restart hint inside the shell -------------------------------------------------
def pulled_scenario(diff="src/grimoire/cli.py\n"):
    return FakeGit(
        **{
            "rev-parse": revparse_sequence("main", OLD, NEW),
            "rev-list": Result(0, "1\n"),
            "log": Result(0, "b x\n"),
            "diff": Result(0, diff),
        }
    )


def test_restart_hint_only_after_a_real_pull_in_the_shell():
    out, echo = collect()
    updater.run_update(pulled_scenario(), ROOT, False, echo, in_shell=True)
    assert any("Restart the shell" in line for line in out)

    out, echo = collect()  # same pull, but not in the shell
    updater.run_update(pulled_scenario(), ROOT, False, echo, in_shell=False)
    assert not any("Restart" in line for line in out)

    out, echo = collect()  # in the shell, but nothing was pulled
    fake = FakeGit(**{"rev-parse": Result(0, "main\n"), "rev-list": Result(0, "0\n")})
    updater.run_update(fake, ROOT, False, echo, in_shell=True)
    assert not any("Restart" in line for line in out)

    out, echo = collect()  # --check never pulls
    updater.run_update(pulled_scenario(), ROOT, True, echo, in_shell=True)
    assert not any("Restart" in line for line in out)

    out, echo = collect()  # a refused update pulled nothing either
    updater.run_update(FakeGit(status=Result(0, " M x\n")), ROOT, False, echo, in_shell=True)
    assert not any("Restart" in line for line in out)


def test_shell_session_sets_flag_and_prints_hint(monkeypatch, tmp_path, capsys):
    import prompt_toolkit

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    lines = iter(["update", "exit"])
    seen = []

    class FakeSession:
        def __init__(self, **kw):
            pass

        def prompt(self, text):
            seen.append(cli._IN_SHELL)  # the flag is on while the loop runs
            return next(lines)

    monkeypatch.setattr(prompt_toolkit, "PromptSession", FakeSession)
    monkeypatch.setattr(updater, "find_repo_root", lambda start: ROOT)
    monkeypatch.setattr(updater, "subprocess_runner", pulled_scenario())
    assert cli._IN_SHELL is False
    cli.shell()
    out = capsys.readouterr().out
    assert "Restart the shell" in out and "Updated aaaaaaa -> bbbbbbb" in out
    assert seen == [True, True]
    assert cli._IN_SHELL is False  # reset when the shell exits


def test_one_shot_update_has_no_restart_hint(monkeypatch):
    monkeypatch.setattr(updater, "find_repo_root", lambda start: ROOT)
    monkeypatch.setattr(updater, "subprocess_runner", pulled_scenario())
    result = runner_cli.invoke(cli.app, ["update"])
    assert result.exit_code == 0 and "Updated aaaaaaa" in result.output
    assert "Restart" not in result.output
