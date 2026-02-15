#!/usr/bin/env python3
"""
Unleashed - v00023
- New: Typeahead dedup — progressive prompt typing (❯ h, ❯ he, ❯ hel...) collapsed
  to final input only
- New: Thinking animation filter — "thinking" indicator and token counter fragments
- New: Partial render filter — short mid-repaint fragments with letter drops
- Carries forward all v22 features: CUF space fix, short-line filter, spinner
  fragment filter, sentinel gate, shared garbage filter, rate-limited mirror

Based on v00022.
"""
import os
import sys
import threading
import time
import argparse
import shutil
import ctypes
import re
from collections import deque
import json
import datetime
import subprocess
from pathlib import Path
from ctypes import wintypes

# Shared garbage filter — single source of truth for TUI noise patterns
from transcript_filters import is_garbage

VERSION = "00023"
LOG_FILE = os.path.join("logs", f"unleashed_v{VERSION}.log")

# winpty write buffer limit
PTY_WRITE_CHUNK_SIZE = 64

try:
    import winpty
except ImportError:
    sys.stderr.write(f"[v{VERSION}] FATAL: pywinpty missing.\n")
    sys.stderr.flush()
    sys.exit(1)

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

def log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

# Windows API setup
kernel32 = ctypes.windll.kernel32
STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", wintypes.BOOL),
        ("wRepeatCount", wintypes.WORD),
        ("wVirtualKeyCode", wintypes.WORD),
        ("wVirtualScanCode", wintypes.WORD),
        ("uChar", wintypes.WCHAR),
        ("dwControlKeyState", wintypes.DWORD),
    ]

class INPUT_RECORD_UNION(ctypes.Union):
    _fields_ = [
        ("KeyEvent", KEY_EVENT_RECORD),
        ("_padding", ctypes.c_byte * 16),
    ]

class INPUT_RECORD(ctypes.Structure):
    _fields_ = [
        ("EventType", wintypes.WORD),
        ("Event", INPUT_RECORD_UNION),
    ]

CLAUDE_CMD = r"C:\Users\mcwiz\AppData\Roaming\npm\claude.cmd"

# Regex to strip ANSI escape sequences from raw bytes before pattern matching.
ANSI_RE = re.compile(rb'\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]|\x1b[A-Za-z]')

MULTI_SPACE_RE = re.compile(rb' {2,}')

# ---------------------------------------------------------------------------
# Session mirror filtering
# ---------------------------------------------------------------------------

# Broader ANSI regex for mirror: catches private mode sequences (\x1b[?25h etc.)
# that the pattern-matching ANSI_RE misses.  [\x20-\x3f]* covers the full ECMA-48
# "intermediate bytes" range: digits, semicolons, ?, >, =, etc.
MIRROR_ANSI_RE = re.compile(
    rb'\x1b\[[\x20-\x3f]*[A-Za-z~]'   # CSI sequences (including ?-prefixed)
    rb'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'  # OSC sequences (BEL or ST terminator)
    rb'|\x1b[()][AB012]'               # character set selection
    rb'|\x1b[A-Za-z]'                  # two-char ESC sequences
)
# Orphaned escape sequence payloads: when \x1b is in one PTY read and the
# parameter bytes are in the next, we get bare ]0;title or [25;10H in the text.
# \x1b itself gets stripped by MIRROR_CONTROL_RE, but the payload survives.
# These regexes clean them from anywhere in the line (mid-line or start).
ORPHAN_CSI_RE = re.compile(r'\[[\d;?]+[A-Za-z~]')   # [25;10H, [2J, [?25h (needs digits)
ORPHAN_OSC_RE = re.compile(r'\]\d;.*')              # ]0;window title... (eats whole line)
# Control chars that cause overwrite behavior in tail -f.
# \r = carriage return (cursor to column 0 — overwrites current line)
# \x08 = backspace, \x07 = BEL, etc.   Keep only \n (0x0a).
MIRROR_CONTROL_RE = re.compile(rb'[\x00-\x09\x0b-\x1f\x7f]')

SPINNER_PREFIX_RE = re.compile(r'^[*✶✻✽·✢●]\s*')
# Spinner fragment: spinner char + partial status text from mid-repaint capture.
# Real spinner lines are 20+ chars ("● Running under unleashed v00021.").
# Fragments are <15 chars ("✻ Me", "✽ M a", "✶ or").
SPINNER_FRAG_RE = re.compile(r'^[*✶✻✽·✢●]\s*.{0,12}$')
SEPARATOR_RE = re.compile(r'^[\s─━═╌╍┄┅]{8,}$')  # mostly box-drawing (allows spaces)
TIMESTAMP_FRAG_RE = re.compile(r'\d{0,2}:\d{2}[:\])]')  # leaked timestamp fragments

# v23: Additional mirror noise filters
# Thinking animation: "thinking" and "thinking)" from Claude's thinking indicator
THINKING_RE = re.compile(r'^thinking\)?$')
# Bare CSI parameters leaked from ANSI stripping: "5;255m", "26l", "7ma"
BARE_CSI_PARAM_RE = re.compile(r'^\d+[;?\d]*[a-zA-Z]$')
# Pure digit noise: token counter fragments "1771187617", "1771187696"
PURE_DIGITS_RE = re.compile(r'^\d{8,}$')

# Rate-limit interval for mirror writes (seconds)
MIRROR_FLUSH_INTERVAL = 0.2

# Sentinel scope: which tool types go through the safety gate
SENTINEL_SCOPES = {
    "bash":  {"Bash"},
    "write": {"Bash", "Write", "Edit"},
    "all":   {"Bash", "Write", "Edit", "WebFetch", "WebSearch", "Skill", "Task"},
}


