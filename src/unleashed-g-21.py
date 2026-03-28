#!/usr/bin/env python3
"""
Unleashed-G - v21
Gemini CLI auto-approval wrapper.

Based on unleashed-g-20.py.

v21 changes: g-20 + event-driven loop, config loading, session index, auto-onboard, Python pickup

Prior (v20):
- Console tab: --console (default on), bash shell cd'd to target repo
- Per-repo session logs: save to {target_repo}/data/unleashed/ with project name
- Tab focus-back: return focus to session tab after launching companions
- Auto-tab-naming: REPONAME YYYY-MM-DD HH:MM

Prior (v19):
- Fix residual "1" leak: increase pre-approval delay from 0.1s to 0.2s
- Raw PTY tee to session log
- Companion tabs for mirror and friction
"""
import os
import sys
import re
import atexit
import subprocess
import threading
import time
import datetime
import json
import argparse
import shutil
import ctypes
from collections import deque
from ctypes import wintypes
from pathlib import Path

VERSION = "g-21"
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

# v21: WaitForSingleObject constants (event-driven stdin)
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258

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

# Gemini permission prompt patterns
# Gemini shows different prompts depending on the tool:
#   - Shell commands: "⠦ Waiting for user confirmation ..." (spinner varies)
#   - File writes:    "Apply this change?" with numbered options
#   - Tool approval:  "Action Required" → "Allow execution of: 'tool'?"
PERMISSION_PATTERNS = [
    b'Waiting for user confirmation',
    b'Apply this change?',
    b'Allow execution of:',
]

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

def launch_tab(title: str, log_path: str, session_id: str = ""):
    """Launch a Windows Terminal tab tailing the given log file."""
    try:
        abs_path = os.path.abspath(log_path)
        unix_path = abs_path.replace("\\", "/")
        if unix_path[1] == ':':
            unix_path = '/' + unix_path[0].lower() + unix_path[2:]
        # Embed session marker so cleanup can find this process (#84)
        sid_export = f"export UNLEASHED_SID={session_id} && " if session_id else ""
        cmd = f'wt.exe -w 0 nt --title "{title}" --suppressApplicationTitle "{BASH_EXE}" -c "{sid_export}tail -f \'{unix_path}\'"'
        log(f"Launching tab: {cmd}")
        subprocess.Popen(cmd, shell=True)
    except Exception as e:
        log(f"WARNING: Failed to launch tab '{title}': {e}")

def launch_console_tab(project: str, cwd: str, session_id: str = ""):
    """Launch a Windows Terminal tab with a bash shell cd'd to the target repo."""
    try:
        unix_cwd = cwd.replace("\\", "/")
        if unix_cwd[1] == ':':
            unix_cwd = '/' + unix_cwd[0].lower() + unix_cwd[2:]
        # Embed session marker so cleanup can find this process (#84)
        sid_export = f"export UNLEASHED_SID={session_id} && " if session_id else ""
        cmd = f'wt.exe -w 0 nt --title "Console: {project}" --suppressApplicationTitle "{BASH_EXE}" --login -c "{sid_export}cd \'{unix_cwd}\' && exec bash --login"'
        log(f"Launching console tab: {cmd}")
        subprocess.Popen(cmd, shell=True)
    except Exception as e:
        log(f"WARNING: Failed to launch console tab: {e}")

def focus_tab(index: int = 0):
    """Focus a Windows Terminal tab by index after launching companion tabs."""
    try:
        cmd = f'wt.exe -w 0 ft --target {index}'
        log(f"Focusing tab: {cmd}")
        subprocess.Popen(cmd, shell=True)
    except Exception as e:
        log(f"WARNING: Failed to focus tab {index}: {e}")


# ---------------------------------------------------------------------------
# FrictionLogger — Tab 3: permission prompt tracking
# ---------------------------------------------------------------------------

