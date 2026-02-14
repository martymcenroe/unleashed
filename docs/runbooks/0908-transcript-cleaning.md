# Runbook: Transcript Cleaning

## Overview

Unleashed saves raw PTY output to `data/unleashed/` in the **target project** directory.
Post-session, run `clean_transcript.py` to produce cleaned and user-input-only files.

## Directory Layout

```
{any-project}/
  data/
    unleashed/
      {project}-{YYYYMMDD}-{HHMMSS}.raw    # Raw PTY output (auto-saved by unleashed)
      {project}-{YYYYMMDD}-{HHMMSS}.clean   # Cleaned transcript (clean_transcript.py)
      {project}-{YYYYMMDD}-{HHMMSS}.user    # User input only (clean_transcript.py)
```

All repos have `data/unleashed/` in `.gitignore`. These files are local-only.

## Transcript Save (Automatic)

Unleashed c-19+ tees all PTY output to `{cwd}/data/unleashed/` automatically.
The raw file path is printed at startup:

```
[Unleashed v00019] Transcript: C:\Users\mcwiz\Projects\Hermes\data\unleashed\Hermes-20260214-1430.raw
```

No user action required. The `.raw` file is created when the session starts
and flushed continuously. It survives crashes.

## Cleaning a Transcript

From the unleashed repo:

```bash
# Basic clean (removes TUI garbage, deduplicates, extracts user input)
poetry run python src/clean_transcript.py /path/to/project/data/unleashed/Project-20260214-1430.raw

# With word-merge fix (experimental — uses wordninja to reinsert spaces)
poetry run python src/clean_transcript.py --fix-spaces /path/to/project/data/unleashed/Project-20260214-1430.raw
```

### Output Files

| Extension | Contents |
|-----------|----------|
| `.raw`    | Unmodified PTY output (binary, ANSI codes included) |
| `.clean`  | Garbage removed, deduplicated, readable conversation |
| `.user`   | User input only (separated by blank lines, typing repaints removed) |

### What Gets Removed

- Spinner animations and activity words (95 regex patterns)
- Status bar timestamps and token counts
- Permission prompt UI chrome
- CLI help listings (garbled repaints)
- Checklist progress repaints
- Plan mode chrome
- Wrangler boilerplate
- Duplicate blocks from compaction replays
- Progressive typing repaints (fuzzy prefix matching)
- Non-breaking spaces (replaced with ASCII space)

### What Gets Preserved

- All actual conversation content
- Compaction markers ("Compacting conversation...", "Conversation compacted")
- Tool call names and results
- Error messages

### Example Output

```
Input:    data/unleashed/Hermes-20260213-2100.raw (9056 lines)
Output:   data/unleashed/Hermes-20260213-2100.clean (3377 lines)
Garbage:  4819 lines
Dedup:    328 lines
Blocks:   1332 lines (duplicate sections)
Total:    6479 lines removed (71.5%)
User:     112 user input lines -> data/unleashed/Hermes-20260213-2100.user
```

## Redacting Profanity (Optional)

A separate gitignored script removes profanity from `.user` files:

```bash
poetry run python src/redact.py /path/to/project/data/unleashed/Project-20260214-1430.user
```

This overwrites the `.user` file in place, removing FUCK, FUCKING, and FUCK YOU
as words (not lines). Whitespace is cleaned up. The script and `data/` directory
are both gitignored.

## Full Post-Session Workflow

```bash
# 1. Session ends, .raw file exists in the target project
# 2. Clean from the unleashed repo
cd ~/Projects/unleashed
poetry run python src/clean_transcript.py ~/Projects/Hermes/data/unleashed/Hermes-20260214-1430.raw

# 3. Optionally redact profanity
poetry run python src/redact.py ~/Projects/Hermes/data/unleashed/Hermes-20260214-1430.user

# 4. Review
cat ~/Projects/Hermes/data/unleashed/Hermes-20260214-1430.user
```

## Setup (Per-Repo)

Every repo needs `data/unleashed/` in `.gitignore`. This was set up for all 53 repos
on 2026-02-14. For new repos, this is tracked in AssemblyZero issue for the
project generator template.

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| No `.raw` file created | Running unleashed < c-19 | Upgrade to c-19+ |
| `.raw` is empty | Session crashed before PTY read | Check stderr for spawn errors |
| `.clean` has too much garbage | New TUI pattern not recognized | Add pattern to `GARBAGE_PATTERNS` in `clean_transcript.py` |
| `.user` has duplicate blocks | Fuzzy threshold too low | Adjust `MATCH_RATIO` in `dedup_typing_repaints()` |
| `wordninja` import error | Dependency missing | `cd unleashed && poetry install` |
