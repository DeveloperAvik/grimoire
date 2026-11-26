"""Self-update for git-checkout installs: fast-forward only, never over local edits.

All git/pip access goes through an injected `runner`, so the logic is testable
without git, pip or a network. The default runner uses argument lists (no shell).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

FETCH_TIMEOUT = 120  # network operations
SHORT_TIMEOUT = 30  # local git operations
PIP_TIMEOUT = 600


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[list[str], int], Result]
Echo = Callable[[str], None]


class GitError(Exception):
    """A git/pip command could not be run or failed."""


def subprocess_runner(args: list[str], timeout: int) -> Result:
    """Default runner: list args, no shell, captured output, bounded time."""
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return Result(proc.returncode, proc.stdout, proc.stderr)


# --------------------------------------------------------------------------
# pure logic
# --------------------------------------------------------------------------
def find_repo_root(start: Path) -> Path | None:
    """Walk up from `start` to the first directory containing .git (dir or worktree file)."""
    start = start.resolve()
    for directory in (start, *start.parents):
        if directory.is_dir() and (directory / ".git").exists():
            return directory
    return None


def changed_paths(porcelain: str) -> list[str]:
    """Paths listed by `git status --porcelain` (untracked files included)."""
    return [line[3:] for line in porcelain.splitlines() if line.strip()]


def is_dirty(porcelain: str) -> bool:
    return bool(changed_paths(porcelain))


def commits_behind(rev_list_count: str) -> int:
    """Parse the output of `git rev-list --count HEAD..origin/<branch>`."""
    try:
        return int(rev_list_count.strip())
    except ValueError as exc:
        raise ValueError(f"unexpected rev-list output: {rev_list_count!r}") from exc


def needs_reinstall(changed_files: list[str]) -> bool:
    """Editable installs pick up code changes; only packaging metadata needs a reinstall."""
    return any(path.strip() == "pyproject.toml" for path in changed_files)


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def _git(runner: Runner, root: Path, args: list[str], timeout: int = SHORT_TIMEOUT) -> str:
    cmd = ["git", "-C", str(root), *args]
    try:
        result = runner(cmd, timeout)
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"`git {args[0]}` timed out after {timeout}s") from exc
    except OSError as exc:
        raise GitError(f"could not run git: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise GitError(f"`git {args[0]}` failed: {detail[-1] if detail else 'no output'}")
    return result.stdout.rstrip()  # keep leading spaces: porcelain lines start with a status column


def run_update(
    runner: Runner,
    repo_root: Path | None,
    check: bool,
    echo: Echo,
    python: str | None = None,
    in_shell: bool = False,
) -> int:
    """Update a git-checkout install. Returns a process exit code.

    `python` is the interpreter used for the editable reinstall; it defaults to
    sys.executable (the interpreter grimoire is running under), never a bare
    `python` resolved from PATH. `in_shell` adds a restart hint after a real pull.
    """
    if repo_root is None:
        echo("grimoire was not installed from a git checkout, so it cannot update itself.")
        echo("If you installed with pip, upgrade from your source, for example:")
        echo("  pip install --upgrade <path-or-git-url-of-your-grimoire-source>")
        return 0

    try:
        # 1. dirty-tree guard (mandatory for a real update; --check only warns)
        status = _git(runner, repo_root, ["status", "--porcelain"])
        dirty = is_dirty(status)
        if dirty and not check:
            echo("Refusing to update: you have uncommitted changes in " + str(repo_root))
            for path in changed_paths(status):
                echo(f"  {path}")
            echo("Commit, stash or revert them first (for example `git stash -u`), then run `grimoire update` again.")
            return 1

        # 2. branch
        branch = _git(runner, repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch == "HEAD":
            echo("Refusing to update: the checkout is on a detached HEAD. Check out a branch first.")
            return 1
        if branch != "main":
            echo(f"warning: on branch '{branch}', not 'main'; updating '{branch}'.")

        # 3. fetch
        _git(runner, repo_root, ["fetch", "--quiet", "origin"], FETCH_TIMEOUT)

        # 4. compare
        behind = commits_behind(
            _git(runner, repo_root, ["rev-list", "--count", f"HEAD..origin/{branch}"])
        )
        if behind == 0:
            echo("Already up to date.")
            return 0

        # 5. show incoming commits
        incoming = _git(runner, repo_root, ["log", "--oneline", f"HEAD..origin/{branch}"])
        echo(f"{behind} new commit{'s' if behind != 1 else ''} on origin/{branch}:")
        for line in incoming.splitlines():
            echo(f"  {line}")
        if check:
            echo("Update available. Run `grimoire update` to apply it.")
            if dirty:
                echo("Note: you have uncommitted changes, so `grimoire update` would refuse until they are committed or stashed.")
            return 0

        old = _git(runner, repo_root, ["rev-parse", "HEAD"])
        try:
            _git(runner, repo_root, ["pull", "--ff-only", "origin", branch], FETCH_TIMEOUT)
        except GitError as exc:
            echo(f"Could not fast-forward ({exc}).")
            echo("Your history has diverged from origin. Resolve it manually; grimoire will not merge or reset.")
            return 1
        new = _git(runner, repo_root, ["rev-parse", "HEAD"])

        # 6. reinstall only when packaging metadata changed
        changed = _git(runner, repo_root, ["diff", "--name-only", f"{old}..{new}"]).splitlines()
        if needs_reinstall(changed):
            echo("pyproject.toml changed; reinstalling (editable)...")
            cmd = [python or sys.executable, "-m", "pip", "install", "-e", str(repo_root)]
            try:
                result = runner(cmd, PIP_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as exc:
                result = Result(1, "", str(exc))
            if result.returncode != 0:
                echo(f"Updated {old[:7]} -> {new[:7]}, but the reinstall failed. Run it manually:")
                echo("  " + " ".join(cmd))
                return 1

        # 7. done
        echo(f"Updated {old[:7]} -> {new[:7]}. Done.")
        if in_shell:
            echo("Restart the shell (exit, then run `grimoire shell`) to load the new code.")
        return 0
    except GitError as exc:
        echo(f"Update failed: {exc}")
        return 1
    except ValueError as exc:
        echo(f"Update failed: {exc}")
        return 1
