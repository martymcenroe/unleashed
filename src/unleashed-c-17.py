#!/usr/bin/env python3
"""
Unleashed - v00017
- Fix: Robust pattern matching for Claude Code permission prompts
- Fix: Multiple pattern candidates (Tab to amend, Do you want to proceed?)
- Fix: Resilient to ANSI codes breaking the footer string
- Added: Debug logging to logs/unleashed_v17.log
- Fix: Strip ANSI escape sequences before pattern matching (invisible codes break matches)
- Fix: Forward terminal resize to PTY (mismatched dimensions cause TUI re-render artifacts)
"""
import os
import sys
import threading
import time
import argparse
import shutil
import ctypes
import re
from ctypes import wintypes

VERSION = "00017"
LOG_FILE = os.path.join("logs", f"unleashed_v{VERSION}.log")

# winpty write buffer limit
PTY_WRITE_CHUNK_SIZE = 64

try:
    import winpty
except ImportError:
    sys.stderr.write(f"[v{VERSION}] FATAL: pywinpty missing.\\n")
    sys.stderr.flush()
    sys.exit(1)

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

def log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\\n")

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
# Matches: CSI sequences (\x1b[...X), OSC sequences (\x1b]...BEL), and simple escapes (\x1bX)
ANSI_RE = re.compile(rb'\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]|\x1b[A-Za-z]')

def strip_ansi(data: bytes) -> bytes:
    """Strip ANSI escape sequences so pattern matching sees clean text."""
    return ANSI_RE.sub(b'', data)

# Patterns to look for. We'll check for these individually to be robust.
# "Tab to amend" is a very strong signal for the permission prompt.
# "Do you want to proceed?" is the current prompt header.
PATTERNS = [
    b'Tab to amend',
    b'Do you want to proceed?',
    b'Allow this command to run?'
]

VK_MAP = {
    0x26: '\\x1b[A', 0x28: '\\x1b[B', 0x25: '\\x1b[D', 0x27: '\\x1b[C',
    0x24: '\\x1b[H', 0x23: '\\x1b[F', 0x2D: '\\x1b[2~', 0x2E: '\\x1b[3~',
    0x21: '\\x1b[5~', 0x22: '\\x1b[6~', 0x70: '\\x1bOP', 0x71: '\\x1bOQ',
    0x72: '\\x1bOR', 0x73: '\\x1bOS', 0x74: '\\x1b[15~', 0x75: '\\x1b[17~',
    0x76: '\\x1b[18~', 0x77: '\\x1b[19~', 0x78: '\\x1b[20~', 0x79: '\\x1b[21~',
    0x7A: '\\x1b[23~', 0x7B: '\\x1b[24~',
}

TERM_RESET = '\\033[0m\\033[?25h\\033[?1000l\\033[?1002l\\033[?1003l\\033[?1006l'

class Unleashed:
    def __init__(self, cwd=None):
        self.cwd = cwd or os.getcwd()
        self.running = True
        self.in_approval = False
        self.overlap_buffer = b""
        self.stdin_handle = None
        self.original_mode = None
        log(f"Initialized v{VERSION} in {self.cwd}")

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
                    if vk == 0x43 and (left_ctrl or right_ctrl): # Ctrl+C
                        self.running = False
                        return
                    if vk == 0x1B: output_chars.append('\\x1b'); continue
                    if vk == 0x09 and shift: output_chars.append('\\x1b[Z'); continue
                    if vk == 0x09: output_chars.append('\\t'); continue
                    if vk == 0x0D: output_chars.append('\\r'); continue
                    if vk == 0x08: output_chars.append('\\x7f'); continue
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
        """Poll for terminal resize and forward new dimensions to PTY.

        Without this, resizing the terminal causes Claude Code's Ink TUI to
        re-render for the wrong dimensions, injecting ANSI cursor-positioning
        codes that break permission prompt pattern matching.
        """
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

                search_chunk = self.overlap_buffer + raw_bytes
                # Strip ANSI escape sequences before pattern matching.
                # The terminal renders these invisibly but they break
                # contiguous byte matching (especially after resize).
                clean_chunk = strip_ansi(search_chunk)

                # Robust matching logic
                if not self.in_approval:
                    # Check for patterns against ANSI-stripped text
                    found_pattern = None
                    for p in PATTERNS:
                        if p in clean_chunk:
                            found_pattern = p
                            break

                    if found_pattern:
                        log(f"Pattern MATCHED: {found_pattern!r}")
                        self.do_approval(pty)
                    elif b'Esc to cancel' in clean_chunk:
                        # Log if we see part of the footer but not the expected triggers
                        log(f"DEBUG: Found 'Esc to cancel' but no match in clean chunk: {clean_chunk[-256:]!r}")

                self.overlap_buffer = raw_bytes[-256:] # Larger overlap for resize re-renders
            except Exception as e:
                log(f"PTY Reader Error: {e}")
                break

    def do_approval(self, pty):
        log("Executing auto-approval...")
        self.in_approval = True
        self.overlap_buffer = b""
        time.sleep(0.5)
        pty.write('\\r')
        log("Sent CR to PTY")
        time.sleep(0.5) # Wait for UI to react
        self.in_approval = False

    def run(self):
        sys.stderr.write(f"[Unleashed v{VERSION}] Starting...\\n")
        sys.stderr.flush()
        self._setup_console()
        try:
            term_size = shutil.get_terminal_size((120, 40))
            cols, rows = term_size.columns, term_size.lines
        except:
            cols, rows = 120, 40

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["UNLEASHED_VERSION"] = VERSION

        try:
            pty = winpty.PtyProcess.spawn(['cmd', '/c', CLAUDE_CMD], dimensions=(rows, cols), cwd=self.cwd, env=env)
        except Exception as e:
            sys.stderr.write(f"[v{VERSION}] Spawn FAILED: {e}\\n")
            log(f"Spawn FAILED: {e}")
            self._restore_console()
            return

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
            pty.terminate()
            self._restore_console()
            sys.stdout.write(TERM_RESET)
            sys.stdout.write('\\x1bc')
            sys.stdout.flush()
            log("Shutting down")
            sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=None)
    args = parser.parse_args()
    Unleashed(cwd=args.cwd).run()