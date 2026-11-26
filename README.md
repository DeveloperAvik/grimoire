# grimoire

A front-end and documentation engine for security tools **already installed on your host** (built with Kali in mind).

grimoire does not bundle or reimplement anything. It catalogs tools by kill-chain phase, shows curated usage instead of a raw `--help` dump, tells you what is installed, and launches tools behind a confirm step with an audit trail. The catalog is plain YAML data.

## Install

```bash
pip install -e .          # Python 3.11+
pip install -e '.[dev]'   # adds pytest
grimoire --help
```

## Commands

| Command | What it does | Example |
|---|---|---|
| `list [-c CATEGORY]` | Tools grouped by category with installed status (uses `which`, never executes anything). | `grimoire list -c web` |
| `search QUERY` | Fuzzy search over names and descriptions; exits 1 if nothing matches. | `grimoire search subdomain` |
| `info TOOL` | Curated usage: header, examples, common flags, cheatsheet, then system docs. Typos resolve to the closest tool. | `grimoire info nmap` |
| `run TOOL [ARGS...]` | Shows the exact command, asks y/N, executes with output streamed, logs it. | `grimoire run nmap -sV 192.0.2.10 --dry-run` |
| `detect` | Which cataloged tools are installed, with best-effort versions, plus an `N/M installed` summary. | `grimoire detect` |
| `shell` | REPL with fuzzy autocomplete over commands and tool names, persistent history. `help`, `clear`, `exit`/`quit` are built in; Ctrl-C cancels the line, Ctrl-D exits. | `grimoire shell` |

Categories follow Kali's tool groups, listed in rough kill-chain order: `information-gathering`, `vulnerability-analysis`, `web`, `database`, `password-attacks`, `wireless`, `reverse-engineering`, `exploitation`, `sniffing-spoofing`, `post-exploitation`, `forensics`, `social-engineering`, `c2`, `reporting`.

### The `info` usage engine

`info` prefers hand-written content and only falls back to system docs:

1. curated examples and flags from the catalog
2. `tldr <binary>` if the tldr client is installed
3. `man <binary>` (captured, trimmed to about 40 lines, with a `run: man <binary>` pointer)
4. `<binary> --help`

## Safety model

- **Legal banner.** The first time you use `run`, grimoire shows an authorized-testing-only notice and requires you to type `I AGREE`. Anything else exits. `-y` never skips the banner.
- **Engagement scope.** Tools with `requires_auth_scope: true` prompt once for an authorization reference and in-scope targets, saved locally and shown before every run. Do not enter secrets in these fields.
- **Confirm before executing.** `run` prints the exact command and asks y/N. `--dry-run` shows and logs the command without executing it. `-y` skips only the y/N.
- **Audit log.** Every `run` attempt appends one JSON line to `audit.jsonl`: `ts`, `tool`, `binary`, `argv`, `cwd`, `scope_ref`, `dry_run`, `outcome`, `exit_code`. Outcomes: `executed`, `dry_run`, `aborted`, `refused_interactive`, `not_found`, `not_installed`, `launch_failed`, `banner_declined`, `scope_missing`, and (shell only) `bad_input` for unparseable or malformed lines. Every `run` attempt, however it ends, writes exactly one line; fields that are unknown at that point are `null`. Other shell commands (`list`, `info`, ...) are read-only and are not audited.
- **No shell, ever.** Commands run as argument lists, never through a shell.
- **Where state lives.** `$GRIMOIRE_HOME` if set, otherwise the platform config dir (`~/.config/grimoire` on Linux). Files: `accepted.json`, `scope.json`, `audit.jsonl`, `history`. All are created with mode `0600`.

### Passing flags through

Everything after the tool name is passed to the tool. `--dry-run`, `--yes` and `-y` are claimed by grimoire wherever they appear. To pass a tool's own flag with the same spelling, put `--` first:

```bash
grimoire run nmap --dry-run -- -sV -T4 192.0.2.10     # grimoire's --dry-run, then nmap's args
grimoire run sometool -- -y                           # -y goes to sometool, not grimoire
```

## Catalog

One YAML file per category in `catalog/`. Adding a tool is a data change only:

```yaml
tools:
  - name: nmap                  # unique, case-insensitive
    category: information-gathering   # one of the categories above
    binary: nmap                # executable looked up on PATH
    install: sudo apt install nmap
    description: Network discovery and port/service scanner.
    common_flags:               # flag -> meaning
      -sV: Probe open ports for service/version info
    examples:                   # cmd + desc
      - {cmd: "nmap -sV 192.0.2.10", desc: "Service scan of one host"}
    cheatsheet_url: https://nmap.org/book/man.html   # optional
    interactive: false          # true for frameworks/REPLs/servers
    requires_auth_scope: true   # prompts for engagement scope on first use
```

Entries are validated on load; duplicate names are rejected. Set `GRIMOIRE_CATALOG=/path/to/dir` to use a different catalog directory.

**Interactive tools** (frameworks that are servers or REPLs) are documented by `info` but never wrapped: `grimoire run` refuses them, logs `refused_interactive`, and tells you to launch the binary directly.

### Curated vs generated entries

The catalog has two tiers:

- `catalog/*.yaml` - **curated**: hand-written, with examples and common flags. These always win.
- `catalog/generated/*.yaml` - **generated** metadata from Kali's `kali-tools-*` metapackages (name, description, binary, category, install command, homepage). They have no examples or flags; `info` falls back to `tldr`, then `man`, then `--help`. If a generated entry shares a name with a curated one it is skipped. Duplicate names inside a single tier are an error.

Regenerate on a Kali machine (needs `apt-cache`, `dpkg` and PyYAML; not a runtime dependency):

```bash
python3 scripts/ingest_kali.py        # rewrites catalog/generated/ only; curated files are never touched
```

The script is metadata only. It never produces examples or attack commands. Frameworks and everything in `c2` are marked `interactive`, and categories that act on targets are marked `requires_auth_scope`; when unsure it defaults to requiring scope. Review the generated YAML before committing it.

Exploitation, post-exploitation and c2 entries follow the same rules as everything else: documentation plus launcher entries, subject to the same scope gating and audit logging.

## Development

```bash
pytest
```

Tests never execute real external binaries; `shutil.which` and `subprocess.run` are patched, and `GRIMOIRE_HOME` points at a temp dir.
