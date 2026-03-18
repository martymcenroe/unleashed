#!/usr/bin/env python3
"""
Unleashed-T - v01
Codex CLI wrapper with PTY handling, live session logs, and companion tabs.

v01 scope:
- Launch Codex in --full-auto mode by default
- Three-thread PTY wrapper (stdin, PTY reader, resize monitor)
- Raw PTY tee to logs/codex-session-{ts}.raw
- ANSI-stripped mirror log filtered through transcript_filters.py
- Friction logger scaffold (records zero prompts in full-auto mode)
- Auto-tab naming: REPONAME YYYY-MM-DD HH:MM
- Companion tabs for clean mirror and friction logs
"""
import argparse
import atexit
import ctypes
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from ctypes import wintypes

from transcript_filters import is_garbage

VERSION = "t-01"
LOG_FILE = os.path.join("logs", f"unleashed-{VERSION}.log")
CODEX_CMD = r"C:\Users\mcwiz\AppData\Roaming\npm\codex.cmd"
BASH_EXE = r"C:\Program Files\Git\usr\bin\bash.exe"
PTY_WRITE_CHUNK_SIZE = 64
MIRROR_FLUSH_INTERVAL = 0.2
SESSION_MIRROR_MAX_LINES = 10_000
SESSION_MIRROR_KEEP_LINES = 7_000

try:
    import winpty
except ImportError:
    sys.stderr.write(f"[Unleashed-T {VERSION}] FATAL: pywinpty missing.\n")
    sys.stderr.flush()
    sys.exit(1)

os.makedirs("logs", exist_ok=True)


def log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


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


VK_MAP = {
    0x26: "\x1b[A",
    0x28: "\x1b[B",
    0x25: "\x1b[D",
    0x27: "\x1b[C",
    0x24: "\x1b[H",
    0x23: "\x1b[F",
    0x2D: "\x1b[2~",
    0x2E: "\x1b[3~",
    0x21: "\x1b[5~",
    0x22: "\x1b[6~",
    0x70: "\x1bOP",
    0x71: "\x1bOQ",
    0x72: "\x1bOR",
    0x73: "\x1bOS",
    0x74: "\x1b[15~",
    0x75: "\x1b[17~",
    0x76: "\x1b[18~",
    0x77: "\x1b[19~",
    0x78: "\x1b[20~",
    0x79: "\x1b[21~",
    0x7A: "\x1b[23~",
    0x7B: "\x1b[24~",
}

TERM_RESET = (
    "\033[0m"
    "\033[?25h"
    "\033[?1000l"
    "\033[?1002l"
    "\033[?1003l"
    "\033[?1006l"
)

ANSI_RE = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]|\x1b[A-Za-z]")
MULTI_SPACE_RE = re.compile(rb" {2,}")
MIRROR_ANSI_RE = re.compile(
    rb"\x1b\[[\x20-\x3f]*[A-Za-z~]"
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    rb"|\x1b[()][AB012]"
    rb"|\x1b[A-Za-z]"
)
ORPHAN_CSI_RE = re.compile(r"\[[\d;?]+[A-Za-z~]")
ORPHAN_OSC_RE = re.compile(r"\]\d;.*")
MIRROR_CONTROL_RE = re.compile(rb"[\x00-\x09\x0b-\x1f\x7f]")
SPINNER_PREFIX_RE = re.compile(r"^[*✶✻✽·✢●]\s*")
SPINNER_FRAG_RE = re.compile(r"^[*✶✻✽·✢●]\s*.{0,12}$")
THINKING_RE = re.compile(r"^thinking\)?$")
BARE_CSI_PARAM_RE = re.compile(r"^\d+[;?\d]*[a-zA-Z]$")
PURE_DIGITS_RE = re.compile(r"^\d{8,}$")


