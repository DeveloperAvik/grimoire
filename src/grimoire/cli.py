"""Typer CLI for grimoire."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from . import __version__, catalog, engagement, updater
from .models import KNOWN_CATEGORY_ORDER, Tool

app = typer.Typer(
    name="grimoire",
    help="Catalog, document and launch security tools installed on this host.",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"grimoire {__version__}", highlight=False)
        raise typer.Exit()


_IN_SHELL = False  # set by `shell` so `update` can tell the user to restart after a pull


def _do_update(check: bool) -> int:
    return updater.run_update(
        updater.subprocess_runner,
        updater.find_repo_root(Path(__file__)),
        check,
        lambda msg: console.print(msg, markup=False, highlight=False),
        in_shell=_IN_SHELL,
    )


def _update_callback(value: bool) -> None:
    if value:
        raise typer.Exit(_do_update(check=False))


@app.callback()
def root(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True, help="Show the version and exit."),
    update: bool = typer.Option(False, "--update", callback=_update_callback, is_eager=True, help="Update this git-checkout install (same as `grimoire update`)."),
) -> None:
    """Catalog, document and launch security tools installed on this host."""

_OVERSTRIKE = re.compile(r".\x08")
DOC_LINES = 40
DOC_TIMEOUT = 10
VERSION_TIMEOUT = 5


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _installed(tool: Tool) -> bool:
    """Presence check only; never executes the binary."""
    return shutil.which(tool.binary) is not None


def _run(args: list[str], timeout: int, env: dict[str, str] | None = None) -> str | None:
    """Run a command safely (no shell). Return stdout+stderr text or None on failure."""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return out if proc.returncode == 0 and out else None


def _name(tool: Tool) -> str:
    return f"{tool.name} [yellow](interactive)[/]" if tool.interactive else tool.name


def _load() -> dict[str, Tool]:
    try:
        return catalog.load_catalog()
    except Exception as exc:  # malformed catalog: fail with a readable message
        console.print(f"[red]Failed to load catalog:[/] {escape(str(exc))}")
        raise typer.Exit(2)


def _system_docs(tool: Tool) -> tuple[str, str] | None:
    """First available of tldr, man, --help. Returns (source label, text)."""
    b = tool.binary
    if shutil.which("tldr"):
        out = _run(["tldr", b], DOC_TIMEOUT)
        if out:
            return "tldr", out
    if shutil.which("man"):
        env = {**os.environ, "MANPAGER": "cat", "PAGER": "cat", "MANWIDTH": "100"}
        out = _run(["man", b], DOC_TIMEOUT, env=env)
        if out:
            lines = _OVERSTRIKE.sub("", out).splitlines()
            if len(lines) > DOC_LINES:
                lines = lines[:DOC_LINES] + ["", f"... truncated. run: man {b}"]
            return "man", "\n".join(lines)
    if shutil.which(b):
        out = _run([b, "--help"], DOC_TIMEOUT)
        if out:
            lines = out.splitlines()
            return "--help", "\n".join(lines[:DOC_LINES])
    return None


def _version(tool: Tool) -> str:
    for flag in ("--version", "-V", "version"):
        out = _run([tool.binary, flag], VERSION_TIMEOUT)
        if out:
            first = out.splitlines()[0].strip()
            return first if len(first) <= 60 else first[:57] + "..."
    return "-"


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
@app.command("list")
def list_tools(
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Only show this category."),
) -> None:
    """List cataloged tools grouped by category."""
    tools = _load()
    groups = catalog.by_phase(tools, category)
    if not groups:
        wanted = category.strip().lower() if category else ""
        known = {t.category for t in tools.values()} | set(KNOWN_CATEGORY_ORDER)
        if wanted not in known:
            console.print(f"[red]Unknown category[/] {escape(repr(category))}. Known: {', '.join(sorted(known))}")
            raise typer.Exit(1)
        console.print("[yellow]No tools in that category.[/]")
        return
    for phase, members in groups.items():
        table = Table(title=phase, title_justify="left", expand=False)
        table.add_column("Tool", style="bold cyan", no_wrap=True)
        table.add_column("Installed", justify="center")
        table.add_column("Description")
        for t in members:
            mark = "[green]yes[/]" if _installed(t) else "[red]no[/]"
            table.add_row(_name(t), mark, t.description)
        console.print(table)


@app.command()
def search(query: str = typer.Argument(..., help="Keyword to match against names and descriptions.")) -> None:
    """Fuzzy search tool names and descriptions."""
    results = catalog.search(_load(), query)
    if not results:
        console.print(f"[yellow]No tools matched[/] {escape(repr(query))}. Try `grimoire list`.")
        raise typer.Exit(1)
    table = Table()
    table.add_column("Tool", style="bold cyan", no_wrap=True)
    table.add_column("Category")
    table.add_column("Description")
    for t in results:
        table.add_row(_name(t), t.category, t.description)
    console.print(table)


@app.command()
def info(tool_name: str = typer.Argument(..., help="Tool name (typos are fuzzy-matched).")) -> None:
    """Show curated usage for a tool, then system docs."""
    tools = _load()
    tool = tools.get(tool_name.lower())
    if tool is None:
        matches = catalog.search(tools, tool_name)
        if not matches:
            console.print(f"[red]Unknown tool[/] {escape(repr(tool_name))}. Try `grimoire search`.")
            raise typer.Exit(1)
        tool = matches[0]
        console.print(f"[dim]No exact match; showing closest: {tool.name}[/]")

    installed = _installed(tool)
    head = [
        f"[bold]{tool.description}[/]",
        f"Category: {tool.category}    Binary: {tool.binary}",
        f"Installed: {'[green]yes[/]' if installed else '[red]no[/]'}",
    ]
    if not installed and tool.install:
        head.append(f"Install: [bold]{escape(tool.install)}[/]")
    if tool.source == "generated":
        head.append("[dim]Generated metadata entry: no curated examples, see system docs below.[/]")
    if tool.requires_auth_scope:
        head.append("[yellow]Requires written authorization / defined scope.[/]")
    if tool.interactive:
        head.append(f"[yellow]Interactive framework: launch `{escape(tool.binary)}` directly; grimoire run will refuse it.[/]")
    console.print(Panel("\n".join(head), title=f"[bold cyan]{tool.name}[/]", border_style="cyan"))

    if tool.examples:
        console.print("[bold]Examples[/]")
        for ex in tool.examples:
            console.print(f"  [green]$ {escape(ex.cmd)}[/]")
            console.print(f"    [dim]{escape(ex.desc)}[/]")
        console.print()

    if tool.common_flags:
        table = Table(title="Common flags", title_justify="left")
        table.add_column("Flag", style="magenta", no_wrap=True)
        table.add_column("Meaning")
        for flag, meaning in tool.common_flags.items():
            table.add_row(escape(str(flag)), escape(str(meaning)))
        console.print(table)

    if tool.cheatsheet_url:
        console.print(f"Cheatsheet: [link={tool.cheatsheet_url}]{tool.cheatsheet_url}[/]\n")

    docs = _system_docs(tool)
    if docs:
        source, text = docs
        console.print(Panel(escape(text), title=f"System docs ({source})", border_style="blue"))
    else:
        console.print("[dim]No system docs available (tldr/man/--help).[/]")


def _confirm(text: str) -> bool:
    try:
        return typer.confirm(text, default=False)
    except typer.Abort:  # EOF at the prompt counts as "no"
        return False


def _prompt(text: str) -> str:
    """Prompt for a line; EOF/Ctrl-D counts as an empty answer."""
    try:
        return typer.prompt(text, default="", show_default=False).strip()
    except typer.Abort:
        return ""


BANNER = (
    "[bold red]AUTHORIZED TESTING ONLY[/]\n\n"
    "These tools may only be used against systems you own or have explicit, written "
    "permission to test. Unauthorized use is illegal. You are responsible for staying "
    "within your agreed scope and for every command you run. Commands run through "
    "grimoire are recorded in a local audit log."
)


def _audit(
    tool: Tool | None,
    argv: list[str] | None,
    dry_run: bool,
    outcome: str,
    exit_code: int | None,
    name: str | None = None,
) -> None:
    """Write exactly one audit line. `name` is used when the tool did not resolve."""
    scope = engagement.load_scope()
    engagement.append_audit(
        {
            "ts": engagement.now(),
            "tool": tool.name if tool else name,
            "binary": tool.binary if tool else None,
            "argv": argv,
            "cwd": os.getcwd(),
            "scope_ref": scope["authorization_ref"] if scope else None,
            "dry_run": dry_run,
            "outcome": outcome,
            "exit_code": exit_code,
        }
    )


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    tool_name: str = typer.Argument(..., help="Tool to run; everything after it is passed through."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the command and log it, but do not execute."),
    assume_yes: bool = typer.Option(False, "--yes", "-y", help="Skip the y/N confirmation (never the legal banner)."),
) -> None:
    """Show the exact command, confirm, then execute it with output streamed."""
    # 1. legal banner (never auto-accepted)
    if not engagement.has_accepted():
        console.print(Panel(BANNER, border_style="red", title="Legal notice"))
        answer = _prompt("Type I AGREE to continue")
        if answer != "I AGREE":
            console.print("[red]Not accepted. Exiting.[/]")
            _audit(None, [tool_name, *ctx.args], dry_run, "banner_declined", 1, name=tool_name)
            raise typer.Exit(1)
        engagement.record_acceptance()

    # 2. resolve tool
    tools = _load()
    tool = tools.get(tool_name.lower())
    if tool is None:
        matches = catalog.search(tools, tool_name)
        if not matches:
            console.print(f"[red]Unknown tool[/] {escape(repr(tool_name))}. Try `grimoire search`.")
            _audit(None, [tool_name, *ctx.args], dry_run, "not_found", 1, name=tool_name)
            raise typer.Exit(1)
        tool = matches[0]
        console.print(f"[dim]No exact match; using closest: {tool.name}[/]")

    argv = [tool.binary, *ctx.args]

    # 3. interactive frameworks are never wrapped
    if tool.interactive:
        console.print(
            f"[yellow]{escape(tool.name)} is an interactive framework.[/] "
            f"Launch it directly: [bold]{escape(tool.binary)}[/]"
        )
        _audit(tool, argv, dry_run, "refused_interactive", None)
        raise typer.Exit(0)

    # 4. must be installed
    if not _installed(tool):
        hint = f" Install with: {escape(tool.install)}" if tool.install else ""
        console.print(f"[red]{escape(tool.binary)} is not installed.[/]{hint}")
        _audit(tool, argv, dry_run, "not_installed", 1)
        raise typer.Exit(1)

    # 5. engagement scope
    if tool.requires_auth_scope and engagement.load_scope() is None:
        console.print("[yellow]This tool requires a recorded engagement scope.[/] (Do not enter secrets.)")
        ref = _prompt("Authorization reference (ticket / contract id)")
        targets = _prompt("In-scope targets (hosts, ranges, domains)")
        if not ref or not targets:
            console.print("[red]Scope not recorded. Exiting.[/]")
            _audit(tool, argv, dry_run, "scope_missing", 1)
            raise typer.Exit(1)
        engagement.save_scope(ref, targets)
    scope = engagement.load_scope()
    if scope:
        console.print(
            f"[bold]Active scope:[/] {escape(scope['authorization_ref'])} -> {escape(scope['targets'])}"
        )

    # 6. show exact command
    console.print(f"[bold]Command:[/] [green]{escape(shlex.join(argv))}[/]")
    if dry_run:
        console.print("\\[dry-run] not executed")
        _audit(tool, argv, True, "dry_run", None)
        raise typer.Exit(0)
    if not assume_yes and not _confirm("Execute?"):
        console.print("Aborted.")
        _audit(tool, argv, False, "aborted", None)
        raise typer.Exit(0)

    # 7. execute: list args, no shell, streamed to the terminal
    try:
        rc = subprocess.run(argv).returncode
    except OSError as exc:
        console.print(f"[red]Failed to execute:[/] {escape(str(exc))}")
        _audit(tool, argv, False, "launch_failed", 127)
        raise typer.Exit(127)

    # 8. audit
    _audit(tool, argv, False, "executed", rc)
    raise typer.Exit(rc)


@app.command()
def update(
    check: bool = typer.Option(False, "--check", help="Only report whether an update is available; do not pull."),
) -> None:
    """Update a git-checkout install by fast-forwarding; refuses on a dirty tree."""
    raise typer.Exit(_do_update(check))


@app.command()
def detect() -> None:
    """Report which cataloged tools are installed, with versions."""
    tools = _load()
    table = Table()
    table.add_column("Tool", style="bold cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Version")
    found = 0
    for t in sorted(tools.values(), key=lambda t: t.name):
        if _installed(t):
            found += 1
            table.add_row(t.name, "[green]installed[/]", escape(_version(t)))
        else:
            table.add_row(t.name, "[red]missing[/]", "-")
    console.print(table)
    console.print(f"[bold]{found}/{len(tools)} installed[/]")


# --------------------------------------------------------------------------
# interactive shell
# --------------------------------------------------------------------------
META = ("help", "clear", "exit", "quit", "history")
SHELL_HELP = """[bold]Commands[/]
  list [-c CATEGORY]   tools by category with install status
  search QUERY         fuzzy search
  info TOOL            curated usage + system docs (a bare tool name works too)
  run TOOL [ARGS]      show command, confirm, execute (use --dry-run to preview)
  detect               installed tools and versions
  update [--check]     fast-forward this git checkout from origin
  history              last 20 commands from this machine's shell history
  help | clear | exit  shell meta commands  (Ctrl-L also clears the screen)"""

TOOLBAR_TARGETS_MAX = 50
HISTORY_SHOWN = 20


# ---- pure helpers (unit-tested; no terminal needed) ---------------------------------
def prompt_fragments(scope: dict | None) -> list[tuple[str, str]]:
    """Prompt as prompt_toolkit style fragments: `grimoire ❯ ` or `grimoire[<ref>] ❯ `."""
    parts = [("class:prompt", "grimoire")]
    if scope:
        parts.append(("class:scope", f"[{scope['authorization_ref']}]"))
    parts.append(("class:arrow", " ❯ "))
    return parts


def toolbar_text(scope: dict | None, total: int, installed: int) -> str:
    """Bottom toolbar text. Pure function of (scope, total, installed)."""
    if scope:
        targets = scope["targets"]
        if len(targets) > TOOLBAR_TARGETS_MAX:
            targets = targets[: TOOLBAR_TARGETS_MAX - 1] + "…"
        scope_part = f"scope: {scope['authorization_ref']} → {targets}"
    else:
        scope_part = "no scope set"
    return f" {scope_part}  │  {total} tools  │  {installed} installed "


def welcome_lines(version: str, total: int, installed: int, scope: dict | None) -> list[str]:
    scope_line = f"scope: {scope['authorization_ref']} → {scope['targets']}" if scope else "no scope set"
    return [f"grimoire {version}", f"{total} tools · {installed} installed", scope_line]


def history_entries(path: Path) -> list[str]:
    """Commands from a prompt_toolkit FileHistory file ('# timestamp' then '+line' rows)."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    entries: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("#"):  # a new entry begins
            if current:
                entries.append("\n".join(current))
            current = []
        elif line.startswith("+"):
            if current is None:
                current = []
            current.append(line[1:])
    if current:
        entries.append("\n".join(current))
    return entries


