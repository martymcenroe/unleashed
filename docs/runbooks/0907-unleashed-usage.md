# Runbook: Unleashed Usage

## Quick Start

```bash
# Navigate to any project directory, then launch
cd Projects/Hermes
unleashed-c-18-triplet
```

## Available Commands

| Command | Script | What It Does |
|---------|--------|-------------|
| `unleashed` | `unleashed.py` (v16) | Basic auto-approval, no tabs |
| `unleashed-c-18` | `unleashed-c-18.py` (v18) | v18 without companion tabs |
| `unleashed-c-18-mirror` | v18 `--mirror` | Session mirror tab only |
| `unleashed-c-18-triplet` | v18 `--mirror --friction` | Full triplet: main + mirror + friction |
| `unleashed-c-18-joint` | v18 `--joint-log --friction` | Permissions inline in mirror + friction |

## v18 Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--mirror` | **on** | Opens a Session Mirror tab (clean scrollable transcript) |
| `--friction` | **on** | Opens a Friction Logger tab (permission prompt tracking) |
| `--no-mirror` | — | Disable session mirror |
| `--no-friction` | — | Disable friction logger |
| `--joint-log` | off | Embed permission events inline in the session mirror |
| `--cwd PATH` | current dir | Override working directory (Windows path) |

Extra arguments are passed through to `claude.cmd` (requires shell function aliases, see issue #6).

## Companion Tabs (Triplet)

v18 opens up to 3 Windows Terminal tabs:

1. **Main tab** — Claude Code session with auto-approval
2. **Session Mirror** — `tail -f` of a cleaned, ANSI-stripped transcript (`logs/session-*.log`)
3. **Friction Logger** — Real-time permission prompt tracking (`logs/friction-*.log`)

Tabs are launched via `wt.exe` and require Windows Terminal.

## How Auto-Approval Works

1. Unleashed spawns Claude Code inside a Windows PTY (`pywinpty`)
2. The PTY output reader scans for the pattern `Esc to cancel · Tab to amend`
3. When matched, it waits 500ms then sends Enter (`\r`) to approve
4. v18 also matches model pause patterns ("Should I proceed?") and auto-selects option 1

## Logs

All logs are in `unleashed/logs/`:

| File Pattern | Contents |
|-------------|----------|
| `unleashed_v00018.log` | Debug log (spawn, approvals, errors) |
| `session-YYYYMMDD-HHMMSS.log` | Session mirror transcript |
| `friction-YYYYMMDD-HHMMSS.log` | Human-readable permission log |
| `friction-YYYYMMDD-HHMMSS.jsonl` | Machine-readable permission data |

## Troubleshooting

### Terminal left in raw mode after crash
The console mode should be restored on exit. If it isn't (see issue #17):
```bash
# Reset terminal
reset
```

### Character-per-line rendering at startup
A PTY dimension race condition. Fixed in v18 by reading terminal size before changing console mode. If it still happens, it self-corrects when the resize monitor kicks in.

### Pattern not matching (permissions not auto-approved)
Claude Code UI text changed. Check `PERMISSION_PATTERNS` in the script against current Claude Code output. See issue #8.

### pywinpty missing
```bash
cd /c/Users/mcwiz/Projects/unleashed
poetry install
```

## Architecture

```
User Terminal
    |
    v
unleashed-c-18.py (Python)
    |
    +-- _reader_stdin: Windows Console API -> PTY input
    +-- _reader_pty:   PTY output -> stdout + pattern matching + mirror/friction logging
    +-- _resize_monitor: polls terminal size, forwards to PTY
    +-- do_approval:   sends \r when permission pattern detected
    +-- _auto_answer:  sends 1\r when model pause detected
    |
    v
winpty.PtyProcess (cmd /c claude.cmd)
    |
    v
Claude Code (Ink TUI)
```

## Related Issues

- #5 — v18 Triplet Tabs feature
- #6 — Shell function conversion for argument passthrough
- #8 — Pattern brittleness
- #9 — No promotion pipeline (v18 isn't what `unleashed` alias runs)