def mirror_strip_ansi(data: bytes) -> bytes:
    result = []
    row = 0
    col = 0
    pos = 0
    length = len(data)

    while pos < length:
        byte = data[pos]

        if byte == 0x1B and pos + 1 < length:
            next_byte = data[pos + 1]

            if next_byte == 0x5B:
                end = pos + 2
                while end < length and 0x20 <= data[end] <= 0x3F:
                    end += 1
                if end < length and 0x40 <= data[end] <= 0x7E:
                    final = data[end]
                    param_bytes = data[pos + 2:end]
                    end += 1

                    if final in (0x48, 0x66):
                        params = param_bytes.split(b";")
                        new_row = int(params[0]) if params[0] else 1
                        new_col = int(params[1]) if len(params) > 1 and params[1] else 1
                        if new_row != row:
                            if result and result[-1] != b"\n":
                                result.append(b"\n")
                            row = new_row
                        elif new_col > col:
                            result.append(b" ")
                        col = new_col
                    elif final == 0x47:
                        new_col = int(param_bytes) if param_bytes else 1
                        if new_col > col:
                            result.append(b" ")
                        col = new_col
                    elif final == 0x43:
                        n = int(param_bytes) if param_bytes else 1
                        if n >= 1:
                            result.append(b" ")
                        col += n
                    elif final in (0x41, 0x42):
                        n = int(param_bytes) if param_bytes else 1
                        row = row - n if final == 0x41 else row + n
                        if result and result[-1] != b"\n":
                            result.append(b"\n")

                    pos = end
                    continue

                pos = end
                continue

            if next_byte == 0x5D:
                end = pos + 2
                while end < length:
                    if data[end] == 0x07:
                        end += 1
                        break
                    if data[end] == 0x1B and end + 1 < length and data[end + 1] == 0x5C:
                        end += 2
                        break
                    end += 1
                pos = end
                continue

            if next_byte in (0x28, 0x29):
                pos = min(pos + 3, length)
                continue

            pos += 2
            continue

        if byte == 0x0A:
            if result and result[-1] != b"\n":
                result.append(b"\n")
            row += 1
            col = 0
            pos += 1
            continue

        if byte < 0x20 or byte == 0x7F:
            pos += 1
            continue

        result.append(data[pos:pos + 1])
        col += 1
        pos += 1

    return b"".join(result)


def launch_tab(title: str, log_path: str):
    try:
        abs_path = os.path.abspath(log_path)
        unix_path = abs_path.replace("\\", "/")
        if len(unix_path) > 1 and unix_path[1] == ":":
            unix_path = "/" + unix_path[0].lower() + unix_path[2:]
        cmd = f'wt.exe -w 0 nt --title "{title}" --suppressApplicationTitle "{BASH_EXE}" -c "tail -f \'{unix_path}\'"'
        log(f"Launching tab: {cmd}")
        subprocess.Popen(cmd, shell=True)
    except Exception as e:
        log(f"WARNING: Failed to launch tab '{title}': {e}")


