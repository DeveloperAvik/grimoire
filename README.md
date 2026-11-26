# grimoire

**A catalog, documentation engine and guarded launcher for the security tools already installed on your machine** (built for Kali Linux).

grimoire does not bundle, download or reimplement any tool. It knows *about* tools: what they are, which category they belong to, whether they are installed, how to use them, and it can launch them behind a confirmation step with an audit trail.

```
$ grimoire list -c web
$ grimoire info nmap
$ grimoire run nmap -sV 192.0.2.10 --dry-run
$ grimoire shell
```

> **Authorized use only.** grimoire is for systems you own or have explicit, written permission to test. See [Safety and legal](#safety-and-legal).

---

## Contents

- [Why grimoire](#why-grimoire)
- [Install](#install)
- [Quick start](#quick-start)
- [Commands](#commands)
- [How `info` works](#how-info-works)
- [Safety and legal](#safety-and-legal)
- [Audit log](#audit-log)
- [Where state lives](#where-state-lives)
- [Passing flags through to tools](#passing-flags-through-to-tools)
- [The catalog](#the-catalog)
- [Generating the catalog from Kali](#generating-the-catalog-from-kali)
- [Performance](#performance)
- [Project layout](#project-layout)
- [Development and tests](#development-and-tests)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)

---

## Why grimoire

A Kali install ships hundreds of tools, each with its own `--help` dialect. grimoire gives them one front door:

- **Find** tools by category or fuzzy search instead of remembering names.
- **Learn** a tool from short curated examples and a flag cheatsheet instead of a 2,000-line man page.
- **Know** what is installed (and its version) before you plan around it.
- **Launch** tools with the exact command shown first, a confirmation, and a JSON-lines record of what was run.

The catalog is plain YAML data, not code. Adding or correcting a tool is a text edit.

## Install

Requires **Python 3.11+**.

```bash
git clone https://github.com/DeveloperAvik/grimoire.git
cd grimoire
python3 -m venv .venv && . .venv/bin/activate   # Kali enforces PEP 668, so use a venv
pip install -e .                                # or: pip install -e '.[dev]' for pytest
grimoire --help
```

`pip install -e .` is the tested install path. Dependencies: Typer, Rich, prompt_toolkit, pydantic, PyYAML, rapidfuzz, platformdirs.

Optional shell completion for the grimoire command itself: `grimoire --install-completion`.

For the best `info` output on Kali, install the tldr client (`sudo apt install tldr && tldr -u`). grimoire works without it.

## Quick start

```bash
grimoire list                      # everything, grouped by category, with installed status
grimoire search subdomain          # fuzzy search names and descriptions
grimoire info nmap                 # curated examples, flags, then system docs
grimoire detect                    # what's installed, with versions
grimoire run nmap -sV 192.0.2.10 --dry-run    # preview a command; nothing executes
grimoire shell                     # interactive REPL with autocomplete
```

The first time you use `run` you will be shown a legal notice and asked to type `I AGREE`. Tools that act on targets will also ask you to record an engagement scope once. Both are described in [Safety and legal](#safety-and-legal).

## Commands

| Command | Purpose |
|---|---|
| [`list`](#list) | Tools grouped by category with installed status |
| [`search`](#search) | Fuzzy search over names and descriptions |
| [`info`](#info) | Curated usage, then system docs |
| [`run`](#run) | Show the exact command, confirm, execute, log |
| [`detect`](#detect) | Installed/missing report with versions |
| [`shell`](#shell) | Interactive REPL |

### list

```bash
grimoire list                  # all categories, in kill-chain order
grimoire list -c web           # one category (case-insensitive)
```

One table per category with columns *Tool / Installed / Description*. Interactive frameworks are marked `(interactive)`. Installed status uses `shutil.which` only. **`list` never executes a binary.** An unknown category exits 1 and prints the known ones; a known but empty category prints a message and exits 0.

### search

```bash
grimoire search wireless
grimoire search nmpa           # typos are tolerated
```

Fuzzy match (rapidfuzz) over names and descriptions, with names weighted above descriptions. Shows *Tool / Category / Description* for up to 10 hits. Exits 1 with a friendly message when nothing matches.

### info

```bash
grimoire info nmap
grimoire info nmapp            # no exact match: shows the closest tool and says so
```

Renders, in order:

1. **Header panel**: description, category, binary, installed yes/no, the install command if it is missing, a scope warning if the tool needs one, and a note for interactive frameworks or generated entries.
2. **Examples**: curated commands, each with a one-line description.
3. **Common flags**: a table of flag and meaning.
4. **Cheatsheet URL**.
5. **System docs**: the first available of `tldr`, `man`, `--help` (see [How `info` works](#how-info-works)).

### run

```bash
grimoire run nmap -sV 192.0.2.10              # shows the command, asks y/N, then executes
grimoire run nmap -sV 192.0.2.10 --dry-run    # shows and logs the command, executes nothing
grimoire run nmap -sV 192.0.2.10 -y           # skips the y/N (never the legal banner)
```

Flow, in order:

1. **Legal banner** on first use (type `I AGREE`). `-y` never skips it.
2. **Resolve the tool**: exact name, else the closest fuzzy match, else exit 1.
3. **Interactive tools are refused.** Frameworks such as Metasploit or Sliver are servers/REPLs, not one-shot tools. grimoire tells you to launch the binary directly and exits 0.
4. **Installed check.** If the binary is not on `PATH` it prints the install command and exits 1.
5. **Scope.** Tools marked `requires_auth_scope` prompt once for an authorization reference and in-scope targets. The active scope is shown before every run.
6. **The exact command** is printed (shell-quoted). `--dry-run` stops here; otherwise you confirm y/N.
7. **Execute** with output streamed to your terminal: no shell, no capture, no timeout.
8. **Audit line** is written. grimoire exits with the tool's own exit code.

Exit codes: `0` for dry-run, declined confirmation and interactive refusal; `1` for unknown tool, not installed, declined banner and missing scope; `127` if the binary could not be launched; otherwise the tool's exit code.

### detect

```bash
grimoire detect
```

A table of every cataloged tool as *installed* or *missing*, with a best-effort version for installed ones, ending in an `N/M installed` summary. Versions come from trying `--version`, `-V`, then `version` (5-second timeout each, first output line, truncated). **`detect` is the only command that executes cataloged binaries**, and only to read a version.

### shell

```bash
grimoire shell
```

```
Type help for commands, exit to quit.
grimoire> info nm<TAB>
```

An interactive loop (prompt_toolkit) that runs the same commands: `list`, `search`, `info`, `run`, `detect`, plus `help`, `clear`, `exit`/`quit`.

- **Autocomplete** is fuzzy: `info` and `run` complete tool names; the other commands complete themselves.
- **History** persists in `shell_history` in the state directory (mode `0600`).
- **Ctrl-C** cancels the current line and keeps going; **Ctrl-D** exits.
- **Resilient**: a bad command, a Typer exit or an unexpected exception is printed and the loop continues.
- **Fast**: the catalog is parsed once per session (see [Performance](#performance)); edits to catalog files are picked up automatically.
- The legal banner, scope prompt, confirmation and audit logging apply to `run` inside the shell exactly as outside it.

## How `info` works

The usage engine prefers curated knowledge and only then falls back to what the system provides:

| Priority | Source | Notes |
|---|---|---|
| 1 | Curated examples and flags | From the catalog YAML |
| 2 | `tldr <binary>` | Only if the tldr client is installed |
| 3 | `man <binary>` | Captured (not paged), formatting stripped, capped at about 40 lines with a `run: man <binary>` pointer |
| 4 | `<binary> --help` | Only if the tool is installed |

All subprocess calls use argument lists (never a shell), capture output, and time out after 10 seconds. Generated catalog entries have no curated examples, so for them the system docs are the content.

## Safety and legal

grimoire is a convenience layer, so it bakes in friction and records.

- **Legal banner.** The first `run` shows an *authorized testing only* notice and requires the exact text `I AGREE`. Anything else (including end-of-input) aborts with exit 1 and is audited. Acceptance is stored and not asked again. `-y` can never accept it for you.
- **Engagement scope.** For tools with `requires_auth_scope: true`, grimoire asks once for an **authorization reference** (ticket or contract id) and the **in-scope targets**, saves them locally, and prints them before every run. Do not put secrets in these fields.
- **Confirm before executing.** Every non-dry run shows the exact command and asks y/N. `--dry-run` previews and logs without executing.
- **No shell injection surface.** Commands run as argument lists. Nothing is passed through `sh -c`.
- **Interactive frameworks are never wrapped.** They are documented and launched by you directly.
- **No secrets stored.** State files hold only the acceptance flag, scope text, the audit log and shell history, all created with mode `0600`. Never type credentials into prompts or tool arguments you expect to keep out of the audit log: **the full argument list is recorded**.

**What these controls are not.** The banner is an acknowledgement, not authorization. The scope is a *record and reminder*: grimoire does not parse tool arguments or block a run whose target is outside it. The audit log is local and not tamper-proof. They support good practice; they do not replace a rules-of-engagement document or your own judgement.

## Audit log

Every `run` attempt appends exactly one JSON line to `audit.jsonl`:

```json
{"ts":"2026-11-26T20:24:13+00:00","tool":"nmap","binary":"nmap","argv":["nmap","-sV","192.0.2.10"],"cwd":"/home/you/work","scope_ref":"TICKET-1","dry_run":true,"outcome":"dry_run","exit_code":null}
```

Fields are always present; values not known at that point are `null`.

| Field | Meaning |
|---|---|
| `ts` | ISO-8601 UTC timestamp |
| `tool` | Catalog name (or what you typed, if it did not resolve) |
| `binary` | Executable name, `null` if the tool did not resolve |
| `argv` | The exact argument list; `null` for an unparseable shell line |
| `cwd` | Working directory |
| `scope_ref` | Authorization reference of the active scope, or `null` |
| `dry_run` | Whether `--dry-run` was set |
| `outcome` | One of the values below |
| `exit_code` | Tool or grimoire exit code, `null` when nothing ran |

| Outcome | Meaning |
|---|---|
| `executed` | The tool ran (see `exit_code`) |
| `dry_run` | Command shown, not executed |
| `aborted` | You answered no (or sent end-of-input) at the confirmation |
| `refused_interactive` | The tool is an interactive framework |
| `not_found` | The name did not resolve to a cataloged tool |
| `not_installed` | Resolved, but the binary is not on `PATH` |
| `launch_failed` | The OS refused to start the binary (`exit_code` 127) |
| `banner_declined` | The legal banner was not accepted |
| `scope_missing` | A scoped tool was requested and no scope was provided |
| `bad_input` | Shell only: an unparseable or malformed line (`exit_code` 2) |

**Invariant:** every `run` attempt, however it ends, writes exactly one line. Read-only commands (`list`, `info`, `search`, `detect`, `help`) are not audited.

Handy queries:

```bash
tail -n 5 "$GRIMOIRE_HOME/audit.jsonl" | python3 -m json.tool --json-lines
grep '"outcome":"executed"' audit.jsonl | wc -l
```

## Where state lives

| File | Contents |
|---|---|
| `accepted.json` | Legal banner acceptance and timestamp |
| `scope.json` | Authorization reference, in-scope targets, timestamp |
| `audit.jsonl` | One JSON object per `run` attempt |
| `shell_history` | REPL command history (can contain targets you typed) |

Location: `$GRIMOIRE_HOME` if set, otherwise the platform config directory (`~/.config/grimoire` on Linux). The directory and files are created on demand, and the files are mode `0600`. The repository's `.gitignore` excludes these four filenames anywhere in the tree, in case `GRIMOIRE_HOME` ever points inside a checkout.

There is one global scope per state directory. To start a new engagement, point `GRIMOIRE_HOME` somewhere else (one directory per engagement keeps audit logs separate) or delete `scope.json`.

| Variable | Effect |
|---|---|
| `GRIMOIRE_HOME` | State directory (see above) |
| `GRIMOIRE_CATALOG` | Catalog directory to use instead of the bundled one |

## Passing flags through to tools

Everything after the tool name is passed to the tool unchanged. grimoire claims only `--dry-run`, `--yes` and `-y`, **wherever they appear**. If a tool needs a flag with the same spelling, put `--` first; everything after it belongs to the tool:

```bash
grimoire run nmap --dry-run -- -sV -T4 192.0.2.10   # --dry-run is grimoire's; the rest is nmap's
grimoire run sometool -- -y                          # -y goes to sometool, not grimoire
```

## The catalog

Tools are described by YAML files. There are two tiers:

| Tier | Location | Content | Precedence |
|---|---|---|---|
| **Curated** | `catalog/*.yaml` | Hand-written: examples, flags, cheatsheet | Always wins |
| **Generated** | `catalog/generated/*.yaml` | Metadata from Kali's apt metapackages, no examples | Skipped if a curated entry has the same name |

Duplicate names inside a single tier are an error. Names are case-insensitive. The loader never needs code changes for new tools or new categories.

### Entry format

```yaml
tools:
  - name: nmap                       # unique, case-insensitive
    category: information-gathering  # lowercase words joined by hyphens (see below)
    binary: nmap                     # executable looked up on PATH
    install: sudo apt install nmap
    description: Network discovery and port/service scanner.
    common_flags:                    # flag -> meaning
      -sV: Probe open ports for service/version info
      -p-: Scan all 65535 ports
    examples:                        # cmd + desc
      - {cmd: "nmap -sV 192.0.2.10", desc: "Service scan of one host"}
    cheatsheet_url: https://nmap.org/book/man.html   # optional
    interactive: false               # true for frameworks/REPLs/servers
    requires_auth_scope: true        # prompts for engagement scope on first use
    source: curated                  # "generated" for ingest output (set automatically)
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `name` | yes | | Unique per tier |
| `category` | yes | | Validated: `^[a-z0-9]+(-[a-z0-9]+)*$`; uppercase is lowercased |
| `binary` | yes | | What `which` and `run` use |
| `description` | yes | | One line |
| `install` | no | `""` | Shown when the tool is missing |
| `common_flags` | no | `{}` | Flag to meaning |
| `examples` | no | `[]` | `cmd` + `desc` pairs |
| `cheatsheet_url` | no | none | |
| `interactive` | no | `false` | `run` refuses these |
| `requires_auth_scope` | no | `false` | Triggers the scope prompt |
| `source` | no | `curated` | |

Guidelines for curated examples: use documentation addresses (`192.0.2.0/24`, `example.com`) as targets, and keep examples to standard, well-documented usage of the tool.

### Categories

Categories are Kali's `kali-tools-<suffix>` names. They are open strings, so a category Kali adds later works without code changes. Known categories display in rough kill-chain order; unfamiliar ones follow, alphabetically.

```
information-gathering  vulnerability  web  database  passwords
wireless  802-11  bluetooth  rfid  sdr  voip  fuzzing
exploitation  sniffing-spoofing  post-exploitation  c2  social-engineering
reverse-engineering  crypto-stego  forensics  hardware  android  windows-resources
detect  identify  protect  respond  recover  reporting  uncategorized
```

### What is cataloged today

The repository ships **9 curated tools**: `whois`, `dnsrecon`, `theharvester`, `nmap` (information-gathering); `nikto`, `gobuster`, `ffuf` (web); `cherrytree`, `eyewitness` (reporting). The generated tier is produced on a Kali machine, reviewed, and committed separately, as described next.

Interactive frameworks (for example Metasploit or Sliver) are documented and launched directly, never wrapped by `run`. Entries for exploitation, post-exploitation and C2 tools follow the same rules as everything else: documentation plus a launcher entry, subject to the same scope gating and audit logging.

## Generating the catalog from Kali

`scripts/ingest_kali.py` builds the generated tier from the apt metapackages on a Kali machine. It is a standalone script (standard library plus PyYAML), **not** a runtime dependency of grimoire.

**It is metadata only.** Names, descriptions, binaries, categories, install commands and homepages come from `apt-cache` and `dpkg`. It never generates examples, flags or attack commands; those entries rely on the `tldr`/`man`/`--help` fallback.

### How discovery works

Starting at `kali-linux-everything`:

1. **Metapackage closure.** Dependencies named `kali-linux-*` or `kali-tools-*` are expanded repeatedly until none are new, with a visited set so dependency cycles terminate. A `kali-tools-*` nested several levels deep is still found.
2. **Categories.** Each `kali-tools-<suffix>` metapackage is category `<suffix>`; its direct Depends and Recommends are its tools.
3. **One level only.** Tools are never expanded further and apt's transitive `--recurse` is never used, so shared libraries cannot flood the catalog.
4. **Uncategorized.** Tools listed by a `kali-linux-*` metapackage but by no `kali-tools-*` land in `uncategorized`, so nothing in `everything` is dropped.
5. **Dedup.** A package in several categories keeps the first by display order.
6. **Fallback.** If `kali-linux-everything` cannot be read (not a Kali system, metapackage absent) the script warns loudly and falls back to an enumerated list of `kali-tools-*` metapackages. Any metapackage it cannot read is warned about and skipped.

Not expanded: `kali-linux-core`, `kali-linux-firmware` (base system) and `kali-tools-top10` (a cross-cutting selection, not a category). Loose packages starting with `lib`, `python3-`, `linux-`, `firmware-`, `fonts-` or `xserver-` are treated as infrastructure and dropped from `uncategorized`.

### Classification

- **`interactive: true`** for known frameworks (`metasploit-framework` with binary `msfconsole`, `sliver`, `powershell-empire`, `starkiller`, `armitage`) and everything in the `c2` category. `sliver`, `powershell-empire` and `starkiller` are placed in `c2` regardless of which metapackage lists them.
- **`requires_auth_scope: true` by default** ("when unsure, true"). It is `false` only for clearly local, defensive or analysis categories (`reverse-engineering`, `forensics`, `reporting`, `crypto-stego`, `hardware`, `windows-resources`, `detect`, `identify`, `protect`, `respond`, `recover`) and a short passive-recon allowlist (`whois`, `theharvester`, `maltego`, `spiderfoot`, `recon-ng`, `metagoofil`).
- **`binary`** is chosen from `dpkg -L` for installed packages (`/usr/bin`, `/usr/sbin`): the one matching the package name, else the alphabetically first, else the package name. For multi-binary packages, check the result.
- **`install`** is `apt install <package>`; **`cheatsheet_url`** is the apt Homepage when present.

The tuning knobs are constants at the top of the script (`UNSCOPED_PACKAGES`, `INTERACTIVE_PACKAGES`, `C2_PACKAGES`, `BINARY_OVERRIDES`, `EXCLUDED_SUFFIXES`, `NON_TOOL_METAPACKAGES`, `NON_TOOL_PREFIXES`).

### Workflow

```bash
# 1. generate (rewrites catalog/generated/ only; curated files are never touched;
#    the output directory must be named "generated")
python3 scripts/ingest_kali.py

# 2. confirm nothing curated changed
git status --short                 # only catalog/generated/ should be untracked

# 3. review scope flags: show every entry marked as NOT needing scope
python3 - <<'E'
import yaml, pathlib
OK_CATS = {"reverse-engineering", "forensics", "reporting", "crypto-stego", "hardware",
           "windows-resources", "detect", "identify", "protect", "respond", "recover"}
PASSIVE = {"whois", "theharvester", "maltego", "spiderfoot", "recon-ng", "metagoofil"}
for f in sorted(pathlib.Path("catalog/generated").glob("*.yaml")):
    for t in yaml.safe_load(f.read_text())["tools"]:
        if not t["requires_auth_scope"]:
            flag = "" if t["category"] in OK_CATS or t["name"] in PASSIVE else "   <-- UNEXPECTED"
            print(f'{t["category"]:24} {t["name"]}{flag}')
E

# 4. review the catch-all and the interactive set
cat catalog/generated/uncategorized.yaml | less
grep -B9 'interactive: true' catalog/generated/*.yaml | grep name:

# 5. run the suite against the generated catalog, then spot-check
pytest -q
grimoire detect | tail -1
grimoire list -c c2

# 6. commit the generated data on its own, as a separate reviewable diff
git add catalog/generated
git commit -m "Add generated Kali tool metadata"
```

Rerunning the script is safe: it replaces the whole generated tree and nothing else.

## Performance

- **YAML parsing** uses libyaml's `CSafeLoader` when PyYAML provides it (about 8x faster than the pure-Python loader) and falls back to `SafeLoader` otherwise.
- **The parsed catalog is memoized** per directory. The cache is invalidated when any catalog file is added, removed or modified (it compares each file's name, mtime and size), so a long-running `grimoire shell` parses once yet still sees edits.
- Measured on a synthetic 5,000-entry catalog: cold load about 150 ms, cached call well under a millisecond, fuzzy search about 10 ms. Each one-shot CLI command is dominated by Python start-up plus one cold load.
- `grimoire list` with thousands of tools renders thousands of rows; use `-c <category>` to narrow it.

## Project layout

```
grimoire/
├── pyproject.toml            # package metadata, dependencies, `grimoire` entry point
├── README.md
├── catalog/
│   ├── information-gathering.yaml   # curated, one file per category
│   ├── web.yaml
│   ├── reporting.yaml
│   └── generated/            # output of scripts/ingest_kali.py (added after review)
├── scripts/
│   └── ingest_kali.py        # standalone Kali metadata generator
├── src/grimoire/
│   ├── models.py             # Tool/Example models, category validation and ordering
│   ├── catalog.py            # YAML loading, tier precedence, cache, grouping, fuzzy search
│   ├── engagement.py         # state dir, banner acceptance, scope, audit log, history path
│   └── cli.py                # Typer app: list/search/info/run/detect/shell
└── tests/                    # pytest suite (no real tools or apt involved)
```

## Development and tests

```bash
pip install -e '.[dev]'
pytest
```

The tests never execute real external binaries and never call `apt` or `dpkg`: `shutil.which` and `subprocess.run` are patched (a fixture makes any unpatched `subprocess.run` fail the test), `GRIMOIRE_HOME` points at a temp directory, and the ingest script is tested through its pure transform functions and a fixture-driven discovery reader. Areas covered: catalog loading, validation, ordering and fuzzy search; tier precedence and the loader cache; every CLI command; the banner, scope, dry-run, confirmation and audit behaviour of `run` (one line per attempt, with the full field set); the shell's dispatch and resilience; and ingest discovery, classification and the write guard.

Conventions:

- Keep the catalog as data. New tools and categories need YAML, not code.
- All subprocess use is list-argument, no shell, with timeouts where output is captured.
- Do not commit runtime state (`accepted.json`, `scope.json`, `audit.jsonl`, `shell_history`).

## Limitations

- **Scope is recorded, not enforced.** grimoire will not stop a run whose target is outside the recorded scope.
- **One scope per state directory.** There is no per-engagement switcher; use separate `GRIMOIRE_HOME` directories.
- **The audit log is local and unsigned.** It records what was run through grimoire, not what you ran directly.
- **`run` records the full argument list**, so anything sensitive typed as an argument is written to the log.
- **Generated entries are thin.** They have a description, binary and install command, but no curated examples or flags; `info` leans on `tldr`/`man`/`--help`. Multi-binary packages may need a manual `binary` fix.
- **Classification of generated entries is heuristic.** Review `requires_auth_scope` and `interactive` before committing generated data.
- **Discovery assumes Kali's metapackage layout.** If Kali renames or restructures metapackages the script warns and falls back, but coverage can shrink silently, so check the summary count.
- **Not a sandbox.** Tools run with your privileges, exactly as if you launched them yourself.

## Troubleshooting

**`error: externally-managed-environment` when installing.** Kali follows PEP 668. Use a virtual environment as shown in [Install](#install).

**A tool I have installed shows as missing.** `list` and `detect` look for the cataloged `binary` on `PATH`. If the package installs a differently named executable, fix `binary` in the catalog entry.

**`info` shows no system docs.** The tool is not installed and has no curated content, or neither `tldr`, `man` nor `--help` produced output. Install the tool or the tldr client.

**`detect` shows `-` for a version.** The tool did not respond to `--version`, `-V` or `version` within 5 seconds. This is cosmetic.

**I want to reset my engagement.** Delete `scope.json` (new scope on next scoped run), or use a fresh `GRIMOIRE_HOME`.

**The shell does not see my catalog edit.** It should: files are re-checked on every command. If it doesn't, confirm you edited the directory `GRIMOIRE_CATALOG` points at (or the bundled `catalog/`).

**`unknown category`.** Run `grimoire list` to see categories that exist, or check spelling (matching is case-insensitive, hyphens required).

---

grimoire is intended for lawful, authorized security work. You are responsible for how you use it.
