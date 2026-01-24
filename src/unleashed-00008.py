#!/usr/bin/env python3
"""
Unleashed - v00008
- Fix: Force exit with sys.exit(0) to prevent hung terminal (from A12 archive)
- Fix: Add full terminal reset (RIS) with \x1bc
- Fix: Silent approval (removed countdown message that may interfere with Claude's TUI)
- Fix: Set UNLEASHED_VERSION environment variable for Claude to detect
- Fix: Non-blocking stdin using kbhit() poll instead of blocking getch()
- Fix: Terminal reset before exit (clear partial escape sequences)
- Fix: Clearer dimension handling (cols, rows) with debug output
- Arrow key fix: Map Windows key codes to ANSI escape sequences
- PERMANENT: Forensic logging to stderr
"""
import os
import sys
import threading
import time
import argparse
import shutil
import msvcrt

VERSION = "00008"

try:
    import winpty
except ImportError:
    sys.stderr.write(f"[v{VERSION}] FATAL: pywinpty missing.\n")
    sys.stderr.flush()
    sys.exit(1)

CLAUDE_CMD = r"C:\Users\mcwiz\AppData\Roaming\npm\claude.cmd"
FOOTER_PATTERN = b'Esc to cancel'

# Windows key codes to ANSI escape sequences
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
        sys.stderr.write(f"[v{VERSION}] Thread-Stdin: ONLINE\n")
        sys.stderr.flush()
        while self.running:
            try:
                # Non-blocking check for keyboard input
                if msvcrt.kbhit():
                    char = msvcrt.getch()

                    if char == b'\x03':  # Ctrl+C
                        sys.stderr.write(f"[v{VERSION}] User Interrupted (Ctrl+C).\n")
                        sys.stderr.flush()
                        self.running = False
                        break

                    if char in (b'\x00', b'\xe0'):
                        next_char = msvcrt.getch()
                        ansi_seq = KEY_MAP.get(next_char)
                        if ansi_seq:
                            pty.write(ansi_seq)
                        # Unknown special keys are ignored
                    else:
                        pty.write(char.decode('utf-8', errors='ignore'))
                else:
                    # No input available, sleep briefly to avoid CPU spin
                    time.sleep(0.01)

            except Exception as e:
                sys.stderr.write(f"[v{VERSION}] Stdin Error: {e}\n")
                sys.stderr.flush()
                break
        sys.stderr.write(f"[v{VERSION}] Thread-Stdin: OFFLINE\n")
        sys.stderr.flush()

    def _reader_pty(self, pty):
        sys.stderr.write(f"[v{VERSION}] Thread-PTY: ONLINE\n")
        sys.stderr.flush()
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
                    time.sleep(0.002)

                    search_chunk = self.overlap_buffer + raw_bytes
                    if not self.in_countdown and FOOTER_PATTERN in search_chunk:
                        self.do_approval(pty)

                    self.overlap_buffer = raw_bytes[-32:]
            except Exception as e:
                sys.stderr.write(f"[v{VERSION}] PTY Error: {e}\n")
                sys.stderr.flush()
                break
        sys.stderr.write(f"[v{VERSION}] Thread-PTY: OFFLINE\n")
        sys.stderr.flush()

    def do_approval(self, pty):
        self.in_countdown = True
        self.overlap_buffer = b""
        # Silent approval - no message to avoid interfering with Claude's TUI
        time.sleep(0.5)
        pty.write('\r')
        self.in_countdown = False

    def run(self):
        sys.stderr.write(f"--- [v{VERSION}] SESSION START ---\n")
        sys.stderr.write(f"[v{VERSION}] Python: {sys.version.split()[0]}\n")
        sys.stderr.write(f"[v{VERSION}] CWD: {self.cwd}\n")
        sys.stderr.flush()

        # Get terminal size - shutil returns (columns, lines)
        try:
            term_size = shutil.get_terminal_size((120, 40))
            cols = term_size.columns
            rows = term_size.lines
        except:
            cols, rows = 120, 40

        sys.stderr.write(f"[v{VERSION}] Terminal size: {cols} cols x {rows} rows\n")
        sys.stderr.flush()

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["UNLEASHED_VERSION"] = VERSION  # So Claude can detect which version

        try:
            # winpty expects dimensions=(rows, cols)
            pty = winpty.PtyProcess.spawn(
                ['cmd', '/c', CLAUDE_CMD],
                dimensions=(rows, cols),
                cwd=self.cwd,
                env=env
            )
            sys.stderr.write(f"[v{VERSION}] Spawn SUCCESS. PID: {pty.pid}\n")
            sys.stderr.write(f"[v{VERSION}] PTY dimensions: ({rows}, {cols}) [rows, cols]\n")
            sys.stderr.flush()
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
            # Give threads time to notice running=False and exit cleanly
            time.sleep(0.2)
            pty.terminate()
            # Reset terminal state
            sys.stdout.write(TERM_RESET)
            sys.stdout.write('\x1bc')  # Full terminal reset (RIS)
            sys.stdout.flush()
            sys.stderr.write(f"--- [v{VERSION}] SESSION CLOSED ---\n")
            sys.stderr.flush()
            # Force exit - daemon threads may be blocking on I/O
            sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=None)
    args = parser.parse_args()
    Unleashed(cwd=args.cwd).run()
