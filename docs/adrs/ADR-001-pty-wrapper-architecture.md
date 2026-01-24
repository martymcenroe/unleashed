# ADR-001: PTY Wrapper Architecture for Claude Code Auto-Approval

**Status:** Accepted
**Date:** 2026-01-20
**Deciders:** mcwiz, Claude
**Version:** 00010 (promoted to production)

## Context

Claude Code's permission system does not work well in git-bash and Windows Terminal. The native permission prompts are unusable, creating a need for a wrapper that can:
1. Auto-approve permission prompts
2. Pass through keyboard input correctly (especially arrow keys)
3. Maintain Claude's TUI rendering (status bar, plan mode indicator)
4. Exit cleanly without hanging the terminal

## Decision Drivers

- Windows Terminal + git-bash incompatibility with Claude's permission UI
- Need for supervised auto-approval (user can still see what's being approved)
- Arrow keys and special keys must work for navigation
- Claude's status bar must render correctly (plan mode, git status, etc.)
- Clean terminal state after exit

## Considered Options

### Option 1: Queue-based I/O with Overlay (Archive A01-A14)
Complex architecture with:
- `PtyReader` class with background thread and queue
- `InputReader` class with background thread and queue
- `CountdownOverlay` using ANSI cursor positioning (CURSOR_SAVE/RESTORE/HOME)
- 10-second countdown with visual feedback
- Structured event logging to JSONL

**Rejected because:** The overlay's cursor manipulation conflicted with Claude's TUI. CURSOR_HOME and CURSOR_SAVE/RESTORE sequences interfered with Claude's status bar rendering.

### Option 2: Direct PTY passthrough with blocking getch() (Archive A15)
Simplified architecture with:
- Direct `msvcrt.getch()` calls (blocking)
- Simple byte pattern matching for footer detection
- Latin-1 encoding hack for special keys
- Countdown message to stderr

**Partially rejected because:** Blocking `getch()` caused terminal hang on exit. The countdown message to stderr still interfered with status bar.

### Option 3: Non-blocking kbhit() with silent approval (v00010 - ACCEPTED)
Current architecture with:
- `msvcrt.kbhit()` polling with sleep to avoid CPU spin
- Windows key code to ANSI escape sequence mapping
- Silent approval (no visible message)
- No runtime stderr output during Claude session
- `sys.exit(0)` for guaranteed clean exit
- Full terminal reset (RIS) on exit

## Decision

**Accepted Option 3** with the following key architectural choices:

### 1. Non-blocking Input via kbhit() Polling
```python
if msvcrt.kbhit():
    char = msvcrt.getch()
    # process key
else:
    time.sleep(0.01)  # Avoid CPU spin
```
This allows the stdin thread to check `self.running` flag and exit cleanly.

### 2. Windows Key Code to ANSI Mapping
```python
KEY_MAP = {
    b'H': '\x1b[A',   # Up
    b'P': '\x1b[B',   # Down
    b'K': '\x1b[D',   # Left
    b'M': '\x1b[C',   # Right
    b'\x0f': '\x1b[Z',  # Shift+Tab
    # ... etc
}
```
Windows console returns `\x00` or `\xe0` prefix followed by scan code. We map these to standard ANSI sequences.

### 3. Silent Approval
No stdout or stderr output during approval. The 0.5s delay still provides a window for user intervention.

### 4. No Runtime stderr Output
All debug output removed during Claude session. Only startup banner before Claude launches.

### 5. Forced Exit with sys.exit(0)
```python
finally:
    # ... cleanup ...
    sys.exit(0)  # Force exit - daemon threads may be blocking
```
Discovered in archive A12 - this prevents terminal hang caused by daemon threads blocking on I/O.

### 6. Full Terminal Reset
```python
sys.stdout.write(TERM_RESET)  # Reset attributes, show cursor, disable mouse
sys.stdout.write('\x1bc')      # RIS - Reset to Initial State
```

## Consequences

### Positive
- Terminal no longer hangs on exit
- Arrow keys work correctly
- Status bar renders correctly (plan mode indicator, git status)
- Clean, minimal codebase (~140 lines)

### Negative
- Shift+Tab mode cycling not yet working (key mapping added but may need further investigation)
- No visual feedback during auto-approval countdown
- No structured logging (removed for cleaner operation)

### Neutral
- 0.5s approval delay is a trade-off between responsiveness and user intervention window

## Technical Notes

### Why stderr Output Interfered with Status Bar
Claude's TUI uses cursor positioning escape sequences to render the status bar at the bottom of the screen. When our wrapper wrote to stderr with newlines (`\n`), it disrupted Claude's cursor position calculations, causing the status bar to render incorrectly or show artifacts.

### Why sys.exit(0) is Necessary
Python daemon threads don't prevent the main thread from exiting, but they also don't guarantee clean termination. The `msvcrt.getch()` and `pty.read()` calls can block indefinitely. Even with `kbhit()` polling, there's a race condition at shutdown. `sys.exit(0)` guarantees immediate process termination.

### The Overlap Buffer
```python
self.overlap_buffer = raw_bytes[-32:]
search_chunk = self.overlap_buffer + raw_bytes
```
This ensures we don't miss the footer pattern if it spans two read chunks. The footer pattern `b'Esc to cancel'` is 13 bytes, so 32 bytes of overlap is sufficient.

## Related Documents
- `docs/reports/done/REP-001-debugging-session-2026-01-20.md` - Full debugging session report
- `archive/` - Historical versions with timestamps in `rename-mapping.txt`
