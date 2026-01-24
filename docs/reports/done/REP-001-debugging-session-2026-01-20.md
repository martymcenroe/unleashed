# REP-001: Unleashed Debugging Session Report

**Date:** 2026-01-20
**Duration:** ~4 hours
**Outcome:** Successful - v00010 promoted to production
**Participants:** mcwiz, Claude (Opus 4.5)

---

## Executive Summary

Starting from a barely-working wrapper (v3026/unleashed-test.py), we systematically debugged and fixed multiple issues to arrive at v00010, which correctly:
- Passes arrow keys and special keys to Claude
- Renders Claude's status bar (plan mode, git status)
- Exits cleanly without hanging the terminal
- Auto-approves permission prompts silently

---

## Issues Addressed

### 1. Arrow Keys Not Working
**Symptom:** Pressing arrow keys printed `àH àP àK àM` instead of navigating.

**Root Cause:** The original code used latin-1 encoding to pass Windows key codes through to the PTY:
```python
full_seq = (char + next_char).decode('latin-1')
pty.write(full_seq)
```
This sent the raw Windows bytes (`\xe0H`) which Claude interpreted as literal characters.

**Fix (v00002):** Map Windows key codes to ANSI escape sequences:
```python
KEY_MAP = {
    b'H': '\x1b[A',  # Up
    b'P': '\x1b[B',  # Down
    b'K': '\x1b[D',  # Left
    b'M': '\x1b[C',  # Right
    # ...
}
```

**Status:** SOLVED

---

### 2. Terminal Hangs After Exit
**Symptom:** After `/exit`, the terminal became unresponsive. User had to close the tab.

**Root Cause:** Daemon threads were blocking on I/O:
- `msvcrt.getch()` blocks waiting for keyboard input
- `pty.read()` blocks waiting for PTY output

Even though daemon threads should terminate when the main thread exits, something was keeping the process alive.

**Attempted Fixes:**
- v00005: Non-blocking `kbhit()` polling instead of blocking `getch()` - **Partial improvement**
- v00008: `sys.exit(0)` at end of cleanup - **SOLVED**

**Key Discovery:** Found in archive A12 which had:
```python
# Force exit - daemon threads may be blocking on I/O
sys.exit(0)
```

**Status:** SOLVED

---

### 3. Status Bar Not Rendering (Display Artifacts)
**Symptom:** Claude's status bar showed fragments like "11 files +98 -45" in wrong positions. Plan mode indicator not visible.

**Root Cause:** Our stderr debug output was interfering with Claude's cursor positioning:
```python
sys.stderr.write(f"[v{VERSION}] Thread-PTY: ONLINE\n")
```
The newlines disrupted Claude's ANSI cursor positioning for the status bar.

**Attempted Fixes:**
- v00004: Terminal reset sequences on exit - **No improvement**
- v00007: Silent approval (removed countdown message) - **Partial improvement**
- v00010: Remove ALL runtime stderr output - **SOLVED**

**Status:** SOLVED

---

### 4. Shift+Tab Mode Cycling
**Symptom:** Pressing Shift+Tab doesn't cycle between plan mode / accept edits / off.

**Root Cause:** Shift+Tab wasn't in the KEY_MAP.

**Fix (v00009):** Added mapping:
```python
b'\x0f': '\x1b[Z',  # Shift+Tab (scan code 15)
```

**Status:** PARTIALLY WORKING - Key mapping added but cycling doesn't work yet. May need further investigation. However, user can use `/plan` command as workaround.

---

### 5. Strange Characters on Exit
**Symptom:** Characters like `;46;99;1;0;1_` appearing in prompt after exit.

**Root Cause:** Partial ANSI escape sequences (likely mouse tracking) leaking when PTY closes mid-stream.

**Fix (v00004+):** Terminal reset sequences:
```python
TERM_RESET = (
    '\033[0m'       # Reset attributes
    '\033[?25h'     # Show cursor
    '\033[?1000l'   # Disable mouse tracking
    '\033[?1002l'   # Disable mouse button tracking
    '\033[?1003l'   # Disable all mouse tracking
    '\033[?1006l'   # Disable SGR mouse mode
)
sys.stdout.write('\x1bc')  # Full terminal reset (RIS)
```

**Status:** IMPROVED (mostly solved, occasional artifacts may still appear)

---

## Version Evolution

| Version | Changes | Result |
|---------|---------|--------|
| 00002 | Arrow key mapping (Windows → ANSI) | Arrow keys work |
| 00003 | Clearer dimension handling, debug output | Diagnostics |
| 00004 | Terminal reset on exit | Partial improvement |
| 00005 | Non-blocking kbhit() polling | Partial improvement on hang |
| 00006 | UNLEASHED_VERSION env var | /unleashed-version works |
| 00007 | Silent approval (no countdown message) | Status bar partial fix |
| 00008 | sys.exit(0) forced exit | **Terminal hang SOLVED** |
| 00009 | Shift+Tab key mapping | Key added (not fully working) |
| 00010 | Remove ALL runtime stderr | **Status bar SOLVED** |

---

## Archive Analysis

### Patterns Accepted

1. **sys.exit(0) for forced exit** (from A12)
   - Critical discovery that solved terminal hang

2. **Windows key code to ANSI mapping** (from A12 InputReader)
   - Using `getwch()` returns scan codes that need ANSI translation

3. **stderr for startup banner only** (refined from A15)
   - A15 used stderr for runtime messages; we restricted to startup only

4. **Overlap buffer for pattern detection** (from A15)
   - Keeping last 32 bytes ensures pattern detection across read boundaries