def mirror_strip_ansi(data: bytes) -> bytes:
    """Smart ANSI stripping for session mirror — cursor-tracking parser.

    The Ink TUI uses cursor positioning codes as whitespace between words:
        \\x1b[7;1HIt\\x1b[7;4His  →  "It" at col 1, "is" at col 4
    The gap from col 3→4 IS the space.  But it also positions characters
    individually for styled elements:
        \\x1b[5;20HC\\x1b[5;21Hl\\x1b[5;22Ha  →  C at 20, l at 21, a at 22
    No gaps — they're adjacent.

    DELETE strips both, giving "Itis" and "Claude" — words merge.
    REPLACE inserts spaces for both, giving "It is" and "C l a u d e".
    Neither works alone.

    This parser tracks cursor column position:
      - Column jump past current position → insert space (word gap)
      - Column matches current position  → nothing (adjacent character)
      - Row change → newline
      - All non-positioning ANSI → deleted
      - Control chars (except \\n) → deleted
    """
    result = []
    row = 0
    col = 0
    pos = 0
    length = len(data)

    while pos < length:
        byte = data[pos]

        # --- ESC sequence ---
        if byte == 0x1b and pos + 1 < length:
            next_byte = data[pos + 1]

            if next_byte == 0x5b:  # '[' → CSI sequence
                # Consume parameter + intermediate bytes (0x20–0x3f)
                end = pos + 2
                while end < length and 0x20 <= data[end] <= 0x3f:
                    end += 1
                # Final byte (0x40–0x7e)
                if end < length and 0x40 <= data[end] <= 0x7e:
                    final = data[end]
                    param_bytes = data[pos + 2:end]
                    end += 1

                    # Cursor Position: \x1b[row;colH or \x1b[row;colf
                    if final in (0x48, 0x66):  # H or f
                        params = param_bytes.split(b';')
                        new_row = int(params[0]) if params[0] else 1
                        new_col = int(params[1]) if len(params) > 1 and params[1] else 1
                        if new_row != row:
                            if result and result[-1] != b'\n':
                                result.append(b'\n')
                            row = new_row
                        elif new_col > col:
                            result.append(b' ')
                        col = new_col

                    # Cursor Horizontal Absolute: \x1b[colG
                    elif final == 0x47:  # G
                        new_col = int(param_bytes) if param_bytes else 1
                        if new_col > col:
                            result.append(b' ')
                        col = new_col

                    # Cursor Forward: \x1b[nC
                    # Ink uses \x1b[1C as the space between words — 62K+ per session.
                    # Any forward cursor movement represents a gap → insert space.
                    elif final == 0x43:  # C
                        n = int(param_bytes) if param_bytes else 1
                        if n >= 1:
                            result.append(b' ')
                        col += n

                    # Cursor Up/Down: row change → newline
                    elif final in (0x41, 0x42):  # A (up) or B (down)
                        n = int(param_bytes) if param_bytes else 1
                        row = row - n if final == 0x41 else row + n
                        if result and result[-1] != b'\n':
                            result.append(b'\n')

                    # All other CSI (colors, erase, scroll, etc.) — skip

                    pos = end
                    continue
                else:
                    pos = end if end < length else end
                    continue

            elif next_byte == 0x5d:  # ']' → OSC sequence
                end = pos + 2
                while end < length:
                    if data[end] == 0x07:  # BEL terminator
                        end += 1
                        break
                    if data[end] == 0x1b and end + 1 < length and data[end + 1] == 0x5c:
                        end += 2  # ST terminator
                        break
                    end += 1
                pos = end
                continue

            elif next_byte in (0x28, 0x29):  # charset selection
                pos = min(pos + 3, length)
                continue

            else:  # other ESC + letter
                pos += 2
                continue

        # --- Newline ---
        if byte == 0x0a:
            if result and result[-1] != b'\n':
                result.append(b'\n')
            row += 1
            col = 0
            pos += 1
            continue

        # --- Other control chars: skip ---
        if byte < 0x20 or byte == 0x7f:
            pos += 1
            continue

        # --- Printable byte: output and advance column ---
        result.append(data[pos:pos + 1])
        col += 1
        pos += 1

    return b''.join(result)


def strip_ansi(data: bytes) -> bytes:
    """Replace ANSI escape sequences with a space to preserve word boundaries.

    Cursor positioning codes (\x1b[row;colH) ARE the whitespace between words
    in the TUI. Simply deleting them merges words: 'Tabtoamend'. Replacing with
    a space then collapsing keeps: 'Tab to amend'.
    """
    result = ANSI_RE.sub(b' ', data)
    result = MULTI_SPACE_RE.sub(b' ', result)
    return result

# Permission prompt patterns (TUI system dialogs)
PERMISSION_PATTERNS = [
    b'Tab to amend',
    b'Do you want to proceed?',
    b'Allow this command to run?',
    b'Do you want to allow Claude to fetch this content?',  # WebFetch domain approval
]

# Model pause patterns — Claude stopping to ask pointless yes/no questions
# These are AskUserQuestion tool renders or conversational pauses
MODEL_PAUSE_PATTERNS = [
    b'Should I proceed',
    b'Should I continue',
    b'Would you like me to proceed',
    b'Would you like me to continue',
    b'Shall I proceed',
    b'Shall I continue',
    b'Want me to proceed',
    b'Want me to continue',
    b'Do you want me to proceed',
    b'Do you want me to continue',
    b'Ready to proceed',
    b'Is my plan ready',
]

# Extract tool call that triggered a permission prompt
TOOL_CALL_RE = re.compile(
    r'(Read|Write|Edit|Bash|Glob|Grep|WebFetch|WebSearch|Skill|Task|NotebookEdit)\(([^)]{1,500})\)',
    re.DOTALL
)

