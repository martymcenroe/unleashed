#!/usr/bin/env python3
"""
Unleashed-G - v17
Gemini CLI auto-approval wrapper.

Based on unleashed.py (Claude version) but adapted for Gemini CLI.

- Pattern: "Waiting for user confirmation" (Gemini's permission prompt footer)
- Command: gemini CLI
"""
import os
import sys
import threading
import time
import argparse
import shutil
import ctypes
from ctypes import wintypes

VERSION = "g-17"

# winpty write buffer limit - very small chunks to handle UTF-16 expansion on Windows
PTY_WRITE_CHUNK_SIZE = 64  # Small to account for UTF-16 surrogate pairs

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

class UnleashedG:
    def __init__(self, cwd=None):
        self.cwd = cwd or os.getcwd()
        self.running = True
        self.in_countdown = False
        self.overlap_buffer = b""
        self.stdin_handle = None
        self.original_mode = None

    def _setup_console(self):
        """Set up console for raw input - minimal mode changes"""
        self.stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)

        # Save original mode
        self.original_mode = wintypes.DWORD()
        kernel32.GetConsoleMode(self.stdin_handle, ctypes.byref(self.original_mode))

        # Minimal raw mode: disable line input and echo, but keep virtual terminal input
        # Don't set ENABLE_WINDOW_INPUT as it may interfere with Ink's input handling
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
        """Normalize UTF-16 surrogate pairs in a string.

        Windows ReadConsoleInputW delivers emoji as separate surrogate pair
        characters (U+D800-U+DFFF). pywinpty/pyo3 panics on these. This
        normalizes them back to proper Unicode code points.
        """
        try:
            return text.encode('utf-16', 'surrogatepass').decode('utf-16')
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text  # Fall back to original if normalization fails

    def _pty_write_chunked(self, pty, data):
        """Write data to PTY in chunks to avoid winpty buffer overflow.

        Also normalizes UTF-16 surrogate pairs to prevent pywinpty panic.
        """
        # Normalize surrogate pairs from Windows console input
        data = self._normalize_surrogates(data)

        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + PTY_WRITE_CHUNK_SIZE]
            pty.write(chunk)
            offset += PTY_WRITE_CHUNK_SIZE
            if offset < len(data):
                time.sleep(0.001)  # Small delay between chunks

    def _reader_stdin(self, pty):
        """Read input using Windows Console API for better paste handling"""
        # Buffer for reading multiple events at once
        max_events = 1024  # Increased buffer for large pastes
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

                    # Check for control keys
                    left_ctrl = ctrl_state & 0x0008
                    right_ctrl = ctrl_state & 0x0004
                    shift = ctrl_state & 0x0010
                    alt = ctrl_state & 0x0001 or ctrl_state & 0x0002

                    # Handle Ctrl+C
                    if vk == 0x43 and (left_ctrl or right_ctrl):  # C with CTRL
                        self.running = False
                        return

                    # Handle Escape
                    if vk == 0x1B:  # VK_ESCAPE
                        output_chars.append('\x1b')
                        continue

                    # Handle Shift+Tab
                    if vk == 0x09 and shift:  # TAB with SHIFT
                        output_chars.append('\x1b[Z')
                        continue

                    # Handle Tab
                    if vk == 0x09:  # TAB
                        output_chars.append('\t')
                        continue

                    # Handle Enter
                    if vk == 0x0D:  # VK_RETURN
                        output_chars.append('\r')
                        continue

                    # Handle Backspace
                    if vk == 0x08:  # VK_BACK
                        output_chars.append('\x7f')  # DEL character
                        continue

                    # Handle special keys (arrows, function keys, etc.)
                    if vk in VK_MAP:
                        output_chars.append(VK_MAP[vk])
                        continue

                    # Handle Ctrl+key combinations
                    if left_ctrl or right_ctrl:
                        if 0x41 <= vk <= 0x5A:  # A-Z
                            # Convert to control character (Ctrl+A = 0x01, etc.)
                            output_chars.append(chr(vk - 0x40))
                            continue

                    # Handle regular characters
                    if char and ord(char) > 0:
                        output_chars.append(char)

                # Write all collected characters in chunks to avoid buffer overflow
                if output_chars:
                    self._pty_write_chunked(pty, ''.join(output_chars))

            except Exception as e:
                break

    def _reader_pty(self, pty):
        """Read from PTY and write to stdout"""
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

                    # Use overlap buffer to catch pattern split across reads
                    search_chunk = self.overlap_buffer + raw_bytes
                    if not self.in_countdown and FOOTER_PATTERN in search_chunk:
                        self.do_approval(pty)

                    # Keep enough overlap for the pattern
                    self.overlap_buffer = raw_bytes[-64:]
            except Exception:
                break

    def do_approval(self, pty):
        """Auto-approve by sending '1' + Enter (selects 'Allow once')"""
        self.in_countdown = True
        self.overlap_buffer = b""
        time.sleep(0.5)
        pty.write('1\r')  # Select option 1: Allow once
        self.in_countdown = False

    def run(self):
        # Startup banner - before Gemini starts, so won't interfere
        sys.stderr.write(f"[Unleashed-G v{VERSION}] Starting...\n")
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
    UnleashedG(cwd=args.cwd).run()
