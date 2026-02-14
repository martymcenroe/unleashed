"""
Sentinel Local Rules — fast regex-based safety decisions.

Loads existing safety data from ~/.agentos/:
  - hard_block_commands.txt (17 regex patterns for dangerous commands)
  - safe_paths.txt (allowed directories for destructive operations)
  - excluded_paths.txt (forbidden paths — OneDrive, AppData, system dirs)

Returns ALLOW, BLOCK, or UNCERTAIN. UNCERTAIN triggers API fallback.
"""
import os
import re

ALLOW = "ALLOW"
BLOCK = "BLOCK"
UNCERTAIN = "UNCERTAIN"

# Commands that are always safe — no API call needed
SAFE_BASH_PATTERNS = [
    re.compile(r'^(ls|dir|cat|head|tail|less|more|wc|file|stat|type)\b'),
    re.compile(r'^git\s+(status|log|diff|show|branch\s+--list|branch\s+-vv|remote|stash\s+list|tag|fetch|worktree\s+list)\b'),
    re.compile(r'^git\s+-C\s+\S+\s+(status|log|diff|show|branch|remote|stash|tag|fetch|worktree)\b'),
    re.compile(r'^(pwd|echo|printf|date|whoami|hostname|uname)\b'),
    re.compile(r'^(grep|rg|find|fd|ag)\b'),
    re.compile(r'^(python|node|npm|npx|poetry|pip)\s+(--version|--help)\b'),
    re.compile(r'^poetry\s+(run|install|add|show|lock)\b'),
    re.compile(r'^pytest\b'),
    re.compile(r'^(cd|pushd|popd|mkdir)\b'),
    re.compile(r'^gh\s+(issue|pr|repo|api)\s+(list|view|create|edit|close)\b'),
    re.compile(r'^powershell\.exe\s+-Command\s+"Get-Date'),
    re.compile(r'^tree\b'),
]


def _load_patterns(filename):
    """Load regex patterns from a config file (one per line, # comments)."""
    path = os.path.join(os.path.expanduser("~"), ".agentos", filename)
    patterns = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    try:
                        patterns.append(re.compile(line, re.IGNORECASE))
                    except re.error:
                        pass  # skip malformed patterns
    except FileNotFoundError:
        pass
    return patterns


def _load_paths(filename):
    """Load path list from a config file (one per line, # comments)."""
    path = os.path.join(os.path.expanduser("~"), ".agentos", filename)
    paths = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    paths.append(line)
    except FileNotFoundError:
        pass
    return paths


class LocalRules:
    def __init__(self):
        self.hard_blocks = _load_patterns("hard_block_commands.txt")
        self.safe_paths = _load_paths("safe_paths.txt")
        self.excluded_paths = _load_paths("excluded_paths.txt")
        # Fallback safe paths if config missing
        if not self.safe_paths:
            self.safe_paths = [r"C:\Users\mcwiz\Projects", "/c/Users/mcwiz/Projects"]

    def check_bash(self, command: str, cwd: str = "") -> tuple:
        """Check a Bash command against local rules.

        Returns (verdict, reason). Verdict is ALLOW, BLOCK, or UNCERTAIN.
        """
        cmd = command.strip()

        # Hard blocks first — always dangerous
        for pattern in self.hard_blocks:
            if pattern.search(cmd):
                # Some hard blocks are conditional (allowed within safe paths)
                # Check if the command targets a safe path
                if self._is_conditional_block(cmd, cwd):
                    continue  # skip this block, let safe patterns or UNCERTAIN handle it
                return (BLOCK, f"Hard-blocked: {pattern.pattern}")

        # Known safe patterns — no API needed
        for pattern in SAFE_BASH_PATTERNS:
            if pattern.search(cmd):
                return (ALLOW, "Safe command pattern")

        # Git commands that are generally safe (commit, add, push without --force)
        if re.search(r'^git\s+(add|commit|push(?!\s+--force)(?!\s+-f\s))\b', cmd):
            return (ALLOW, "Safe git operation")
        if re.search(r'^git\s+-C\s+\S+\s+(add|commit|push(?!\s+--force)(?!\s+-f\s))\b', cmd):
            return (ALLOW, "Safe git operation")

        # Uncertain — needs API
        return (UNCERTAIN, "")

    def _is_conditional_block(self, cmd: str, cwd: str) -> bool:
        """Check if a conditionally-blocked command targets a safe path."""
        # If we have a cwd and it's within a safe path, allow the conditional block
        if cwd:
            normalized_cwd = cwd.replace('\\', '/').lower()
            for safe in self.safe_paths:
                normalized_safe = safe.replace('\\', '/').lower()
                if normalized_cwd.startswith(normalized_safe):
                    return True
        # Also check if the command itself references a safe path
        cmd_lower = cmd.lower().replace('\\', '/')
        for safe in self.safe_paths:
            normalized_safe = safe.replace('\\', '/').lower()
            if normalized_safe in cmd_lower:
                return True
        return False

    def check_write(self, file_path: str) -> tuple:
        """Check a Write/Edit target path.

        Returns (verdict, reason). Verdict is ALLOW, BLOCK, or UNCERTAIN.
        """
        normalized = file_path.replace('\\', '/').lower()

        # Check excluded paths first
        for excluded in self.excluded_paths:
            if excluded.lower() in normalized:
                return (BLOCK, f"Excluded path: {excluded}")

        # Check if within safe paths
        for safe in self.safe_paths:
            normalized_safe = safe.replace('\\', '/').lower()
            if normalized.startswith(normalized_safe):
                return (ALLOW, f"Within safe path: {safe}")

        # Outside all known paths — uncertain
        return (UNCERTAIN, "")
