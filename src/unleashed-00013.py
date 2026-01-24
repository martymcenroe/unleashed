#!/usr/bin/env python3
"""
Unleashed - v00013
- Fix: Use ReadConsoleInput API for larger paste buffer (bypass msvcrt limits)
- Fix: Batch-read stdin to handle large pastes (fixes freeze on paste)
- Fix: Remove runtime stderr output that may interfere with Claude's status bar
- Fix: Add Shift+Tab support for mode cycling (plan mode, accept edits)
- Fix: Force exit with sys.exit(0) to prevent hung terminal (from A12 archive)
- Fix: Add full terminal reset (RIS) with \x1bc
- Fix: Silent approval (removed countdown message that may interfere with Claude's TUI)
- Fix: Set UNLEASHED_VERSION environment variable for Claude to detect
- Fix: Terminal reset before exit (clear partial escape sequences)
- Arrow key fix: Map Windows key codes to ANSI escape sequences
"""
import os
import sys
import threading
import time
import argparse
import shutil
import ctypes
from ctypes import wintypes

VERSION = "00013"

try:
    import winpty
except ImportError:
    sys.stderr.write(f"[v{VERSION}] FATAL: pywinpty missing.\n")
    sys.stderr.flush()
    sys.exit(1)

# Windows API setup
kernel32 = ctypes.windll.kernel32

# Constants
STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001
ENABLE_WINDOW_INPUT = 0x0008
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
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

CLAUDE_CMD = r"C:\Users\mcwiz\AppData\Roaming\npm\claude.cmd"
FOOTER_PATTERN = b'Esc to cancel'

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

class Unleashed:
    def __init__(self, cwd=None):
        self.cwd = cwd or os.getcwd()
        self.running = True
        self.in_countdown = False
        self.overlap_buffer = b""
        self.stdin_handle = None
        self.original_mode = None

    def _setup_console(self):
        """Set up console for raw input"""
        self.stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)

        # Save original mode
        self.original_mode = wintypes.DWORD()
        kernel32.GetConsoleMode(self.stdin_handle, ctypes.byref(self.original_mode))

        # Set raw mode - disable line input, echo, and processed input
        new_mode = ENABLE_WINDOW_INPUT | ENABLE_VIRTUAL_TERMINAL_INPUT
        kernel32.SetConsoleMode(self.stdin_handle, new_mode)

    def _restore_console(self):
        """Restore original console mode"""
        if self.stdin_handle and self.original_mode:
            kernel32.SetConsoleMode(self.stdin_handle, self.original_mode)

    def _reader_stdin(self, pty):
        """Read input using Windows Console API for better paste handling"""
        # Buffer for reading multiple events at once
        max_events = 512  # Read up to 512 input events at a time
        input_buffer = (INPUT_RECORD * max_events)()
        events_read = wintypes.DWORD()

        while self.running:
            try:
                # Check if input is available (non-blocking peek)
                events_available = wintypes.DWORD()
                kernel32.GetNumberOfConsoleInputEvents(self.stdin_handle, ctypes.byref(events_available))

                if events_available.value == 0:
                    time.sleep(0.005)
                    continue

                # Read all available input events
                success = kernel32.ReadConsoleInputW(
                    self.stdin_handle,
                    input_buffer,
                    max_events,
                    ctypes.byref(events_read)
                )

                if not success or events_read.value == 0:
                    time.sleep(0.005)
                    continue

                # Process events and build output string
                output_chars = []

                for i in range(events_read.value):
                    record = input_buffer[i]

                    if record.EventType != KEY_EVENT:
                        continue

                    key_event = record.Event.KeyEvent

                    # Only process key down events
                    if not key_event.bKeyDown:
                        continue

                    vk = key_event.wVirtualKeyCode
                    char = key_event.uChar
                    ctrl_state = key_event.dwControlKeyState

                    # Handle Ctrl+C
                    if vk == 0x43 and (ctrl_state & 0x0008 or ctrl_state & 0x0004):  # C with LEFT_CTRL or RIGHT_CTRL
                        self.running = False
                        return

                    # Handle Shift+Tab
                    if vk == 0x09 and (ctrl_state & 0x0010):  # TAB with SHIFT
                        output_chars.append('\x1b[Z')
                        continue

                    # Handle special keys (arrows, etc.)
                    if vk in VK_MAP:
                        output_chars.append(VK_MAP[vk])
                        continue

                    # Handle regular characters
                    if char and ord(char) > 0:
                        output_chars.append(char)

                # Write all collected characters at once
                if output_chars:
                    pty.write(''.join(output_chars))

            except Exception as e:
                break

    def _reader_pty(self, pty):
        # No runtime debug output - interferes with Claude's TUI
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

                    search_chunk = self.overlap_buffer + raw_bytes
                    if not self.in_countdown and FOOTER_PATTERN in search_chunk:
                        self.do_approval(pty)

                    self.overlap_buffer = raw_bytes[-32:]
            except Exception:
                break

    def do_approval(self, pty):
        self.in_countdown = True
        self.overlap_buffer = b""
        time.sleep(0.5)
        pty.write('\r')
        self.in_countdown = False

    def run(self):
        # Startup banner - before Claude starts, so won't interfere
        sys.stderr.write(f"[Unleashed v{VERSION}] Starting...\n")
        sys.stderr.flush()

        self._setup_console()

        try:
            term_size = shutil.get_terminal_size((120, 40))
            cols = term_size.columns
            rows = term_size.lines
        except:
            cols, rows = 120, 40

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["UNLEASHED_VERSION"] = VERSION

        try:
            pty = winpty.PtyProcess.spawn(
                ['cmd', '/c', CLAUDE_CMD],
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
            pty.terminate()
            self._restore_console()
            sys.stdout.write(TERM_RESET)
            sys.stdout.write('\x1bc')
            sys.stdout.flush()
            sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=None)
    args = parser.parse_args()
    Unleashed(cwd=args.cwd).run()
