"""
Sentinel Gate — AI safety check for Unleashed permission auto-approval.

Runs in a worker thread (NOT the PTY reader thread) to avoid blocking.
Uses local regex rules first (fast), falls back to Haiku API for ambiguous commands.

Phase 1: Bash-only gate
Phase 2: Configurable scope (bash, write, all)
Phase 3: Local rules from ~/.agentos/ data + API fallback
"""
import os
import time
import logging

from sentinel_rules import LocalRules

log = logging.getLogger("sentinel_gate")

# Verdict constants
ALLOW = "ALLOW"
BLOCK = "BLOCK"
ERROR = "ERROR"
UNCERTAIN = "UNCERTAIN"

SYSTEM_PROMPT = (
    "You are Sentinel, a security gatekeeper for a Windows/Git Bash development environment. "
    "The user is a Professional Engineer running Claude Code with auto-approval (Unleashed). "
    "You receive either a Bash command or a file operation (Write/Edit with a path). "
    "For Bash: check for deletion outside project dirs, secret exfiltration, system modification, "
    "disk operations, or piping to shell. "
    "For Write/Edit: check the target path is within project directories, not overwriting "
    "system files, credentials, or config files outside the working tree. "
    "Reply exactly 'ALLOW' if safe, or 'BLOCK: <reason>' if dangerous."
)


class SentinelGate:
    def __init__(self, api_key: str, timeout: float = 3.0):
        self.api_key = api_key
        self.timeout = timeout
        self._client = None  # lazy init
        self.local_rules = LocalRules()
        self.stats = {
            "local_allow": 0,
            "local_block": 0,
            "api_allow": 0,
            "api_block": 0,
            "api_error": 0,
        }

    def _get_client(self):
        if self._client is None:
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self.api_key)
        return self._client

    def check(self, tool_type: str, tool_args: str, cwd: str) -> tuple:
        """Check a tool invocation for safety.

        Returns (verdict, reason) where verdict is ALLOW, BLOCK, or ERROR.
        Tries local rules first; falls back to Haiku API for UNCERTAIN.
        """
        # Phase 3: Local rules first
        if tool_type == "Bash":
            verdict, reason = self.local_rules.check_bash(tool_args, cwd)
        elif tool_type in ("Write", "Edit"):
            verdict, reason = self.local_rules.check_write(tool_args)
        else:
            verdict = UNCERTAIN
            reason = ""

        if verdict == ALLOW:
            self.stats["local_allow"] += 1
            return (ALLOW, reason)
        elif verdict == BLOCK:
            self.stats["local_block"] += 1
            return (BLOCK, reason)

        # UNCERTAIN — fall through to Haiku API
        return self._api_check(tool_type, tool_args, cwd)

    def _api_check(self, tool_type: str, tool_args: str, cwd: str) -> tuple:
        """Call Haiku API for safety verdict."""
        try:
            client = self._get_client()
            user_message = f"Tool: {tool_type}\nCWD: {cwd}\nArgs: {tool_args[:500]}"
            response = client.messages.create(
                model="claude-3-5-haiku-latest",
                max_tokens=100,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                timeout=self.timeout,
            )
            verdict_text = response.content[0].text.strip()
            if verdict_text.startswith("ALLOW"):
                self.stats["api_allow"] += 1
                return (ALLOW, "")
            else:
                reason = verdict_text.replace("BLOCK:", "").strip()
                self.stats["api_block"] += 1
                return (BLOCK, reason)
        except Exception as e:
            self.stats["api_error"] += 1
            return (ERROR, str(e))
