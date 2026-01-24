#!/usr/bin/env python3
"""
Unleashed - v00012
- Fix: Tighter paste loop - re-check for more input after writing (paste arrives in chunks)
- Fix: Batch-read stdin to handle large pastes (fixes freeze on paste)
- Fix: Remove runtime stderr output that may interfere with Claude's status bar
- Fix: Add Shift+Tab support for mode cycling (plan mode, accept edits)
- Fix: Force exit with sys.exit(0) to prevent hung terminal (from A12 archive)
- Fix: Add full terminal reset (RIS) with \x1bc
- Fix: Silent approval (removed countdown message that may interfere with Claude's TUI)
- Fix: Set UNLEASHED_VERSION environment variable for Claude to detect
- Fix: Non-blocking stdin using kbhit() poll instead of blocking getch()
- Fix: Terminal reset before exit (clear partial escape sequences)
- Fix: Clearer dimension handling (cols, rows) with debug output
- Arrow key fix: Map Windows key codes to ANSI escape sequences
"""
import os
import sys
import threading
import time
import argparse
import shutil
import msvcrt

VERSION = "00012"

try:
    import winpty
except ImportError:
    sys.stderr.write(f"[v{VERSION}] FATAL: pywinpty missing.\n")
    sys.stderr.flush()
    sys.exit(1)

CLAUDE_CMD = r"C:\Users\mcwiz\AppData\Roaming\npm\claude.cmd"
FOOTER_PATTERN = b'Esc to cancel'

# Windows key codes to ANSI escape sequences
# These follow the \x00 or \xe0 prefix byte
KEY_MAP = {
    b'H': '\x1b[A',   # Up
    b'P': '\x1b[B',   # Down
    b'K': '\x1b[D',   # Left
    b'M': '\x1b[C',   # Right
    b'G': '\x1b[H',   # Home
    b'O': '\x1b[F',   # End
    b'R': '\x1b[2~',  # Insert
    b'S': '\x1b[3~',  # Delete
    b'I': '\x1b[5~',  # Page Up
    b'Q': '\x1b[6~',  # Page Down
    b'\x0f': '\x1b[Z',  # Shift+Tab (scan code 15)
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

    def _reader_stdin(self, pty):
        # Batch-read with tight loop to catch chunked paste data
        while self.running:
            try:
                got_input = False

                while True:
                    # Drain all currently available characters
                    input_buffer = []
                    while msvcrt.kbhit():
                        char = msvcrt.getch()

                        if char == b'\x03':  # Ctrl+C
                            self.running = False
                            return

                        if char in (b'\x00', b'\xe0'):
                            # Special key - need to read the scan code
                            if msvcrt.kbhit():
                                next_char = msvcrt.getch()
                                ansi_seq = KEY_MAP.get(next_char)
                                if ansi_seq:
                                    input_buffer.append(ansi_seq)
                        else:
                            input_buffer.append(char.decode('utf-8', errors='ignore'))

                    if input_buffer:
                        # Write batch to PTY
                        pty.write(''.join(input_buffer))
                        got_input = True
                        # Brief pause to let more paste data arrive
                        time.sleep(0.002)
                    else:
                        # No more input available
                        break

                if not got_input:
                    # Only sleep when no input was processed
                    time.sleep(0.01)

            except Exception:
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
            sys.stdout.write(TERM_RESET)
            sys.stdout.write('\x1bc')
            sys.stdout.flush()
            sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=None)
    args = parser.parse_args()
    Unleashed(cwd=args.cwd).run()
