#!/usr/bin/env python3
"""
Unleashed-G - v19
Gemini CLI auto-approval wrapper.

Based on unleashed-g-18.py.

v19 changes:
- Fix residual "1" leak: increase pre-approval delay from 0.1s to 0.2s
  to let Gemini's Ink UI finish rendering before we send CR
- Add raw PTY tee to session log (logs/gemini-session-{ts}.log)
- Launch companion tab (tail -f on session log) for live visibility
- Add --no-mirror flag to disable companion tab
- Add friction logger tab (permission prompt tracking) — triplet is default
"""
import os
import sys
import atexit
import subprocess
import threading
import time
import datetime
import json
import argparse
import shutil
import ctypes
from ctypes import wintypes

VERSION = "g-19"
LOG_FILE = os.path.join("logs", f"unleashed-{VERSION}.log")

# winpty write buffer limit - very small chunks to handle UTF-16 expansion on Windows
PTY_WRITE_CHUNK_SIZE = 64  # Small to account for UTF-16 surrogate pairs

try:
    import winpty
except ImportError:
    sys.stderr.write(f"[v{VERSION}] FATAL: pywinpty missing.\n")
    sys.stderr.flush()
    sys.exit(1)

def log(msg):
    os.makedirs("logs", exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

# Windows API setup
kernel32 = ctypes.windll.kernel32

# Constants
STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001

# Console mode flags
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
ENABLE_WINDOW_INPUT = 0x0008
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_INSERT_MODE = 0x0020
ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_AUTO_POSITION = 0x0100
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

# Structures for ReadConsoleInput
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

# Gemini CLI path
GEMINI_CMD = r"C:\Users\mcwiz\AppData\Roaming\npm\gemini.cmd"

# Gemini permission prompt pattern
# Gemini shows: "⠦ Waiting for user confirmation ..." (spinner varies)
FOOTER_PATTERN = b'Waiting for user confirmation'

# Virtual key codes to ANSI escape sequences
VK_MAP = {
    0x26: '\x1b[A',   # VK_UP
    0x28: '\x1b[B',   # VK_DOWN
    0x25: '\x1b[D',   # VK_LEFT
    0x27: '\x1b[C',   # VK_RIGHT
    0x24: '\x1b[H',   # VK_HOME
    0x23: '\x1b[F',   # VK_END
    0x2D: '\x1b[2~',  # VK_INSERT
    0x2E: '\x1b[3~',  # VK_DELETE
    0x21: '\x1b[5~',  # VK_PRIOR (Page Up)
    0x22: '\x1b[6~',  # VK_NEXT (Page Down)
    0x70: '\x1bOP',   # F1
    0x71: '\x1bOQ',   # F2
    0x72: '\x1bOR',   # F3
    0x73: '\x1bOS',   # F4
    0x74: '\x1b[15~', # F5
    0x75: '\x1b[17~', # F6
    0x76: '\x1b[18~', # F7
    0x77: '\x1b[19~', # F8
    0x78: '\x1b[20~', # F9
    0x79: '\x1b[21~', # F10
    0x7A: '\x1b[23~', # F11
    0x7B: '\x1b[24~', # F12
}

# Terminal reset sequences
TERM_RESET = (
    '\033[0m'       # Reset all attributes (colors, etc.)
    '\033[?25h'     # Show cursor
    '\033[?1000l'   # Disable mouse tracking
    '\033[?1002l'   # Disable mouse button tracking
    '\033[?1003l'   # Disable all mouse tracking
    '\033[?1006l'   # Disable SGR mouse mode
)

BASH_EXE = r"C:\Program Files\Git\usr\bin\bash.exe"

def launch_tab(title: str, log_path: str):
    """Launch a Windows Terminal tab tailing the given log file."""
    try:
        abs_path = os.path.abspath(log_path)
        unix_path = abs_path.replace("\\", "/")
        if unix_path[1] == ':':
            unix_path = '/' + unix_path[0].lower() + unix_path[2:]
        cmd = f'wt.exe -w 0 nt --title "{title}" --suppressApplicationTitle "{BASH_EXE}" -c "tail -f \'{unix_path}\'"'
        log(f"Launching tab: {cmd}")
        subprocess.Popen(cmd, shell=True)
    except Exception as e:
        log(f"WARNING: Failed to launch tab '{title}': {e}")


# ---------------------------------------------------------------------------
# FrictionLogger — Tab 3: permission prompt tracking
# ---------------------------------------------------------------------------

class FrictionLogger:
    def __init__(self, session_ts: str):
        self.human_path = os.path.join("logs", f"gemini-friction-{session_ts}.log")
        self.jsonl_path = os.path.join("logs", f"gemini-friction-{session_ts}.jsonl")
        self.session_start = time.time()
        self.prompt_count = 0
        self.fh_human = open(self.human_path, "a", encoding="utf-8")
        self.fh_jsonl = open(self.jsonl_path, "a", encoding="utf-8")
        self.fh_human.write(f"{'='*60}\n")
        self.fh_human.write(f"  Gemini Permission Friction Logger\n")
        self.fh_human.write(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.fh_human.write(f"{'='*60}\n\n")
        self.fh_human.write("Waiting for permission prompts...\n\n")
        self.fh_human.flush()

    def record_prompt(self):
        self.prompt_count += 1
        now = time.time()
        elapsed = int(now - self.session_start)

        # JSONL record
        record = {
            "ts": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "elapsed_s": elapsed,
            "type": "permission_prompt",
            "pattern": "Waiting for user confirmation",
            "auto_approved": True,
            "prompt_number": self.prompt_count
        }
        self.fh_jsonl.write(json.dumps(record) + "\n")
        self.fh_jsonl.flush()

        # Human-readable
        ts = time.strftime('%H:%M:%S')
        self.fh_human.write(f"--- Permission #{self.prompt_count} ---\n")
        self.fh_human.write(f"[{ts}] Auto-approved (sent CR after 0.2s)\n")

        # Tally
        elapsed_min = (now - self.session_start) / 60
        if self.prompt_count > 0:
            rate_s = (now - self.session_start) / self.prompt_count
            rate_str = f"1 every {rate_s / 60:.1f}m" if rate_s >= 60 else f"1 every {rate_s:.0f}s"
        else:
            rate_str = "n/a"
        self.fh_human.write(
            f"[PROMPTS: {self.prompt_count} | SESSION: {elapsed_min:.0f}m | RATE: {rate_str}]\n\n"
        )
        self.fh_human.flush()

    def close(self):
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


class UnleashedG:
    def __init__(self, cwd=None, mirror=True, friction=True):
        self.cwd = cwd or os.getcwd()
        self.running = True
        self.in_approval = False
        self.overlap_buffer = b""
        self.stdin_handle = None
        self.original_mode = None
        self.mirror = mirror
        self.friction = friction
        self.session_fh = None  # raw tee file handle
        self.friction_logger = None
        self.approval_count = 0          # #46: total auto-approvals this session
        self.session_start = None        # #46: set in run()

    def _setup_console(self):
        """Set up console for raw input - minimal mode changes"""
        self.stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)

        # Save original mode
        self.original_mode = wintypes.DWORD()
        kernel32.GetConsoleMode(self.stdin_handle, ctypes.byref(self.original_mode))

        # Minimal raw mode: disable line input and echo, but keep virtual terminal input
        new_mode = (
            ENABLE_EXTENDED_FLAGS |
            ENABLE_VIRTUAL_TERMINAL_INPUT
        )
        kernel32.SetConsoleMode(self.stdin_handle, new_mode)

    def _restore_console(self):
        """Restore original console mode"""
        if self.stdin_handle and self.original_mode:
            kernel32.SetConsoleMode(self.stdin_handle, self.original_mode)

    def _normalize_surrogates(self, text):
        """Normalize UTF-16 surrogate pairs in a string."""
        try:
            return text.encode('utf-16', 'surrogatepass').decode('utf-16')
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text

    def _pty_write_chunked(self, pty, data):
        """Write data to PTY in chunks to avoid winpty buffer overflow."""
        data = self._normalize_surrogates(data)
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + PTY_WRITE_CHUNK_SIZE]
            pty.write(chunk)
            offset += PTY_WRITE_CHUNK_SIZE
            if offset < len(data):
                time.sleep(0.001)

    def _reader_stdin(self, pty):
        """Read input using Windows Console API for better paste handling"""
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

                success = kernel32.ReadConsoleInputW(
                    self.stdin_handle,
                    input_buffer,
                    max_events,
                    ctypes.byref(events_read)
                )

                if not success or events_read.value == 0:
                    time.sleep(0.005)
                    continue

                output_chars = []

                for i in range(events_read.value):
                    record = input_buffer[i]

                    if record.EventType != KEY_EVENT:
                        continue

                    key_event = record.Event.KeyEvent

                    if not key_event.bKeyDown:
                        continue

                    vk = key_event.wVirtualKeyCode
                    char = key_event.uChar
                    ctrl_state = key_event.dwControlKeyState

                    left_ctrl = ctrl_state & 0x0008
                    right_ctrl = ctrl_state & 0x0004
                    shift = ctrl_state & 0x0010

                    if vk == 0x43 and (left_ctrl or right_ctrl):
                        self.running = False
                        return

                    if vk == 0x1B:
                        output_chars.append('\x1b')
                        continue

                    if vk == 0x09 and shift:
                        output_chars.append('\x1b[Z')
                        continue

                    if vk == 0x09:
                        output_chars.append('\t')
                        continue

                    if vk == 0x0D:
                        output_chars.append('\r')
                        continue

                    if vk == 0x08:
                        output_chars.append('\x7f')
                        continue

                    if vk in VK_MAP:
                        output_chars.append(VK_MAP[vk])
                        continue

                    if left_ctrl or right_ctrl:
                        if 0x41 <= vk <= 0x5A:
                            output_chars.append(chr(vk - 0x40))
                            continue

                    if char and ord(char) > 0:
                        output_chars.append(char)

                if output_chars:
                    self._pty_write_chunked(pty, ''.join(output_chars))

            except Exception as e:
                log(f"Stdin reader error: {e}")
                break

    def _reader_pty(self, pty):
        """Read from PTY, write to stdout, tee to session log."""
        while self.running and pty.isalive():
            try:
                data = pty.read(8192)
                if data:
                    if isinstance(data, str):
                        raw_bytes = data.encode('utf-8', errors='ignore')
                        sys.stdout.write(data)
                    else:
                        raw_bytes = data
                        sys.stdout.buffer.write(data)

                    sys.stdout.flush()

                    # Tee raw output to session log
                    if self.session_fh:
                        self.session_fh.write(raw_bytes)
                        self.session_fh.flush()

                    # Use overlap buffer to catch pattern split across reads
                    search_chunk = self.overlap_buffer + raw_bytes
                    if not self.in_approval and FOOTER_PATTERN in search_chunk:
                        self.do_approval(pty)

                    # Keep enough overlap for the pattern
                    self.overlap_buffer = raw_bytes[-64:]
            except Exception as e:
                log(f"PTY reader error: {e}")
                break

    def do_approval(self, pty):
        """Auto-approve by sending Enter (accepts default/focused option).

        v18: send just CR instead of '1\\r' to avoid echo.
        v19: increase pre-write delay from 0.1s to 0.2s — let Gemini's
        Ink UI finish rendering before we inject the keystroke.
        """
        self.in_approval = True
        self.overlap_buffer = b""
        self.approval_count += 1
        time.sleep(0.2)  # v19: was 0.1 — longer delay lets UI settle
        pty.write('\r')
        log("Auto-approved (sent CR)")
        if self.friction_logger:
            self.friction_logger.record_prompt()
        time.sleep(0.1)
        self.in_approval = False

    def _purge_old_logs(self, max_age_days: int = 7):
        """Delete session logs older than max_age_days from logs/ directory."""
        from pathlib import Path
        log_dir = Path("logs")
        if not log_dir.exists():
            return
        cutoff = time.time() - (max_age_days * 86400)
        extensions = {'.log', '.raw', '.jsonl'}
        cleaned = 0
        for f in log_dir.iterdir():
            if f.suffix in extensions and f.is_file():
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        cleaned += 1
                except Exception:
                    pass
        if cleaned:
            log(f"Log cleanup: deleted {cleaned} files older than {max_age_days}d from logs/")
            sys.stderr.write(f"[v{VERSION}] Cleaned {cleaned} old log files\n")
            sys.stderr.flush()

    def run(self):
        self.session_start = time.time()
        session_ts = time.strftime("%Y%m%d-%H%M%S")

        sys.stderr.write(f"[Unleashed-G v{VERSION}] Starting...\n")
        sys.stderr.flush()

        # #23: purge old session logs on startup
        self._purge_old_logs()

        # Read terminal size BEFORE changing console mode
        try:
            term_size = shutil.get_terminal_size((120, 40))
            cols = term_size.columns
            rows = term_size.lines
        except Exception:
            cols, rows = 120, 40

        self._setup_console()

        # Ensure console is restored even on unhandled crash
        atexit.register(self._restore_console)

        # Set up raw session log (tee of PTY output)
        os.makedirs("logs", exist_ok=True)
        session_log_path = os.path.join("logs", f"gemini-session-{session_ts}.raw")
        self.session_fh = open(session_log_path, "wb")
        log(f"Session log: {session_log_path}")

        # Set up friction logger
        if self.friction:
            self.friction_logger = FrictionLogger(session_ts)
            log(f"Friction logger: {self.friction_logger.human_path}")

        # Launch companion tabs
        if self.mirror:
            launch_tab("Gemini Raw", session_log_path)
        if self.friction and self.friction_logger:
            launch_tab("Friction", self.friction_logger.human_path)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["UNLEASHED_VERSION"] = VERSION

        try:
            pty = winpty.PtyProcess.spawn(
                ['cmd', '/c', GEMINI_CMD],
                dimensions=(rows, cols),
                cwd=self.cwd,
                env=env
            )
        except Exception as e:
            sys.stderr.write(f"[v{VERSION}] Spawn FAILED: {e}\n")
            sys.stderr.flush()
            self._restore_console()
            return

        t1 = threading.Thread(target=self._reader_stdin, args=(pty,), daemon=True)
        t2 = threading.Thread(target=self._reader_pty, args=(pty,), daemon=True)
        t1.start()
        t2.start()

        try:
            while self.running and pty.isalive():
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            time.sleep(0.2)

            if self.session_fh:
                self.session_fh.close()
                log(f"Session log closed: {session_log_path}")
            if self.friction_logger:
                self.friction_logger.close()

            pty.terminate()
            self._restore_console()
            sys.stdout.write(TERM_RESET)
            sys.stdout.write('\x1bc')
            sys.stdout.flush()

            # #46: Print session summary to stderr
            elapsed = time.time() - self.session_start if self.session_start else 0
            mins, secs = divmod(int(elapsed), 60)
            hrs, mins = divmod(mins, 60)
            dur = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"
            sys.stderr.write(f"\n\u2500\u2500 unleashed-G v{VERSION} session summary \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n")
            sys.stderr.write(f"  Duration:     {dur}\n")
            sys.stderr.write(f"  Approvals:    {self.approval_count}\n")
            if session_log_path:
                sys.stderr.write(f"  Session log:  {session_log_path}\n")
            if self.friction_logger:
                sys.stderr.write(f"  Friction:     {self.friction_logger.jsonl_path}\n")
            sys.stderr.write(f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n")
            sys.stderr.flush()
            log(f"Session summary: {dur}, {self.approval_count} approvals")
            sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Unleashed-G v{VERSION}")
    parser.add_argument("--cwd", default=None, help="Working directory for Gemini CLI")
    parser.add_argument("--mirror", action="store_true", default=True, help="Open raw session log tab (default: on)")
    parser.add_argument("--no-mirror", action="store_false", dest="mirror", help="Disable session log tab")
    parser.add_argument("--friction", action="store_true", default=True, help="Open friction logger tab (default: on)")
    parser.add_argument("--no-friction", action="store_false", dest="friction", help="Disable friction logger tab")
    args = parser.parse_args()
    UnleashedG(cwd=args.cwd, mirror=args.mirror, friction=args.friction).run()