def history_view(path: Path, count: int = HISTORY_SHOWN) -> list[tuple[int, str]]:
    """The last `count` commands as (absolute position, command)."""
    entries = history_entries(path)
    start = max(0, len(entries) - count)
    return [(start + i + 1, e.replace("\n", " ")) for i, e in enumerate(entries[start:])]


def _install_status(tools: dict[str, Tool]) -> dict[str, bool]:
    """One which() pass over the catalog. Called once at shell startup, never per render."""
    return {name: _installed(tool) for name, tool in tools.items()}


def _known_commands(cmd: Any) -> set[str]:
    return set(getattr(cmd, "commands", {}) or {}) | set(META) | {"shell"}


def dispatch_line(cmd: Any, line: str) -> bool:
    """Parse and run one REPL line. Returns False when the shell should exit.

    Never raises: a bad line or a typer.Exit must not kill the shell.
    """
    try:
        argv = shlex.split(line)
    except ValueError as exc:
        console.print(f"[red]Parse error:[/] {escape(str(exc))}")
        _audit(None, None, False, "bad_input", 2)
        return True
    if not argv:
        return True
    head = argv[0].lower()
    if head in ("exit", "quit"):
        return False
    if head == "help":
        console.print(SHELL_HELP)
        return True
    if head == "clear":
        console.clear()
        return True
    if head == "history":
        rows = history_view(engagement.history_path())
        if not rows:
            console.print("[dim]No history yet.[/]")
        for number, text in rows:
            console.print(f"{number:>5}  {text}", markup=False, highlight=False)
        return True
    if head == "shell":
        console.print("[yellow]Already in the shell.[/]")
        return True
    if head not in _known_commands(cmd):
        # bare tool name -> `info <tool>` (read-only, not audited)
        try:
            is_tool = head in _load()
        except (typer.Exit, Exception):
            is_tool = False
        if is_tool and len(argv) == 1:
            argv = ["info", argv[0]]
        elif is_tool:
            console.print(f"[dim]Tip: `info {escape(head)}` shows usage; `run {escape(head)} ...` executes it.[/]")
    try:
        cmd.main(args=argv, prog_name="grimoire", standalone_mode=False)
    except (typer.Exit, SystemExit):
        pass
    except typer.Abort:
        console.print("[yellow]Aborted.[/]")
    except Exception as exc:  # usage errors, bugs: report and keep the loop alive
        fmt = getattr(exc, "format_message", None)  # click usage errors
        console.print(f"[red]{escape(fmt() if callable(fmt) else str(exc))}[/]")
        if any(c.__name__ == "UsageError" for c in type(exc).__mro__):
            # malformed args never reached the command body, so nothing else logged it
            _audit(None, argv, False, "bad_input", 2, name=argv[0])
    return True