VK_MAP = {
    0x26: '\x1b[A', 0x28: '\x1b[B', 0x25: '\x1b[D', 0x27: '\x1b[C',
    0x24: '\x1b[H', 0x23: '\x1b[F', 0x2D: '\x1b[2~', 0x2E: '\x1b[3~',
    0x21: '\x1b[5~', 0x22: '\x1b[6~', 0x70: '\x1bOP', 0x71: '\x1bOQ',
    0x72: '\x1bOR', 0x73: '\x1bOS', 0x74: '\x1b[15~', 0x75: '\x1b[17~',
    0x76: '\x1b[18~', 0x77: '\x1b[19~', 0x78: '\x1b[20~', 0x79: '\x1b[21~',
    0x7A: '\x1b[23~', 0x7B: '\x1b[24~',
}

TERM_RESET = '\033[0m\033[?25h\033[?1000l\033[?1002l\033[?1003l\033[?1006l'

# ANSI color codes for session mirror categories
COLORS = {
    'user':       '\033[32m',    # green
    'assistant':  '\033[36m',    # cyan
    'tool':       '\033[2m',     # dim
    'permission': '\033[33m',    # yellow
    'reset':      '\033[0m',
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def classify_line(line: str) -> str:
    """Classify a cleaned line for colorization in the session mirror."""
    stripped = line.strip()
    if stripped.startswith('\u276f ') or stripped.startswith('> '):   # ❯ or >
        return 'user'
    if stripped.startswith('\u25cf') or stripped.startswith('\u23bf'):  # ● or ⎿
        return 'tool'
    if any(kw in stripped for kw in ('Do you want to proceed?', 'Allow this command to run?', 'Tab to amend', 'Esc to cancel')):
        return 'permission'
    return 'assistant'

def extract_permission_context(buffer: str) -> str:
    """Extract the tool call that triggered a permission prompt from recent PTY text."""
    matches = list(TOOL_CALL_RE.finditer(buffer))
    if matches:
        m = matches[-1]
        return f"{m.group(1)}({m.group(2)})"
    # Fallback: last 200 chars
    return buffer[-200:].strip() if buffer else "(no context)"


def extract_permission_context_structured(buffer: str) -> tuple:
    """Extract structured (tool_type, tool_args, raw_context) for sentinel routing."""
    matches = list(TOOL_CALL_RE.finditer(buffer))
    if matches:
        m = matches[-1]
        return (m.group(1), m.group(2), f"{m.group(1)}({m.group(2)})")
    return ("unknown", "", buffer[-200:].strip() if buffer else "(no context)")

BASH_EXE = r"C:\Program Files\Git\usr\bin\bash.exe"

def launch_tab(title: str, log_path: str):
    """Launch a Windows Terminal tab tailing the given log file."""
    try:
        abs_path = os.path.abspath(log_path)
        # Convert Windows path to Unix for Git Bash tail
        unix_path = abs_path.replace("\\", "/")
        if unix_path[1] == ':':
            unix_path = '/' + unix_path[0].lower() + unix_path[2:]

        # Must use full path to bash.exe — Git Bash isn't in the Windows system PATH
        cmd = f'wt.exe -w 0 nt --title "{title}" --suppressApplicationTitle "{BASH_EXE}" -c "tail -f \'{unix_path}\'"'
        log(f"Launching tab: {cmd}")
        subprocess.Popen(cmd, shell=True)
    except Exception as e:
        log(f"WARNING: Failed to launch tab '{title}': {e}")


# ---------------------------------------------------------------------------
# SessionLogger — Tab 2: timestamped, colorized transcript
# ---------------------------------------------------------------------------

SESSION_MIRROR_MAX_LINES = 10_000
SESSION_MIRROR_KEEP_LINES = 7_000  # After truncation, keep this many recent lines


class SessionLogger:
    def __init__(self, session_ts: str, joint_log: bool):
        self.path = os.path.join("logs", f"session-{session_ts}.log")
        self.joint_log = joint_log
        self.line_count = 0
        self.fh = open(self.path, "a", encoding="utf-8")
        self.fh.write(f"{'='*60}\n")
        self.fh.write(f"  Unleashed v{VERSION} Session Mirror\n")
        self.fh.write(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.fh.write(f"  Joint log (permissions inline): {joint_log}\n")
        self.fh.write(f"{'='*60}\n\n")
        self.fh.flush()
        self.line_count = 6

    def _truncate_if_needed(self):
        """Truncate mirror to keep only recent lines when it exceeds the cap."""
        if self.line_count < SESSION_MIRROR_MAX_LINES:
            return
        try:
            self.fh.flush()
            self.fh.close()
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            keep = lines[-SESSION_MIRROR_KEEP_LINES:]
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(f"[--- truncated {len(lines) - len(keep)} older lines ---]\n")
                f.writelines(keep)
            self.fh = open(self.path, "a", encoding="utf-8")
            self.line_count = len(keep) + 1
            log(f"Session mirror truncated: {len(lines)} -> {self.line_count} lines")
        except Exception as e:
            log(f"Session mirror truncation failed: {e}")
            self.fh = open(self.path, "a", encoding="utf-8")

    def write_raw(self, text: str):
        """Write text to log. No timestamps — mirror is a live tail."""
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if i < len(lines) - 1:
                self.fh.write(f"{line}\n")
                self.line_count += 1
            elif line:
                self.fh.write(line)
        self.fh.flush()
        self._truncate_if_needed()

    def write_event(self, event_type: str, detail: str):
        """Write a highlighted event (permission, model pause, etc)."""
        c = COLORS['permission']
        r = COLORS['reset']
        self.fh.write(f"\n{c}>>> [{event_type}] {detail}{r}\n\n")
        self.fh.flush()
        self.line_count += 3

    def close(self):
        if self.fh and not self.fh.closed:
            self.fh.write(f"\n{'='*60}\n")
            self.fh.write(f"  Session ended: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            self.fh.write(f"{'='*60}\n")
            self.fh.flush()
            self.fh.close()


# ---------------------------------------------------------------------------
# FrictionLogger — Tab 3: permission prompt tracking
# ---------------------------------------------------------------------------

class FrictionLogger:
    def __init__(self, session_ts: str, session_id: str):
        self.human_path = os.path.join("logs", f"friction-{session_ts}.log")
        self.jsonl_path = os.path.join("logs", f"friction-{session_ts}.jsonl")
        self.session_id = session_id
        self.session_start = time.time()
        self.prompt_count = 0
        self.fh_human = open(self.human_path, "a", encoding="utf-8")
        self.fh_jsonl = open(self.jsonl_path, "a", encoding="utf-8")
        self.fh_human.write(f"{'='*60}\n")
        self.fh_human.write(f"  Permission Friction Logger — {session_id}\n")
        self.fh_human.write(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.fh_human.write(f"{'='*60}\n\n")
        self.fh_human.write("Waiting for permission prompts...\n\n")
        self.fh_human.flush()

    def record_prompt(self, pattern_matched: str, raw_context: str, event_type: str = "permission_prompt"):
        self.prompt_count += 1
        now = time.time()
        elapsed = int(now - self.session_start)

        # JSONL record
        record = {
            "ts": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "session_id": self.session_id,
            "elapsed_s": elapsed,
            "type": event_type,
            "pattern_matched": pattern_matched,
            "raw_context": raw_context,
            "auto_approved": True,
            "prompt_number": self.prompt_count
        }
        self.fh_jsonl.write(json.dumps(record) + "\n")
        self.fh_jsonl.flush()

        # Human-readable
        ts = time.strftime('%H:%M:%S')
        label = "Permission" if event_type == "permission_prompt" else "Model Pause"
        self.fh_human.write(f"--- {label} #{self.prompt_count} ---\n")
        self.fh_human.write(f"[{ts}] Type: {event_type}\n")
        self.fh_human.write(f"[{ts}] Pattern: {pattern_matched}\n")
        self.fh_human.write(f"[{ts}] Context: {raw_context}\n")
        self._write_tally()
        self.fh_human.write("\n")
        self.fh_human.flush()

    def _write_tally(self):
        elapsed = time.time() - self.session_start
        elapsed_min = elapsed / 60
        if self.prompt_count > 0:
            rate_s = elapsed / self.prompt_count
            if rate_s >= 60:
                rate_str = f"1 every {rate_s / 60:.1f}m"
            else:
                rate_str = f"1 every {rate_s:.0f}s"
        else:
            rate_str = "n/a"
        self.fh_human.write(
            f"[PROMPTS: {self.prompt_count} | SESSION: {elapsed_min:.0f}m | RATE: {rate_str}]\n"
        )

    def close(self):
        # Write final summary
        elapsed = time.time() - self.session_start
        elapsed_min = elapsed / 60
        summary = (
            f"\n{'='*60}\n"
            f"  SESSION SUMMARY\n"
            f"  Duration: {elapsed_min:.1f} minutes\n"
            f"  Total permission prompts: {self.prompt_count}\n"
        )
        if self.prompt_count > 0:
            rate = elapsed / self.prompt_count
            summary += f"  Average rate: 1 every {rate:.0f}s ({rate/60:.1f}m)\n"
        summary += f"  JSONL data: {self.jsonl_path}\n"
        summary += f"{'='*60}\n"

        self.fh_human.write(summary)
        self.fh_human.flush()

        for fh in (self.fh_human, self.fh_jsonl):
            if fh and not fh.closed:
                fh.close()


# ---------------------------------------------------------------------------
# Main Unleashed class
# ---------------------------------------------------------------------------

class Unleashed:
    def __init__(self, cwd=None, mirror=False, friction=False, joint_log=False,
                 sentinel_shadow=False, sentinel_scope=None, claude_args=None):
        self.cwd = cwd or os.getcwd()
        self.running = True
        self.in_approval = False
        self.overlap_buffer = b""
        self.stdin_handle = None
        self.original_mode = None
        # v18: tab features
        self.mirror = mirror
        self.friction = friction
        self.joint_log = joint_log
        self.claude_args = claude_args or []  # extra args passed through to claude.cmd
        self.session_logger = None
        self.friction_logger = None
        self.context_buffer = ""         # last 2048 chars of stripped PTY text
        self._mirror_recent = deque(maxlen=32)  # recent lines for mirror dedup
        self._last_auto_answer_time = 0  # cooldown: prevent _auto_answer loop
        # v20: raw transcript save
        self.transcript_file = None
        # v21: rate-limited mirror writes
        self._mirror_buffer = b""
        self._mirror_last_flush = 0.0
        # v23: typeahead prompt buffer
        self._pending_prompt = None
        # v21: sentinel integration
        self.sentinel_shadow = sentinel_shadow
        self.sentinel_scope = sentinel_scope
        self.sentinel_gate = None
        self._shadow_fh = None
        log(f"Initialized v{VERSION} in {self.cwd} (mirror={mirror}, friction={friction}, joint_log={joint_log}, sentinel_shadow={sentinel_shadow}, sentinel_scope={sentinel_scope}, claude_args={self.claude_args})")

    def _setup_transcript(self):
        """Set up raw transcript file in the target project's data/unleashed/ dir."""
        transcript_dir = Path(self.cwd) / 'data' / 'unleashed'
        transcript_dir.mkdir(parents=True, exist_ok=True)
        # Auto-cleanup: delete .raw files older than 7 days
        self._cleanup_old_transcripts(transcript_dir)
        project = Path(self.cwd).name
        timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        path = transcript_dir / f'{project}-{timestamp}.raw'
        self.transcript_file = open(path, 'ab')
        log(f"Transcript: {path}")
        sys.stderr.write(f"[v{VERSION}] Transcript: {path}\n")
        sys.stderr.flush()

    def _cleanup_old_transcripts(self, transcript_dir: Path, max_age_days: int = 7):
        """Delete .raw transcript files older than max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        cleaned = 0
        for f in transcript_dir.glob('*.raw'):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    cleaned += 1
            except Exception:
                pass
        if cleaned:
            log(f"Transcript cleanup: deleted {cleaned} files older than {max_age_days}d")

    def _close_transcript(self):
        """Close transcript file."""
        if self.transcript_file:
            self.transcript_file.close()
            self.transcript_file = None

    def _setup_console(self):
        self.stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        self.original_mode = wintypes.DWORD()
        kernel32.GetConsoleMode(self.stdin_handle, ctypes.byref(self.original_mode))
        new_mode = ENABLE_EXTENDED_FLAGS | ENABLE_VIRTUAL_TERMINAL_INPUT
        kernel32.SetConsoleMode(self.stdin_handle, new_mode)

    def _restore_console(self):
        if self.stdin_handle and self.original_mode:
            kernel32.SetConsoleMode(self.stdin_handle, self.original_mode)

    def _normalize_surrogates(self, text):
        try:
            return text.encode('utf-16', 'surrogatepass').decode('utf-16')
        except:
            return text

    def _pty_write_chunked(self, pty, data):
        data = self._normalize_surrogates(data)
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + PTY_WRITE_CHUNK_SIZE]
            pty.write(chunk)
            offset += PTY_WRITE_CHUNK_SIZE
            if offset < len(data):
                time.sleep(0.001)

    def _reader_stdin(self, pty):
        max_events = 1024
        input_buffer = (INPUT_RECORD * max_events)()
        events_read = wintypes.DWORD()
        while self.running:
            try:
                events_available = wintypes.DWORD()
                kernel32.GetNumberOfConsoleInputEvents(self.stdin_handle, ctypes.byref(events_available))
                if events_available.value == 0:
                    time.sleep(0.005)
                    continue
                success = kernel32.ReadConsoleInputW(self.stdin_handle, input_buffer, max_events, ctypes.byref(events_read))
                if not success or events_read.value == 0:
                    time.sleep(0.005)
                    continue
                output_chars = []
                for i in range(events_read.value):
                    record = input_buffer[i]
                    if record.EventType != KEY_EVENT: continue
                    key_event = record.Event.KeyEvent
                    if not key_event.bKeyDown: continue
                    vk = key_event.wVirtualKeyCode
                    char = key_event.uChar
                    ctrl_state = key_event.dwControlKeyState
                    left_ctrl = ctrl_state & 0x0008
                    right_ctrl = ctrl_state & 0x0004
                    shift = ctrl_state & 0x0010
                    if vk == 0x43 and (left_ctrl or right_ctrl):
                        self.running = False
                        return
                    if vk == 0x1B: output_chars.append('\x1b'); continue
                    if vk == 0x09 and shift: output_chars.append('\x1b[Z'); continue
                    if vk == 0x09: output_chars.append('\t'); continue
                    if vk == 0x0D: output_chars.append('\r'); continue
                    if vk == 0x08: output_chars.append('\x7f'); continue
                    if vk in VK_MAP: output_chars.append(VK_MAP[vk]); continue
                    if left_ctrl or right_ctrl:
                        if 0x41 <= vk <= 0x5A:
                            output_chars.append(chr(vk - 0x40))
                            continue
                    if char and ord(char) > 0: output_chars.append(char)
                if output_chars:
                    self._pty_write_chunked(pty, ''.join(output_chars))
            except: break

    def _resize_monitor(self, pty):
        """Poll for terminal resize and forward new dimensions to PTY."""
        last_size = shutil.get_terminal_size((120, 40))
        last_cols, last_rows = last_size.columns, last_size.lines
        log(f"Resize monitor started: {last_cols}x{last_rows}")
        while self.running and pty.isalive():
            try:
                cur_size = shutil.get_terminal_size((last_cols, last_rows))
                cur_cols, cur_rows = cur_size.columns, cur_size.lines
                if cur_cols != last_cols or cur_rows != last_rows:
                    log(f"Terminal resized: {last_cols}x{last_rows} -> {cur_cols}x{cur_rows}")
                    pty.setwinsize(cur_rows, cur_cols)
                    last_cols, last_rows = cur_cols, cur_rows
                time.sleep(0.3)
            except Exception as e:
                log(f"Resize monitor error: {e}")
                break

    def _flush_mirror(self, final=False):
        """Flush accumulated mirror buffer through the garbage filter.

        Args:
            final: True when called at session end (flushes pending prompt).
                   False during normal 200ms timer operation (preserves prompt buffer).
        """
        if not self._mirror_buffer or not self.session_logger:
            if final and self._pending_prompt is not None and self.session_logger:
                try:
                    self._flush_pending_prompt(self.session_logger.fh)
                    self.session_logger.fh.flush()
                except Exception:
                    pass
            return
        self._log_to_mirror(self._mirror_buffer)
        self._mirror_buffer = b""
        if final and self._pending_prompt is not None:
            try:
                self._flush_pending_prompt(self.session_logger.fh)
                self.session_logger.fh.flush()
            except Exception:
                pass

    @staticmethod
    def _is_sparse_fragment(line):
        """Detect char-by-char rendering fragments: 'W p', 'e n 2 0', 'D i'.

        When Ink positions characters individually with CUF, the space fix creates
        spaced-out noise. Real content has longer words; fragments have mostly
        1-2 char "words" with spaces between them.
        """
        if len(line) > 12:
            return False
        words = line.split()
        if len(words) < 2:
            return False
        avg_word_len = sum(len(w) for w in words) / len(words)
        return avg_word_len < 2.5

    def _log_to_mirror(self, raw_data):
        """Write filtered PTY output to session mirror as clean scrollable log.

        v23 pipeline:
          1. mirror_strip_ansi() — cursor-tracking parser, spaces at word gaps
          2. Split into lines
          3. Strip orphaned CSI/OSC payloads (split across PTY reads)
          4. v23: Prompt detection (❯) → typeahead buffer (before filters!)
          5. Short-line filter (<3 chars)
          6. Spinner fragment filter (<15 chars with spinner prefix)
          7. v23: Sparse fragment filter (char-by-char render noise)
          8. v23: Thinking/ANSI/digit noise filters
          9. is_garbage(stripped) — shared 95-pattern filter
         10. Dedup against recent 32 lines
        """
        if not self.session_logger:
            return
        try:
            if isinstance(raw_data, str):
                raw_bytes = raw_data.encode('utf-8', errors='ignore')
            else:
                raw_bytes = raw_data

            clean = mirror_strip_ansi(raw_bytes)
            text = clean.decode('utf-8', errors='replace')

            fh = self.session_logger.fh
            wrote = False
            for line in text.split('\n'):
                stripped = line.strip()
                if not stripped:
                    continue

                # Strip orphaned escape sequence payloads spanning PTY reads
                stripped = ORPHAN_CSI_RE.sub('', stripped)
                stripped = ORPHAN_OSC_RE.sub('', stripped)
                stripped = stripped.strip()
                if not stripped:
                    continue

                # v23: Prompt detection FIRST — ❯ lines are user input,
                # not garbage. Route straight to typeahead buffer.
                if stripped.startswith('\u276f'):
                    if self._pending_prompt is not None:
                        prev = self._pending_prompt
                        if stripped.startswith(prev) or prev.startswith(stripped):
                            # Typing continuation — keep the longer one
                            self._pending_prompt = stripped if len(stripped) >= len(prev) else prev
                            continue
                        else:
                            # Different prompt — flush the previous one
                            self._flush_pending_prompt(fh)
                            wrote = True
                    self._pending_prompt = stripped
                    continue

                # Short-line filter: 1-2 char lines are TUI rendering fragments
                if len(stripped) < 3:
                    continue

                # Spinner fragment filter: mid-repaint captures of spinner + partial text
                if SPINNER_FRAG_RE.match(stripped):
                    continue

                # v23: Sparse fragment filter — char-by-char CUF noise
                if self._is_sparse_fragment(stripped):
                    continue

                # v23: Thinking animation, bare CSI params, pure digit noise
                if THINKING_RE.match(stripped):
                    continue
                if BARE_CSI_PARAM_RE.match(stripped):
                    continue
                if PURE_DIGITS_RE.match(stripped):
                    continue

                # v23: Short ellipsis fragments — partial renders ending in …
                if len(stripped) <= 8 and stripped.endswith('\u2026'):
                    continue

                # Shared garbage filter — 95 patterns, single source of truth
                if is_garbage(stripped):
                    continue

                # Deduplicate against recent lines BEFORE flushing prompt.
                # TUI chrome (separator bars, status) repeats between keystrokes;
                # flushing the prompt on duplicates would break typeahead collapsing.
                normalized = SPINNER_PREFIX_RE.sub('', stripped).strip()
                if normalized in self._mirror_recent:
                    continue

                # New content — flush any pending prompt first (preserves order)
                if self._pending_prompt is not None:
                    self._flush_pending_prompt(fh)
                    wrote = True

                self._mirror_recent.append(normalized)
                fh.write(f"{stripped}\n")
                wrote = True

            # NOTE: Do NOT flush pending prompt at batch boundaries.
            # Typeahead keystrokes span multiple 200ms batches — flushing
            # here would emit every intermediate prompt instead of collapsing.
            # Pending prompt is flushed only when:
            #   - new (non-duplicate) content line arrives (above)
            #   - a non-prefix prompt arrives (above)
            #   - the session ends (_flush_mirror(final=True))

            if wrote:
                fh.flush()
        except Exception as e:
            log(f"Mirror write error: {e}")

    def _flush_pending_prompt(self, fh):
        """Write buffered prompt line to mirror and clear buffer."""
        if self._pending_prompt is None:
            return
        normalized = SPINNER_PREFIX_RE.sub('', self._pending_prompt).strip()
        if normalized not in self._mirror_recent:
            self._mirror_recent.append(normalized)
            fh.write(f"{self._pending_prompt}\n")
        self._pending_prompt = None

    def _reader_pty(self, pty):
        while self.running and pty.isalive():
            try:
                data = pty.read(8192)
                if not data: continue
                if isinstance(data, str):
                    raw_bytes = data.encode('utf-8', errors='ignore')
                    sys.stdout.write(data)
                else:
                    raw_bytes = data
                    sys.stdout.buffer.write(data)
                sys.stdout.flush()

                # v20: tee to raw transcript file
                if self.transcript_file:
                    self.transcript_file.write(raw_bytes)
                    self.transcript_file.flush()

                search_chunk = self.overlap_buffer + raw_bytes
                clean_chunk = strip_ansi(search_chunk)

                # Update context buffer for friction/pause context (human-readable).
                # Uses cursor-tracking parser — not strip_ansi() which adds
                # spurious spaces from character-by-character TUI rendering.
                mirror_text = mirror_strip_ansi(raw_bytes).decode('utf-8', errors='replace')
                self.context_buffer = (self.context_buffer + mirror_text)[-2048:]

                # v21: Rate-limited mirror writes — accumulate 200ms of data
                # before processing. Larger chunks = fewer mid-repaint reads,
                # fewer letter drops, less garbage volume.
                if self.session_logger:
                    self._mirror_buffer += raw_bytes
                    now = time.time()
                    if now - self._mirror_last_flush >= MIRROR_FLUSH_INTERVAL:
                        self._flush_mirror()
                        self._mirror_last_flush = now

                # Matching logic — permission prompts and model pauses
                if not self.in_approval:
                    # 1. Check permission prompt patterns
                    found_permission = None
                    for p in PERMISSION_PATTERNS:
                        if p in clean_chunk:
                            found_permission = p
                            break

                    if found_permission:
                        pattern_str = found_permission.decode('utf-8', errors='replace')
                        log(f"PERMISSION MATCHED: {found_permission!r}")
                        tool_type, tool_args, context = extract_permission_context_structured(self.context_buffer)

                        # Phase 0: Shadow logging — record what sentinel would see
                        if self._shadow_fh:
                            ts = time.strftime('%H:%M:%S')
                            self._shadow_fh.write(f"--- [{ts}] ---\n")
                            self._shadow_fh.write(f"Tool: {tool_type}\n")
                            self._shadow_fh.write(f"Args: {tool_args[:300]}\n")
                            self._shadow_fh.write(f"Context: {context[:300]}\n\n")
                            self._shadow_fh.flush()

                        if self.friction_logger:
                            self.friction_logger.record_prompt(pattern_str, context, "permission_prompt")

                        if self.session_logger:
                            self.session_logger.write_event("PERMISSION", f"{pattern_str} | {context}")

                        self.do_approval(pty, tool_type=tool_type, tool_args=tool_args)

                    else:
                        # 2. Check model pause patterns (Claude asking pointless yes/no)
                        found_pause = None
                        for p in MODEL_PAUSE_PATTERNS:
                            if p in clean_chunk:
                                found_pause = p
                                break

                        if found_pause and (time.time() - self._last_auto_answer_time > 10):
                            pattern_str = found_pause.decode('utf-8', errors='replace')
                            log(f"MODEL PAUSE MATCHED: {found_pause!r}")
                            context = self.context_buffer[-300:].strip()

                            if self.friction_logger:
                                self.friction_logger.record_prompt(pattern_str, context, "model_pause")

                            if self.session_logger:
                                self.session_logger.write_event("MODEL PAUSE", f"{pattern_str}")

                            # Auto-answer: select first option
                            self._auto_answer(pty)

                        elif b'Esc to cancel' in clean_chunk:
                            log(f"DEBUG: Found 'Esc to cancel' but no match in clean chunk: {clean_chunk[-256:]!r}")

                self.overlap_buffer = raw_bytes[-256:]
            except Exception as e:
                log(f"PTY Reader Error: {e}")
                break

    def do_approval(self, pty, tool_type="unknown", tool_args=""):
        """Auto-approve permission prompt, optionally through sentinel gate."""
        self.in_approval = True
        self.overlap_buffer = b""

        # Sentinel gate: check if this tool type is in scope
        if self.sentinel_gate and self.sentinel_scope:
            scope_tools = SENTINEL_SCOPES.get(self.sentinel_scope, set())
            if tool_type in scope_tools:
                log(f"SENTINEL CHECK: {tool_type}({tool_args[:100]})")
                t = threading.Thread(
                    target=self._sentinel_check,
                    args=(pty, tool_type, tool_args),
                    daemon=True
                )
                t.start()
                return  # PTY reader resumes; in_approval stays True until sentinel thread finishes

        # Default: instant approval
        log("Executing auto-approval...")
        time.sleep(0.1)
        pty.write('\r')
        log("Sent CR to PTY")
        time.sleep(0.1)
        self.in_approval = False

    def _sentinel_check(self, pty, tool_type, tool_args):
        """Worker thread: call sentinel gate then approve or block."""
        try:
            start = time.time()
            verdict, reason = self.sentinel_gate.check(tool_type, tool_args, self.cwd)
            elapsed_ms = int((time.time() - start) * 1000)

            if verdict == "ALLOW":
                log(f"SENTINEL ALLOW ({elapsed_ms}ms): {tool_type}({tool_args[:100]})")
                time.sleep(0.1)
                pty.write('\r')
                log("Sent CR to PTY (sentinel approved)")
                time.sleep(0.1)

            elif verdict == "BLOCK":
                log(f"SENTINEL BLOCK ({elapsed_ms}ms): {reason} | {tool_type}({tool_args[:100]})")
                # Do NOT send CR — user sees the permission prompt and decides manually
                sys.stderr.write(f"\n\033[91m[SENTINEL] BLOCKED: {reason}\033[0m\n")
                sys.stderr.flush()

            else:  # ERROR — fail open
                log(f"SENTINEL ERROR ({elapsed_ms}ms): {reason} | {tool_type}({tool_args[:100]})")
                sys.stderr.write(f"\n\033[93m[SENTINEL] API error, fail-open: {reason[:80]}\033[0m\n")
                sys.stderr.flush()
                time.sleep(0.1)
                pty.write('\r')
                time.sleep(0.1)

            if self.friction_logger:
                self.friction_logger.record_prompt(
                    f"sentinel:{verdict.lower()}",
                    f"{tool_type}({tool_args[:200]}) ({elapsed_ms}ms)",
                    "sentinel_check"
                )

        except Exception as e:
            log(f"SENTINEL THREAD EXCEPTION: {e}")
            # Fail open
            try:
                time.sleep(0.1)
                pty.write('\r')
                time.sleep(0.1)
            except Exception:
                pass

        finally:
            self.in_approval = False

    def _auto_answer(self, pty):
        """Auto-answer model pause questions by selecting option 1 (yes/proceed).

        Claude's AskUserQuestion renders numbered options like:
          1. Yes (Recommended)
          2. No
        Sending '1\r' selects the first option. For plain text questions
        that just need Enter, CR alone works too.
        """
        log("Auto-answering model pause...")
        self._last_auto_answer_time = time.time()
        self.in_approval = True
        self.overlap_buffer = b""
        time.sleep(0.2)  # slightly longer than permission — let the UI render
        pty.write('1\r')
        log("Sent '1\\r' to PTY")
        time.sleep(0.1)
        self.in_approval = False

    def run(self):
        sys.stderr.write(f"[Unleashed v{VERSION}] Starting...\n")
        sys.stderr.flush()

        # v20: set up raw transcript before anything else
        self._setup_transcript()

        # Read terminal size BEFORE changing console mode to avoid
        # race condition where shutil.get_terminal_size() returns
        # incorrect dimensions after mode change, causing Ink to
        # render the status bar character-per-line at startup.
        try:
            term_size = shutil.get_terminal_size((120, 40))
            cols, rows = term_size.columns, term_size.lines
        except:
            cols, rows = 120, 40

        self._setup_console()

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["UNLEASHED_VERSION"] = VERSION

        try:
            claude_cmd = ['cmd', '/c', CLAUDE_CMD] + self.claude_args
            log(f"Spawning: {claude_cmd}")
            pty = winpty.PtyProcess.spawn(claude_cmd, dimensions=(rows, cols), cwd=self.cwd, env=env)
        except Exception as e:
            sys.stderr.write(f"[v{VERSION}] Spawn FAILED: {e}\n")
            log(f"Spawn FAILED: {e}")
            self._restore_console()
            self._close_transcript()
            return

        # v21: Initialize sentinel shadow log
        session_ts = time.strftime("%Y%m%d-%H%M%S")
        session_id = f"unleashed-{session_ts}"

        if self.sentinel_shadow:
            shadow_path = os.path.join("logs", f"sentinel-shadow-{session_ts}.log")
            self._shadow_fh = open(shadow_path, "a", encoding="utf-8")
            self._shadow_fh.write(f"=== Sentinel Shadow Log — {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
            self._shadow_fh.flush()
            log(f"Sentinel shadow log: {shadow_path}")
            sys.stderr.write(f"[v{VERSION}] Sentinel shadow: {shadow_path}\n")
            sys.stderr.flush()

        # v21: Initialize sentinel gate
        if self.sentinel_scope:
            sentinel_key = os.environ.get("AGENTOS_SENTINEL_KEY")
            if sentinel_key:
                from sentinel_gate import SentinelGate
                self.sentinel_gate = SentinelGate(api_key=sentinel_key)
                log(f"Sentinel gate enabled (scope={self.sentinel_scope})")
                sys.stderr.write(f"[v{VERSION}] Sentinel gate: ACTIVE (scope={self.sentinel_scope})\n")
                sys.stderr.flush()
            else:
                log("WARNING: --sentinel-scope set but AGENTOS_SENTINEL_KEY not found, running without sentinel")
                sys.stderr.write(f"[v{VERSION}] WARNING: AGENTOS_SENTINEL_KEY not set, sentinel disabled\n")
                sys.stderr.flush()

        # v18: Initialize loggers and launch companion tabs

        if self.mirror:
            self.session_logger = SessionLogger(session_ts, self.joint_log)
            log(f"Session mirror: {self.session_logger.path}")

        if self.friction:
            self.friction_logger = FrictionLogger(session_ts, session_id)
            log(f"Friction logger: {self.friction_logger.human_path}")

        # Launch tabs AFTER creating log files so tail -f has a file to open
        if self.mirror:
            launch_tab("Session Mirror", self.session_logger.path)

        if self.friction:
            launch_tab("Friction", self.friction_logger.human_path)

        t1 = threading.Thread(target=self._reader_stdin, args=(pty,), daemon=True)
        t2 = threading.Thread(target=self._reader_pty, args=(pty,), daemon=True)
        t3 = threading.Thread(target=self._resize_monitor, args=(pty,), daemon=True)
        t1.start()
        t2.start()
        t3.start()

        try:
            while self.running and pty.isalive():
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            time.sleep(0.2)

            # v21: flush any remaining mirror buffer before closing
            # v23: final=True also flushes the typeahead prompt buffer
            self._flush_mirror(final=True)

            # Close loggers and transcript before PTY termination
            self._close_transcript()
            if self._shadow_fh:
                self._shadow_fh.close()
            if self.sentinel_gate:
                stats = self.sentinel_gate.stats
                log(f"Sentinel stats: {stats}")
                sys.stderr.write(f"[v{VERSION}] Sentinel stats: {stats}\n")
                sys.stderr.flush()
            if self.session_logger:
                self.session_logger.close()
            if self.friction_logger:
                self.friction_logger.close()

            pty.terminate()
            self._restore_console()
            sys.stdout.write(TERM_RESET)
            sys.stdout.write('\x1bc')
            sys.stdout.flush()
            log("Shutting down")
            sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Unleashed v{VERSION} — Shared Filter + Rate-Limited Mirror + Sentinel")
    parser.add_argument("--cwd", default=None, help="Working directory for Claude Code")
    parser.add_argument("--mirror", action="store_true", default=True, help="Open session mirror tab (default: on)")
    parser.add_argument("--no-mirror", action="store_false", dest="mirror", help="Disable session mirror tab")
    parser.add_argument("--friction", action="store_true", default=True, help="Open permission friction logger tab (default: on)")
    parser.add_argument("--no-friction", action="store_false", dest="friction", help="Disable permission friction logger tab")
    parser.add_argument("--joint-log", action="store_true", help="Include permissions inline in session mirror (implies --mirror)")
    parser.add_argument("--sentinel-shadow", action="store_true", help="Log sentinel context for every approval (shadow mode, no API calls)")
    parser.add_argument("--sentinel", action="store_true", help="Enable sentinel gate for Bash commands (alias for --sentinel-scope bash)")
    parser.add_argument("--sentinel-scope", default=None, choices=["bash", "write", "all"],
                        help="Enable sentinel gate: bash=Bash only, write=Bash+Write+Edit, all=all tools")

    # parse_known_args: unleashed flags are consumed, everything else passes to claude.cmd
    args, claude_args = parser.parse_known_args()

    # --joint-log implies --mirror
    if args.joint_log:
        args.mirror = True

    # --sentinel is alias for --sentinel-scope bash
    sentinel_scope = args.sentinel_scope
    if args.sentinel and not sentinel_scope:
        sentinel_scope = "bash"

    Unleashed(
        cwd=args.cwd,
        mirror=args.mirror,
        friction=args.friction,
        joint_log=args.joint_log,
        sentinel_shadow=args.sentinel_shadow,
        sentinel_scope=sentinel_scope,
        claude_args=claude_args
    ).run()
