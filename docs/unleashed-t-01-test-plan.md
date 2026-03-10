# Unleashed-T v01 Test Plan

## Scope

Validate the Codex CLI wrapper in `src/unleashed-t-01.py` for:

- PTY startup and shutdown
- Default launch policy (`-a never -s workspace-write`)
- Raw session logging
- Clean mirror logging
- Friction logger creation
- Tab naming
- Companion tab behavior
- Basic interactive I/O forwarding

## Preconditions

- `poetry install` completed successfully
- `codex.cmd` is installed at `C:\Users\mcwiz\AppData\Roaming\npm\codex.cmd`
- Codex CLI is already authenticated
- Windows Terminal is installed for companion tabs

## Automated Smoke Tests

### 1. Syntax

```bash
poetry run python -m py_compile src/unleashed-t-01.py
```

Expected:
- Exit code `0`

### 2. End-to-End Non-Interactive Wrapper Run

```bash
poetry run python src/unleashed-t-01.py --no-mirror --no-friction exec --skip-git-repo-check "Reply with exactly: UNLEASHED_T_SMOKE_OK"
```

Expected:
- Wrapper starts and exits cleanly
- Codex output includes `UNLEASHED_T_SMOKE_OK`
- Session summary prints on exit
- Summary shows a raw session log path and clean mirror log path

### 3. Interactive Launch Policy Verification

```bash
unleashed-t
```

Expected:
- Codex starts without approval prompts
- Wrapper startup requests `approval=never`
- Wrapper startup requests `sandbox=workspace-write`
- Treat any startup banner or behavior showing `read-only` as a release blocker

## Interactive Manual Tests

### 4. Tab Naming

Run from `/c/Users/mcwiz/Projects/patent-general`:

```bash
unleashed-t
```

Expected:
- Current terminal tab title starts with `PATENT-GENERAL`
- Title includes local timestamp `YYYY-MM-DD HH:MM`

### 5. Raw Companion Tab

Run:

```bash
unleashed-t
```

Expected:
- A `Codex Raw` Windows Terminal tab opens
- It tails `logs/codex-session-*.raw`
- Raw PTY output streams live while Codex runs

### 6. Friction Companion Tab

Run:

```bash
unleashed-t
```

Expected:
- A `Codex Friction` Windows Terminal tab opens
- It tails `logs/codex-friction-*.log`
- Log header notes approval detection is deferred to v02
- Session summary records `0` prompts for normal full-auto runs

### 7. Interactive File Task

From a disposable repo or scratch branch:

```text
Create a file named tmp-unleashed-t-check.txt containing the text OK.
```

Expected:
- Codex completes the file write without approval prompts
- The file is created in the current working directory
- Raw and mirror logs both capture the session

### 8. Interactive Read Task

Prompt:

```text
List the files in src/ and summarize what this repo does.
```

Expected:
- Input is forwarded correctly through the PTY
- Output appears in terminal and raw log
- Mirror log contains readable stripped output instead of dense ANSI noise

## Log Verification

After any run, verify:

- `logs/codex-session-*.raw` exists
- `logs/codex-session-*.log` exists
- `logs/codex-friction-*.log` exists when friction is enabled
- `logs/codex-friction-*.jsonl` exists when friction is enabled

## Failure Checks

- If `pywinpty` import fails, run `poetry install`
- If Codex is unauthenticated, run `codex login`
- If companion tabs do not open, verify `wt.exe` is available in PATH
- If interactive launch policy is not `never/workspace-write`, treat as a release blocker