def _build_completer(tools: dict[str, Tool] | None = None, status: dict[str, bool] | None = None):
    """Fuzzy completer. Tool names carry `<category> · installed|missing`; `list -c` completes categories."""
    from prompt_toolkit.completion import FuzzyCompleter, NestedCompleter, WordCompleter

    from .models import category_sort_key

    tools = tools if tools is not None else _load()
    status = status if status is not None else _install_status(tools)
    meta = {
        t.name: f"{t.category} · {'installed' if status.get(key) else 'missing'}"
        for key, t in tools.items()
    }
    names = WordCompleter(sorted(meta), meta_dict=meta, ignore_case=True)

    counts: dict[str, int] = {}
    for t in tools.values():
        counts[t.category] = counts.get(t.category, 0) + 1
    cats = sorted(set(KNOWN_CATEGORY_ORDER) | set(counts), key=category_sort_key)
    cat_meta = {c: f"{counts.get(c, 0)} tools" for c in cats}
    categories = WordCompleter(cats, meta_dict=cat_meta, ignore_case=True)

    tree = {
        "info": names,
        "run": names,
        "list": {"-c": categories, "--category": categories},
        "search": None,
        "detect": None,
        "update": None,
        "history": None,
        "help": None,
        "clear": None,
        "exit": None,
        "quit": None,
    }
    return FuzzyCompleter(NestedCompleter.from_nested_dict(tree))