class SessionLogger:
    def __init__(self, session_ts: str):
        self.path = os.path.join("logs", f"codex-session-{session_ts}.log")
        self.fh = open(self.path, "a", encoding="utf-8")
        self.line_count = 0
        self.fh.write(f"{'=' * 60}\n")
        self.fh.write(f"  Unleashed-T v{VERSION} Session Mirror\n")
        self.fh.write(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.fh.write("  Mode: codex -a never -s workspace-write\n")
        self.fh.write(f"{'=' * 60}\n\n")
        self.fh.flush()
        self.line_count = 6

    def _truncate_if_needed(self):
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

    def write_lines(self, lines):
        wrote = False
        for line in lines:
            self.fh.write(f"{line}\n")
            self.line_count += 1
            wrote = True
        if wrote:
            self.fh.flush()
            self._truncate_if_needed()

    def close(self):
        if self.fh and not self.fh.closed:
            self.fh.write(f"\n{'=' * 60}\n")
            self.fh.write(f"  Session ended: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            self.fh.write(f"{'=' * 60}\n")
            self.fh.flush()
            self.fh.close()


class FrictionLogger:
    def __init__(self, session_ts: str):
        self.human_path = os.path.join("logs", f"codex-friction-{session_ts}.log")
        self.jsonl_path = os.path.join("logs", f"codex-friction-{session_ts}.jsonl")
        self.session_start = time.time()
        self.prompt_count = 0
        self.fh_human = open(self.human_path, "a", encoding="utf-8")
        self.fh_jsonl = open(self.jsonl_path, "a", encoding="utf-8")
        self.fh_human.write(f"{'=' * 60}\n")
        self.fh_human.write("  Codex Friction Logger\n")
        self.fh_human.write(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.fh_human.write("  Mode: -a never -s workspace-write (approval detection deferred to v02)\n")
        self.fh_human.write(f"{'=' * 60}\n\n")
        self.fh_human.write("No approval prompts are expected in v01.\n\n")
        self.fh_human.flush()

        self.fh_jsonl.write(json.dumps({
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "type": "session_config",
            "mode": "never/workspace-write",
            "approval_detection": "deferred",
        }) + "\n")
        self.fh_jsonl.flush()

    def close(self):
        elapsed = time.time() - self.session_start
        elapsed_min = elapsed / 60
        summary = (
            f"\n{'=' * 60}\n"
            f"  SESSION SUMMARY\n"
            f"  Duration: {elapsed_min:.1f} minutes\n"
            f"  Total permission prompts: {self.prompt_count}\n"
            f"  JSONL data: {self.jsonl_path}\n"
            f"{'=' * 60}\n"
        )
        self.fh_human.write(summary)
        self.fh_human.flush()
        for fh in (self.fh_human, self.fh_jsonl):
            if fh and not fh.closed:
                fh.close()


class UnleashedT:
    def __init__(self, cwd=None, mirror=True, friction=True, codex_args=None):
        self.cwd = cwd or os.getcwd()
        self.running = True
        self.stdin_handle = None
        self.original_mode = None
        self.mirror = mirror
        self.friction = friction
        self.codex_args = codex_args or []
        self.session_fh = None
        self.session_logger = None
        self.friction_logger = None
        self.session_start = None
        self._mirror_buffer = b""
        self._mirror_last_flush = 0.0
        self._mirror_recent = deque(maxlen=32)
        self._pending_prompt = None

    def _purge_old_logs(self, max_age_days: int = 7):
        from pathlib import Path

        log_dir = Path("logs")
        if not log_dir.exists():
            return
        cutoff = time.time() - (max_age_days * 86400)
        extensions = {".log", ".raw", ".jsonl"}
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
            sys.stderr.write(f"[Unleashed-T {VERSION}] Cleaned {cleaned} old log files\n")
            sys.stderr.flush()

    def _setup_console(self):
        self.stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        self.original_mode = wintypes.DWORD()
        kernel32.GetConsoleMode(self.stdin_handle, ctypes.byref(self.original_mode))
        new_mode = ENABLE_EXTENDED_FLAGS | ENABLE_VIRTUAL_TERMINAL_INPUT
        kernel32.SetConsoleMode(self.stdin_handle, new_mode)

    def _restore_console(self):
        if self.stdin_handle and self.original_mode:
            kernel32.SetConsoleMode(self.stdin_handle, self.original_mode)

    def _set_tab_title(self):
        repo_name = os.path.basename(self.cwd).upper()
        timestamp = time.strftime("%Y-%m-%d %H:%M")
        title = f"{repo_name} {timestamp}"
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()
        log(f"Tab title set: {title}")

    def _normalize_surrogates(self, text):
        try:
            return text.encode("utf-16", "surrogatepass").decode("utf-16")
        except (UnicodeEncodeError, UnicodeDecodeError):
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

                success = kernel32.ReadConsoleInputW(
                    self.stdin_handle,
                    input_buffer,
                    max_events,
                    ctypes.byref(events_read),
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
                        output_chars.append("\x1b")
                        continue
                    if vk == 0x09 and shift:
                        output_chars.append("\x1b[Z")
                        continue
                    if vk == 0x09:
                        output_chars.append("\t")
                        continue
                    if vk == 0x0D:
                        output_chars.append("\r")
                        continue
                    if vk == 0x08:
                        output_chars.append("\x7f")
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
                    self._pty_write_chunked(pty, "".join(output_chars))

            except Exception as e:
                log(f"Stdin reader error: {e}")
                break

    def _resize_monitor(self, pty):
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

    @staticmethod
    def _is_sparse_fragment(line):
        if len(line) > 12:
            return False
        words = line.split()
        if len(words) < 2:
            return False
        avg_word_len = sum(len(w) for w in words) / len(words)
        return avg_word_len < 2.5

    def _flush_pending_prompt(self):
        if self._pending_prompt is None or not self.session_logger:
            return
        normalized = SPINNER_PREFIX_RE.sub("", self._pending_prompt).strip()
        if normalized not in self._mirror_recent:
            self._mirror_recent.append(normalized)
            self.session_logger.write_lines([self._pending_prompt])
        self._pending_prompt = None

    def _log_to_mirror(self, raw_data):
        if not self.session_logger:
            return

        raw_bytes = raw_data.encode("utf-8", errors="ignore") if isinstance(raw_data, str) else raw_data
        clean = mirror_strip_ansi(raw_bytes)
        clean = MIRROR_ANSI_RE.sub(b" ", clean)
        clean = MIRROR_CONTROL_RE.sub(b"", clean)
        text = clean.decode("utf-8", errors="replace")
        out_lines = []

        for line in text.split("\n"):
            stripped = ORPHAN_CSI_RE.sub("", line.strip())
            stripped = ORPHAN_OSC_RE.sub("", stripped).strip()
            if not stripped:
                continue

            if stripped.startswith("\u276f"):
                if self._pending_prompt is not None:
                    prev = self._pending_prompt
                    if stripped.startswith(prev) or prev.startswith(stripped):
                        self._pending_prompt = stripped if len(stripped) >= len(prev) else prev
                        continue
                    self._flush_pending_prompt()
                self._pending_prompt = stripped
                continue

            if len(stripped) < 3:
                continue
            if SPINNER_FRAG_RE.match(stripped):
                continue
            if self._is_sparse_fragment(stripped):
                continue
            if THINKING_RE.match(stripped):
                continue
            if BARE_CSI_PARAM_RE.match(stripped):
                continue
            if PURE_DIGITS_RE.match(stripped):
                continue
            if len(stripped) <= 8 and stripped.endswith("\u2026"):
                continue
            if is_garbage(stripped):
                continue

            normalized = SPINNER_PREFIX_RE.sub("", stripped).strip()
            if normalized in self._mirror_recent:
                continue

            if self._pending_prompt is not None:
                self._flush_pending_prompt()

            self._mirror_recent.append(normalized)
            out_lines.append(stripped)

        if out_lines:
            self.session_logger.write_lines(out_lines)

    def _flush_mirror(self, final=False):
        if self._mirror_buffer:
            self._log_to_mirror(self._mirror_buffer)
            self._mirror_buffer = b""
        if final and self._pending_prompt is not None:
            self._flush_pending_prompt()

    def _reader_pty(self, pty):
        while self.running and pty.isalive():
            try:
                data = pty.read(8192)
                if not data:
                    continue

                if isinstance(data, str):
                    raw_bytes = data.encode("utf-8", errors="ignore")
                    sys.stdout.write(data)
                else:
                    raw_bytes = data
                    sys.stdout.buffer.write(data)
                sys.stdout.flush()

                if self.session_fh:
                    self.session_fh.write(raw_bytes)
                    self.session_fh.flush()

                if self.session_logger:
                    self._mirror_buffer += raw_bytes
                    now = time.time()
                    if now - self._mirror_last_flush >= MIRROR_FLUSH_INTERVAL:
                        self._flush_mirror()
                        self._mirror_last_flush = now

            except Exception as e:
                log(f"PTY reader error: {e}")
                break

    def run(self):
        self.session_start = time.time()
        session_ts = time.strftime("%Y%m%d-%H%M%S")

        sys.stderr.write(f"[Unleashed-T {VERSION}] Starting...\n")
        sys.stderr.flush()

        self._purge_old_logs()

        try:
            term_size = shutil.get_terminal_size((120, 40))
            cols, rows = term_size.columns, term_size.lines
        except Exception:
            cols, rows = 120, 40

        self._setup_console()
        atexit.register(self._restore_console)
        self._set_tab_title()

        session_log_path = os.path.join("logs", f"codex-session-{session_ts}.raw")
        self.session_fh = open(session_log_path, "wb")
        log(f"Raw session log: {session_log_path}")
        if self.mirror:
            launch_tab("Codex Raw", session_log_path)

        self.session_logger = SessionLogger(session_ts)
        log(f"Session mirror: {self.session_logger.path}")

        if self.friction:
            self.friction_logger = FrictionLogger(session_ts)
            log(f"Friction logger: {self.friction_logger.human_path}")
            launch_tab("Codex Friction", self.friction_logger.human_path)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["UNLEASHED_VERSION"] = VERSION
        env["NO_PROXY"] = "*"  # Bypass Codex sandbox proxy (127.0.0.1:9) for gh/git

        codex_cmd = ["cmd", "/c", CODEX_CMD,
                     "-a", "never", "-s", "workspace-write",
                     "-c", "shell_environment_policy.inherit=all",
                     "--search"] + self.codex_args
        log(f"Spawning: {codex_cmd}")

        try:
            pty = winpty.PtyProcess.spawn(
                codex_cmd,
                dimensions=(rows, cols),
                cwd=self.cwd,
                env=env,
            )
        except Exception as e:
            sys.stderr.write(f"[Unleashed-T {VERSION}] Spawn FAILED: {e}\n")
            sys.stderr.flush()
            log(f"Spawn FAILED: {e}")
            self._restore_console()
            if self.session_fh:
                self.session_fh.close()
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
            self._flush_mirror(final=True)

            if self.session_fh:
                self.session_fh.close()
            if self.session_logger:
                self.session_logger.close()
            if self.friction_logger:
                self.friction_logger.close()

            if pty.isalive():
                pty.terminate()
            self._restore_console()
            sys.stdout.write(TERM_RESET)
            sys.stdout.write("\x1bc")
            sys.stdout.flush()

            elapsed = time.time() - self.session_start if self.session_start else 0
            mins, secs = divmod(int(elapsed), 60)
            hrs, mins = divmod(mins, 60)
            dur = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"
            sys.stderr.write(f"\n-- unleashed-T {VERSION} session summary -------------------\n")
            sys.stderr.write(f"  Duration:     {dur}\n")
            sys.stderr.write("  Approvals:    never\n")
            sys.stderr.write("  Sandbox:      workspace-write (requested)\n")
            sys.stderr.write(f"  Session log:  {session_log_path}\n")
            if self.session_logger:
                sys.stderr.write(f"  Mirror:       {self.session_logger.path}\n")
            if self.friction_logger:
                sys.stderr.write(f"  Friction:     {self.friction_logger.jsonl_path}\n")
            sys.stderr.write("----------------------------------------------------------\n")
            sys.stderr.flush()
            log(f"Session summary: {dur}, approval=never sandbox=workspace-write")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Unleashed-T v{VERSION}")
    parser.add_argument("--cwd", default=None, help="Working directory for Codex CLI")
    parser.add_argument("--mirror", action="store_true", default=True, help="Open raw session log tab (default: on)")
    parser.add_argument("--no-mirror", action="store_false", dest="mirror", help="Disable raw session log tab")
    parser.add_argument("--friction", action="store_true", default=True, help="Open friction logger tab (default: on)")
    parser.add_argument("--no-friction", action="store_false", dest="friction", help="Disable friction logger tab")
    args, codex_args = parser.parse_known_args()
    UnleashedT(cwd=args.cwd, mirror=args.mirror, friction=args.friction, codex_args=codex_args).run()