5. **Simple byte pattern matching** (from A15)
   - `b'Esc to cancel'` is simpler and faster than regex

### Patterns Rejected

1. **Queue-based I/O architecture** (A01-A14)
   - `PtyReader` and `InputReader` classes with queues
   - Added complexity without benefit for our use case

2. **CountdownOverlay with cursor positioning** (A01-A14)
   - CURSOR_SAVE, CURSOR_HOME, CURSOR_RESTORE sequences
   - Conflicted with Claude's TUI rendering

3. **Visible countdown message** (A01-A15)
   - Even stderr messages disrupted status bar

4. **Latin-1 encoding for special keys** (A15)
   - Passed raw bytes through; Claude couldn't interpret them

5. **Blocking getch()** (A15)
   - Caused terminal hang on exit

6. **Structured JSONL event logging** (A01-A14)
   - Useful for forensics but added complexity
   - Could be re-added later if needed

### Interesting Approaches Not Fully Explored

1. **Sentinel/Guarded mode** (unleashed-guarded-A01.py)
   - Uses Anthropic API to have an AI review commands before approval
   - Interesting security layer, could be revisited

2. **Hard block patterns** (A12-A14)
   - Regex patterns for dangerous commands (rm, del, etc.)
   - Safe path allowlisting
   - Could be re-added for safety

3. **Tool type detection** (A14)
   - Detect whether prompt is for Bash, Write, Edit, etc.
   - Apply different approval policies per tool type

4. **getwch() instead of getch()** (A12)
   - Returns Unicode strings instead of bytes
   - Might be cleaner for key handling

---

## Cruft Analysis: Version 00010

### Code That Could Be Removed

1. **overlap_buffer (maybe)**
   ```python
   self.overlap_buffer = b""
   # ...
   search_chunk = self.overlap_buffer + raw_bytes
   self.overlap_buffer = raw_bytes[-32:]
   ```
   The footer pattern `b'Esc to cancel'` (13 bytes) could theoretically span read boundaries. However, with 8192-byte reads, this is extremely unlikely. Could be simplified to just check each chunk directly.

   **Recommendation:** Keep for safety. Minimal overhead.

2. **in_countdown flag**
   ```python
   self.in_countdown = False
   # ...
   if not self.in_countdown and FOOTER_PATTERN in search_chunk:
   ```
   Prevents re-triggering during the 0.5s delay. Still useful.

   **Recommendation:** Keep.

3. **isinstance check for data**
   ```python
   if isinstance(data, str):
       raw_bytes = data.encode('utf-8', errors='ignore')
       sys.stdout.write(data)
   else:
       raw_bytes = data
       sys.stdout.buffer.write(data)
   ```
   winpty seems to always return strings, not bytes. The else branch may never execute.

   **Recommendation:** Test and potentially simplify to string-only handling.

4. **TERM_RESET sequences**
   The individual mouse tracking disable sequences might be redundant if `\x1bc` (RIS) does a full reset anyway.

   **Recommendation:** Test with just `\x1bc`. Keep both for safety if uncertain.

### Code That Should Stay

1. **KEY_MAP** - Essential for arrow keys and special keys
2. **kbhit() polling** - Essential for clean exit
3. **sys.exit(0)** - Essential for clean exit
4. **UNLEASHED_VERSION env var** - Useful for debugging/version checking
5. **Startup banner** - Minimal, useful confirmation that wrapper is running

---

## Future Ideas and Investigations

### High Priority

1. **Fix Shift+Tab cycling**
   - Key mapping is in place but doesn't work
   - May need to investigate exact byte sequence on Windows
   - Could add debug mode to log actual bytes received

2. **Test getwch() vs getch()**
   - Archive A12 used `getwch()` (Unicode)
   - Might provide cleaner key handling

### Medium Priority

3. **Re-add structured logging (optional)**
   - Write to file, not stderr
   - Useful for debugging issues after the fact

4. **Add safety patterns (optional)**
   - Hard block dangerous commands outside Projects/
   - Re-use patterns from A12-A14

5. **Terminal resize handling (SIGWINCH)**
   - Forward resize events to PTY
   - Low priority per user - rarely resizes

### Low Priority / Research

6. **Sentinel mode**
   - AI-gated approval for suspicious commands
   - Interesting but adds latency and API cost

7. **Investigate winpty alternatives**
   - Are there other PTY libraries for Windows?
   - Could ConPTY work better?

8. **Multi-session management**
   - User mentioned running 5 sessions
   - Could track/manage multiple unleashed instances

---

## Lessons Learned

1. **Archive diving pays off** - The sys.exit(0) fix was hiding in A12 all along

2. **One change at a time** - User's discipline in testing incrementally helped isolate issues

3. **stderr affects cursor positioning** - Non-obvious interaction between our output and Claude's TUI

4. **Windows console input is weird** - Special keys come as prefix + scan code, need explicit mapping

5. **Daemon threads don't guarantee clean exit** - sys.exit(0) is sometimes necessary

---

## Files Created This Session

- `src/unleashed-00002.py` through `src/unleashed-00010.py`
- `src/unleashed.py` (promoted from 00010)
- `docs/adrs/ADR-001-pty-wrapper-architecture.md`
- `docs/reports/done/REP-001-debugging-session-2026-01-20.md` (this file)

---

## Appendix: Final v00010 Statistics

- **Lines of code:** ~140
- **Dependencies:** winpty, msvcrt (Windows built-in)
- **Key mappings:** 11 (arrows, home/end, pgup/pgdn, ins/del, shift+tab)
- **Approval delay:** 0.5 seconds
- **Exit method:** sys.exit(0)