def _build_key_bindings():
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("c-l")
    def _clear_screen(event) -> None:
        event.app.renderer.clear()  # redraws the prompt; the loop keeps running

    return kb


def _print_welcome(total: int, installed: int, scope: dict | None) -> None:
    from rich.box import ROUNDED
    from rich.text import Text

    title, counts, scope_line = welcome_lines(__version__, total, installed, scope)
    body = Text()
    body.append(counts + "\n", style="bold")
    body.append(scope_line, style="yellow" if scope else "dim")
    console.print(Panel(body, title=f"[bold cyan]{escape(title)}[/]", box=ROUNDED, expand=False, padding=(0, 2)))
    console.print("Type help for commands, exit to quit.")


@app.command()
def shell() -> None:
    """Interactive REPL with tool-name autocomplete."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style

    cmd = typer.main.get_command(app)
    hist = engagement.history_path()
    hist.touch(mode=0o600, exist_ok=True)
    hist.chmod(0o600)

    tools = _load()  # cached
    status = _install_status(tools)  # the only which() pass for this session
    total, installed = len(tools), sum(status.values())
    session = PromptSession(
        history=FileHistory(str(hist)),
        completer=_build_completer(tools, status),
        auto_suggest=AutoSuggestFromHistory(),
        key_bindings=_build_key_bindings(),
        style=Style.from_dict({"prompt": "ansicyan bold", "scope": "ansiyellow", "arrow": "ansigreen bold"}),
    )

    global _IN_SHELL
    _print_welcome(total, installed, engagement.load_scope())
    _IN_SHELL = True
    try:
        while True:
            scope = engagement.load_scope()  # re-read per prompt: `run` may have just recorded one
            try:
                line = session.prompt(
                    prompt_fragments(scope),
                    bottom_toolbar=[("", toolbar_text(scope, total, installed))],
                )
            except KeyboardInterrupt:
                continue  # cancel the current line
            except EOFError:
                break  # Ctrl-D
            if not dispatch_line(cmd, line):
                break
    finally:
        _IN_SHELL = False


if __name__ == "__main__":
    app()