class FrictionLogger:
    def __init__(self, session_ts: str, log_dir: str = "logs", project: str = "gemini"):
        self.human_path = os.path.join(log_dir, f"gemini-friction-{project}-{session_ts}.log")
        self.jsonl_path = os.path.join(log_dir, f"gemini-friction-{project}-{session_ts}.jsonl")
        self.session_start = time.time()
        self.prompt_count = 0
        self.fh_human = open(self.human_path, "a", encoding="utf-8")
        self.fh_jsonl = open(self.jsonl_path, "a", encoding="utf-8")
        self.fh_human.write(f"{'='*60}\n")
        self.fh_human.write("  Gemini Permission Friction Logger\n")
        self.fh_human.write(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.fh_human.write(f"{'='*60}\n\n")
        self.fh_human.write("Waiting for permission prompts...\n\n")
        self.fh_human.flush()

    def record_prompt(self, pattern="unknown"):
        self.prompt_count += 1
        now = time.time()
        elapsed = int(now - self.session_start)

        # JSONL record
        record = {
            "ts": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "elapsed_s": elapsed,
            "type": "permission_prompt",
            "pattern": pattern,
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
    def __init__(self, cwd=None, mirror=True, friction=True, console=True, home_tab=0,
                 pickup=False, auto_onboard=True, sessions=False):
        self.cwd = cwd or os.getcwd()
        self.running = True
        self.in_approval = False
        self.overlap_buffer = b""
        self.stdin_handle = None
        self.original_mode = None
        self.mirror = mirror
        self.friction = friction
        self.console = console
        self.home_tab = home_tab
        self.session_fh = None  # raw tee file handle
        self.friction_logger = None
        self.approval_count = 0          # #46: total auto-approvals this session
        self.session_start = None        # #46: set in run()
        self._session_ts = None          # #84: for companion cleanup
        self._done_event = threading.Event()  # v21: event-driven main loop
        self._config_echo = None  # v21: deferred config echo
        self._init_cmd_sent = False  # v21: auto-onboard injection
        self._recent_lines = deque(maxlen=10)  # v21: for session index
        self.assembly_zero = False
        self.onboard_config = {}
        self.sessions = sessions  # --sessions flag
        self.pickup = pickup
        self.auto_onboard = auto_onboard

    def _load_repo_config(self):
        """Read .unleashed.json from target repo and apply config."""
        config_path = Path(self.cwd) / '.unleashed.json'
        if not config_path.exists():
            return
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
            gemini_config = config.get('claude', {})  # shares claude section for now

            model = gemini_config.get('model', 'default')
            effort = gemini_config.get('effort', 'default')

            self._config_echo = (f"[v{VERSION}] Config: model={model} | "
                                 f"effort={effort} | "
                                 f"assemblyZero={config.get('assemblyZero', False)}")
            log(self._config_echo)

            self.assembly_zero = config.get('assemblyZero', False)
            onboard_config = config.get('onboard', {})
            self.onboard_config = onboard_config
            if 'auto' in onboard_config and self.auto_onboard:
                self.auto_onboard = bool(onboard_config['auto'])
            if onboard_config:
                log(f"Repo config: onboard auto={self.auto_onboard}, "
                    f"pickupThreshold={onboard_config.get('pickupThresholdMinutes', 10)}m")
        except Exception as e:
            log(f"WARNING: Failed to read .unleashed.json: {e}")

    def _parse_last_handoff(self):
        """Parse last handoff entry from data/handoff-log.md."""
        try:
            handoff_path = Path(self.cwd) / 'data' / 'handoff-log.md'
            if not handoff_path.exists():
                return None
            text = handoff_path.read_text(encoding='utf-8', errors='replace')
            starts = [i for i in range(len(text)) if text[i:].startswith('<!-- handoff-start -->')]
            ends = [i for i in range(len(text)) if text[i:].startswith('<!-- handoff-end -->')]
            if not starts or not ends:
                return None
            last_start = starts[-1]
            last_end = ends[-1]
            if last_end <= last_start:
                return None
            content_start = last_start + len('<!-- handoff-start -->')
            content = text[content_start:last_end].strip()
            preamble = text[:last_start]
            ts_match = re.search(r'## Handoff\s*[—–\-]+\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', preamble)
            if not ts_match:
                return None
            handoff_dt = datetime.datetime.strptime(ts_match.group(1), '%Y-%m-%d %H:%M:%S')
            age_minutes = (datetime.datetime.now() - handoff_dt).total_seconds() / 60
            files = []
            files_section = re.search(r'## Files to Read First\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL)
            if files_section:
                for line in files_section.group(1).split('\n'):
                    path_match = re.match(r'^\d+\.\s*`([^`]+)`', line)
                    if not path_match:
                        continue
                    raw_path = path_match.group(1)
                    raw_path = re.sub(r':\d+(-\d+)?$', '', raw_path)
                    if re.search(r'(?:^|[\\/])CLAUDE\.md$', raw_path):
                        continue
                    p = Path(raw_path)
                    if not p.is_absolute():
                        p = Path(self.cwd) / p
                    files.append(str(p))
            return {"timestamp": handoff_dt, "age_minutes": age_minutes, "content": content, "files": files}
        except Exception as e:
            log(f"WARNING: _parse_last_handoff failed: {e}")
            return None

    def _check_session_health(self, handoff_ts):
        """Check session-index.jsonl for thrashing or crash patterns."""
        try:
            index_path = Path(self.cwd) / 'data' / 'session-index.jsonl'
            if not index_path.exists():
                return {"status": "no_index"}
            entries = []
            for line in index_path.read_text(encoding='utf-8').strip().split('\n'):
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            if not entries:
                return {"status": "no_index"}
            last_5 = entries[-5:]
            brief_entries = []
            for e in last_5:
                if e.get('line_count', 0) < 50:
                    try:
                        brief_entries.append(datetime.datetime.fromisoformat(e['start']))
                    except (KeyError, ValueError):
                        continue
            if len(brief_entries) >= 3:
                brief_entries.sort()
                for i in range(len(brief_entries) - 2):
                    window = (brief_entries[i + 2] - brief_entries[i]).total_seconds() / 60
                    if window <= 30:
                        last_sub = None
                        for e in reversed(entries):
                            if e.get('line_count', 0) >= 50:
                                last_sub = e.get('sid', 'unknown')
                                break
                        return {"status": "thrashing", "count": len(brief_entries), "last_substantive_sid": last_sub}
            last_entry = entries[-1]
            if last_entry.get('line_count', 0) >= 50:
                try:
                    session_start = datetime.datetime.fromisoformat(last_entry['start'])
                    if handoff_ts < session_start:
                        return {"status": "crash", "session": last_entry}
                except (KeyError, ValueError):
                    pass
            return {"status": "ok"}
        except Exception as e:
            log(f"WARNING: _check_session_health failed: {e}")
            return {"status": "error"}

    def _compose_pickup_context(self, handoff):
        """Build pickup context file from handoff data and preloaded files."""
        try:
            project = Path(self.cwd).name
            age_str = f"{handoff['age_minutes']:.0f}m"
            files_loaded = 0
            lines = [f"# Pickup Context (unleashed v{VERSION})", f"Age: {age_str} | Project: {project}", "", "## Handoff", "", handoff["content"], ""]
            try:
                remote_result = subprocess.run(['git', '-C', self.cwd, 'remote', 'get-url', 'origin'], capture_output=True, text=True, timeout=5)
                repo_match = re.search(r'github\.com[:/](.+?)(?:\.git)?$', remote_result.stdout.strip())
                if repo_match:
                    owner_repo = repo_match.group(1)
                    issues_result = subprocess.run(['gh', 'issue', 'list', '--state', 'open', '--limit', '10', '--repo', owner_repo], capture_output=True, text=True, timeout=10)
                    lines.append("## Open Issues")
                    lines.append("")
                    lines.append(issues_result.stdout.strip() if issues_result.returncode == 0 and issues_result.stdout.strip() else "No open issues or gh CLI unavailable.")
                    lines.append("")
            except Exception as e:
                lines.extend(["## Open Issues", "", f"Unavailable: {e}", ""])
            if handoff["files"]:
                lines.extend(["## Preloaded Files", ""])
                for fpath in handoff["files"]:
                    p = Path(fpath)
                    if not p.exists():
                        lines.extend([f"### {fpath}", "", "[File not found]", ""])
                        continue
                    try:
                        file_text = p.read_text(encoding='utf-8', errors='replace')
                        file_lines = file_text.split('\n')
                        if len(file_lines) > 500:
                            file_text = '\n'.join(file_lines[:500]) + f"\n[... truncated at 500/{len(file_lines)} lines ...]"
                        lines.extend([f"### {fpath}", "", file_text, ""])
                        files_loaded += 1
                    except Exception as e:
                        lines.extend([f"### {fpath}", "", f"[Read failed: {e}]", ""])
            context_text = '\n'.join(lines)
            context_path = Path(self.cwd) / 'data' / '.pickup-context.md'
            context_path.parent.mkdir(parents=True, exist_ok=True)
            context_path.write_text(context_text, encoding='utf-8')
            return str(context_path), files_loaded
        except Exception as e:
            log(f"WARNING: _compose_pickup_context failed: {e}")
            return None

    def _attempt_python_pickup(self, pty):
        """Try Python-side pickup. Returns True if handled."""
        handoff = self._parse_last_handoff()
        if not handoff:
            log("Python pickup: no handoff found")
            return False
        if handoff["age_minutes"] > 2880:
            log(f"Python pickup: handoff too old ({handoff['age_minutes']:.0f}m)")
            return False
        health = self._check_session_health(handoff["timestamp"])
        if health["status"] in ("thrashing", "crash"):
            warn = f"[v{VERSION}] {health['status'].upper()}: falling back to /onboard"
            sys.stdout.write(f"\r\n\033[93m{warn}\033[0m\r\n")
            sys.stdout.flush()
            log(warn)
            return False
        result = self._compose_pickup_context(handoff)
        if not result:
            return False
        context_path, files_loaded = result
        age_str = f"{handoff['age_minutes']:.0f}m"
        report = f"[v{VERSION}] Pickup: {age_str} ago | {files_loaded} files preloaded"
        sys.stdout.write(f"\r\n\033[90m{report}\033[0m\r\n")
        sys.stdout.flush()
        log(report)
        rel_path = os.path.relpath(context_path, self.cwd).replace('\\', '/')
        inject_msg = (f"Read {rel_path} — this is preloaded session context from the "
                      f"previous handoff ({age_str} ago, {files_loaded} files). "
                      f"Internalize it, then report ready and ask what to work on next.")
        pty.write(inject_msg + '\r')
        log(f"Python pickup: injected context message ({len(inject_msg)} chars)")
        return True

    def _display_sessions(self):
        """Display session index and exit."""
        index_path = Path(self.cwd) / 'data' / 'session-index.jsonl'
        if not index_path.exists():
            sys.stderr.write("No session index found.\n")
            sys.exit(1)
        entries = []
        for line in index_path.read_text(encoding='utf-8').strip().split('\n'):
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if not entries:
            sys.stderr.write("Session index is empty.\n")
            sys.exit(1)
        project = Path(self.cwd).name
        print(f"\nRecent sessions ({project}):")
        for i, entry in enumerate(reversed(entries), 1):
            sid = entry.get('sid', '?')
            start = entry.get('start', '?')
            dur = entry.get('duration_min', 0)
            lines_total = entry.get('line_count', 0)
            last_lines = entry.get('last_10_lines', [])
            dur_str = f"{dur // 60}h {dur % 60}m" if dur >= 60 else f"{dur}m"
            brief = " [BRIEF]" if lines_total < 50 else ""
            print(f"{'─' * 50}")
            print(f"[{i}] {start[:16]} ({dur_str}) — SID: {sid}{brief}")
            for line in last_lines[-10:]:
                clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', line)
                if clean.strip():
                    print(f"    > {clean.strip()[:100]}")
        print(f"{'─' * 50}")
        sys.exit(0)

    def _capture_session_index(self):
        """Capture last 10 lines to session-index.jsonl."""
        try:
            last_10 = list(self._recent_lines)
            elapsed = time.time() - self.session_start if self.session_start else 0
            duration_min = int(elapsed / 60)
            session_ts = self._session_ts or datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            start_dt = datetime.datetime.strptime(session_ts, '%Y%m%d-%H%M%S')
            # Count lines from raw session log
            line_count = 0
            if self.session_fh:
                try:
                    self.session_fh.flush()
                    with open(self.session_fh.name, 'r', encoding='utf-8', errors='replace') as f:
                        line_count = sum(1 for _ in f)
                except Exception:
                    pass
            record = {
                "sid": session_ts,
                "start": start_dt.isoformat(),
                "end": datetime.datetime.now().isoformat(),
                "duration_min": duration_min,
                "last_10_lines": last_10,
                "line_count": line_count,
                "project": Path(self.cwd).name
            }
            index_path = Path(self.cwd) / 'data' / 'session-index.jsonl'
            index_path.parent.mkdir(parents=True, exist_ok=True)
            with open(index_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record) + '\n')
            lines = index_path.read_text(encoding='utf-8').strip().split('\n')
            if len(lines) > 20:
                index_path.write_text('\n'.join(lines[-20:]) + '\n', encoding='utf-8')
            log(f"Session index: captured {len(last_10)} lines, {line_count} total, {duration_min}m")
        except Exception as e:
            log(f"WARNING: Failed to capture session index: {e}")

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

    def _set_tab_title(self):
        """Set terminal tab title to REPONAME YYYY-MM-DD HH:MM for easy identification."""
        repo_name = os.path.basename(self.cwd).upper()
        timestamp = time.strftime('%Y-%m-%d %H:%M')
        title = f"{repo_name} {timestamp}"
        sys.stdout.write(f'\033]0;{title}\007')
        sys.stdout.flush()
        log(f"Tab title set: {title}")

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
                wait_result = kernel32.WaitForSingleObject(self.stdin_handle, 100)
                if wait_result != WAIT_OBJECT_0:
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
                        self._done_event.set()
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
                self._done_event.set()
                break

    def _reader_pty(self, pty):
        """Read from PTY, write to stdout, tee to session log."""
        _chunk_count = 0  # v21: track chunks for init sequence
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
                    _chunk_count += 1

                    # Tee raw output to session log
                    if self.session_fh:
                        self.session_fh.write(raw_bytes)
                        self.session_fh.flush()

                    # Use overlap buffer to catch pattern split across reads
                    search_chunk = self.overlap_buffer + raw_bytes
                    if not self.in_approval:
                        for pattern in PERMISSION_PATTERNS:
                            if pattern in search_chunk:
                                log(f"Pattern matched: {pattern!r}")
                                self.do_approval(pty, pattern.decode('utf-8', errors='replace'))
                                break

                    # Keep enough overlap for the pattern
                    self.overlap_buffer = raw_bytes[-64:]

                    # v21: track recent lines for session index
                    try:
                        text_chunk = raw_bytes.decode('utf-8', errors='replace')
                        for line in text_chunk.split('\n'):
                            stripped = line.strip()
                            if stripped:
                                self._recent_lines.append(stripped)
                    except Exception:
                        pass

                    # v21: auto-onboard + config echo after initial PTY output
                    if not self._init_cmd_sent and _chunk_count >= 3:
                        self._init_cmd_sent = True
                        if self._config_echo:
                            sys.stdout.write(f"\r\n\033[90m{self._config_echo}\033[0m\r\n")
                            sys.stdout.flush()
                            self._config_echo = None
                        time.sleep(0.5)
                        pickup_ok = self._attempt_python_pickup(pty)
                        if not pickup_ok and self.auto_onboard:
                            pty.write('/onboard\r')
                            log("v21: injected /onboard")
                        if self.pickup and not pickup_ok:
                            pty.write('/onboard --pickup\r')
                            log("v21: injected /onboard --pickup (fallback)")
            except Exception as e:
                log(f"PTY reader error: {e}")
                self._done_event.set()
                break
        self._done_event.set()

    def do_approval(self, pty, pattern="unknown"):
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
        log(f"Auto-approved (sent CR) pattern={pattern!r}")
        if self.friction_logger:
            self.friction_logger.record_prompt(pattern=pattern)
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

    def _cleanup_companions(self):
        """Kill companion tab processes launched by this session (#84)."""
        if not self._session_ts:
            return
        marker = f"UNLEASHED_SID={self._session_ts}"
        try:
            result = subprocess.run(
                ['wmic', 'process', 'where',
                 f"CommandLine like '%{marker}%'",
                 'get', 'ProcessId'],
                capture_output=True, text=True, timeout=10
            )
            pids = [line.strip() for line in result.stdout.strip().split('\n')
                    if line.strip().isdigit()]
            for pid in pids:
                try:
                    subprocess.run(['taskkill', '/PID', pid, '/T', '/F'],
                                   capture_output=True, timeout=5)
                    log(f"Killed companion PID={pid}")
                except Exception as e:
                    log(f"WARNING: Failed to kill companion PID={pid}: {e}")
            if pids:
                log(f"Companion cleanup: killed {len(pids)} processes")
            else:
                log("Companion cleanup: no orphaned processes found")
        except Exception as e:
            log(f"WARNING: Companion cleanup failed: {e}")

    def run(self):
        self.session_start = time.time()
        self._session_ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')  # v21
        session_ts = self._session_ts  # #84: for companion cleanup

        # v21: --sessions flag — display and exit
        if self.sessions:
            self._display_sessions()

        sys.stderr.write(f"[Unleashed-G v{VERSION}] Starting...\n")
        sys.stderr.flush()

        # v21: load repo config before PTY spawn
        self._load_repo_config()

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

        # v20: auto-tab-naming
        self._set_tab_title()

        # v20: per-repo session logs
        _project = Path(self.cwd).name
        transcript_dir = Path(self.cwd) / 'data' / 'unleashed'
        transcript_dir.mkdir(parents=True, exist_ok=True)
        _log_dir = str(transcript_dir)

        # Set up raw session log (tee of PTY output)
        session_log_path = os.path.join(_log_dir, f"gemini-session-{_project}-{session_ts}.raw")
        self.session_fh = open(session_log_path, "wb")
        log(f"Session log: {session_log_path}")
        sys.stderr.write(f"[v{VERSION}] Transcript: {session_log_path}\n")
        sys.stderr.flush()

        # Set up friction logger
        if self.friction:
            self.friction_logger = FrictionLogger(session_ts, log_dir=_log_dir, project=_project)
            log(f"Friction logger: {self.friction_logger.human_path}")

        # Launch companion tabs
        if self.mirror:
            launch_tab("Gemini Raw", session_log_path, session_id=session_ts)
        if self.friction and self.friction_logger:
            launch_tab("Friction", self.friction_logger.human_path, session_id=session_ts)

        # v20 #67: console tab cd'd to target repo
        if self.console:
            launch_console_tab(_project, self.cwd, session_id=session_ts)

        # v20 #66: focus back to session tab after launching companions
        if self.mirror or self.friction or self.console:
            time.sleep(0.3)
            focus_tab(self.home_tab)

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
            self._done_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            self._cleanup_companions()  # #84: kill companion tabs before they orphan
            time.sleep(0.2)

            # v21: capture session index before closing loggers
            self._capture_session_index()

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
            sys.stderr.write("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n")
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
    parser.add_argument("--console", action="store_true", default=True, help="Open a bash console tab cd'd to target repo (default: on)")
    parser.add_argument("--no-console", action="store_false", dest="console", help="Disable console tab")
    parser.add_argument("--home-tab", type=int, default=0, help="Tab index to focus after launching companion tabs (default: 0)")
    parser.add_argument('--sessions', action='store_true', help='Show recent sessions and exit')
    parser.add_argument('--pickup', action='store_true', help='Import last handoff context')
    parser.add_argument('--no-onboard', action='store_true', help='Skip auto-onboard injection')
    args = parser.parse_args()
    UnleashedG(cwd=args.cwd, mirror=args.mirror, friction=args.friction,
               console=args.console, home_tab=args.home_tab,
               sessions=args.sessions, pickup=args.pickup,
               auto_onboard=not args.no_onboard).run()
