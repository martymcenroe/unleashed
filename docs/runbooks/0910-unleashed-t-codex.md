# Runbook: Unleashed-T (Codex CLI)

## Purpose

This runbook explains how to run the Codex wrapper in the `unleashed` repo without relying on `CLAUDE.md`.

## What It Is

`unleashed-t` is the Codex CLI wrapper implemented in `src/unleashed-t-04.py` (production).
`unleashed-t-alpha` runs `src/unleashed-t-05.py` (alpha: t-04 + resume/fork workflows).

It provides:

- a Windows PTY wrapper around Codex CLI
- keyboard input forwarding
- PTY output streaming to the terminal
- raw session logging and cleaned mirror log
- friction log scaffold
- automatic tab naming
- optional companion tabs for raw logs and friction logs
- event-driven main loop (low CPU idle)
- session index with `--sessions` flag
- per-repo session logs

## Preconditions

- Run from Git Bash, not PowerShell
- `poetry install` has been run in the repo
- `codex.cmd` exists at `C:\Users\mcwiz\AppData\Roaming\npm\codex.cmd`
- Codex CLI is authenticated
- Windows Terminal is installed if you want companion tabs
- The shell aliases `unleashed-t()` and `unleashed-t-alpha()` exist in `~/.bash_profile`

## Default Launch Behavior

The wrapper launches Codex with:

- approval requested as `never`
- sandbox requested as `workspace-write`

The wrapper is an interactive PTY launcher. Non-interactive `codex exec` behavior should not be treated as authoritative for the interactive wrapper's sandbox semantics.

## How To Run It

From any target project directory:

```bash
unleashed-t              # production (t-04)
unleashed-t-alpha        # alpha (t-05)
```

This should:

- set the terminal tab title to `REPONAME YYYY-MM-DD HH:MM`
- start Codex inside the PTY wrapper
- create raw and cleaned log files under `unleashed/logs/`
- open companion tabs unless disabled

## Useful Flags

These flags are handled by the wrapper itself:

- `--cwd PATH`
- `--mirror` / `--no-mirror`
- `--friction` / `--no-friction`
- `--sessions` — display session index and exit

Examples:

```bash
unleashed-t --no-mirror
unleashed-t --no-friction
unleashed-t --sessions
```

Arguments not consumed by the wrapper are passed through to Codex.

## Alpha: Resume and Fork Workflows (t-05)

The alpha wrapper (`unleashed-t-alpha`) adds native resume and fork flags. These are **launch-time flags only** — they select the Codex launch mode before the PTY spawns. They are not in-session slash commands and cannot be used once a session is running.

### Resume Flags

| Flag | Behavior |
|------|----------|
| `--resume` | Open the Codex native resume picker |
| `--resume-last` | Resume the most recent Codex session |
| `--resume-id SESSION` | Resume a specific session ID or thread name |

### Fork Flags

| Flag | Behavior |
|------|----------|
| `--fork` | Open the Codex native fork picker |
| `--fork-last` | Fork the most recent Codex session |
| `--fork-id SESSION` | Fork a specific session ID |

### Examples

```bash
unleashed-t-alpha --resume           # interactive picker
unleashed-t-alpha --resume-last      # jump back into last session
unleashed-t-alpha --resume-id abc123 # resume a specific session
unleashed-t-alpha --fork-last        # fork from last session
```

Resume and fork are mutually exclusive launch modes (you pick one or neither). Logging is launch-mode-aware: log filenames reflect whether the session is fresh, resumed, or forked.

## Non-Interactive Smoke Test

Use this to verify wrapper startup, PTY handling, and shutdown:

```bash
cd /c/Users/mcwiz/Projects/unleashed
poetry run python src/unleashed-t-04.py --no-mirror --no-friction exec --skip-git-repo-check "Reply with exactly: UNLEASHED_T_SMOKE_OK"
```

Expected:

- process exits cleanly
- output includes `UNLEASHED_T_SMOKE_OK`
- wrapper prints a session summary
- raw and clean log files are created

## Logs

Files are written under `logs/` in the `unleashed` repo.

- `logs/codex-session-YYYYMMDD-HHMMSS.raw`
- `logs/codex-session-YYYYMMDD-HHMMSS.log`
- `logs/codex-friction-YYYYMMDD-HHMMSS.log`
- `logs/codex-friction-YYYYMMDD-HHMMSS.jsonl`

## Companion Tabs

When enabled, the wrapper can open:

- `Codex Raw` for the raw session log
- `Codex Friction` for the friction log

The clean mirror log is always written to disk, even if the raw companion tab is disabled.

## Operational Checks

After a run, verify:

- the tab title matches the current repo name
- the raw session log exists
- the clean mirror log exists
- the friction files exist when friction is enabled
- the session summary printed on exit

## Codex Sandbox Proxy Behavior

Codex CLI's Windows sandbox injects proxy env vars (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `GIT_HTTP_PROXY`, `GIT_HTTPS_PROXY` = `http://127.0.0.1:9`) to null-route all HTTP traffic. This is by design — the `--full-auto` help text says "network-disabled sandbox."

The proxy is injected by `codex.exe` itself after launch, not by unleashed or any user config. The unleashed-t wrapper passes `os.environ.copy()` (clean, no proxy vars) but Codex adds them internally.

### Mitigation

The wrapper sets `NO_PROXY=*` in the env before spawn. Most HTTP clients (curl, gh, git, requests) respect `NO_PROXY` and skip the proxy for matching hosts. `*` means all hosts.

Additionally, the wrapper passes `-c shell_environment_policy.inherit=all` to Codex, which instructs Codex to inherit the parent environment without overriding it.

### Fallback

If `NO_PROXY=*` doesn't survive Codex's sandbox injection, create a wrapper script that unsets the proxy vars before calling gh:

```bash
#!/bin/bash
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY GIT_HTTP_PROXY GIT_HTTPS_PROXY
exec gh "$@"
```

Prepend a PATH directory containing this wrapper, or set `GH_PATH` to point to it.

### Verification

Inside a Codex session:

```bash
env | grep -i proxy       # Check NO_PROXY=* is present
gh issue list -R octocat/Hello-World --limit 3   # Should succeed
```

## Web Search

The `--search` flag enables Codex's web search capability. This is passed by default in the wrapper's codex_cmd construction. No config.toml key exists for this — it's CLI-flag only as of Codex 0.115.0.

## Config Boundaries (Codex 0.115.0)

Documented/verified settings used by the wrapper:

| Setting | Value | Mechanism |
|---------|-------|-----------|
| Model | gpt-5.4 | config.toml |
| Sandbox | workspace-write | `-s` flag |
| Approval | never | `-a` flag |
| shell_environment_policy.inherit | all | `-c` flag |
| Web search | enabled | `--search` flag |

**Not documented** in `codex --help` or `config.toml`: reasoning effort, output verbosity, context window, output token limit. Do not guess config keys for these.

## Troubleshooting

### `pywinpty` import failure

Run:

```bash
cd /c/Users/mcwiz/Projects/unleashed
poetry install
```

### Codex not authenticated

Run:

```bash
codex login
```

### Companion tabs do not open

Check that `wt.exe` is installed and available.
