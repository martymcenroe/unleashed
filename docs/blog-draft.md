# Unleashed: Taming Permission Fatigue in Claude Code

*How a weekend hack turned into a productivity multiplier for multi-agent workflows*

---

## The Problem: Death by a Thousand Prompts

If you've used Claude Code for any serious work, you know the drill. Every file read, every bash command, every edit—Claude politely asks for permission. And you politely press Enter. Again. And again.

For a quick task, it's fine. For a 30-minute coding session? You're pressing Enter every 10-20 seconds. I counted once: **25+ permission prompts in a single session**. My workflow became:

1. Give Claude a task
2. Wait for permission prompt
3. Press Enter
4. Repeat 25 times
5. Wonder why I'm so tired

The irony wasn't lost on me. I was using an AI to save time while spending that time babysitting it.

## The Idea: A Dead Man's Switch

The solution seemed obvious: auto-approve everything. But that felt reckless. What if Claude decided to `rm -rf /` while I was getting coffee?

I wanted a **dead man's switch**—automatic approval after a countdown, with the ability to cancel if something looked wrong. Like a train conductor's vigilance device, but for AI permissions.

The concept:
- Detect when Claude shows a permission prompt
- Start a 10-second countdown
- If I do nothing, approve automatically
- If I press any key, cancel and let me intervene

## Building Unleashed

Unleashed is a PTY (pseudo-terminal) wrapper that sits between you and Claude Code. It watches the terminal output, detects permission prompts, and handles the countdown/approval logic.

### The Architecture

```
┌─────────────┐     ┌───────────────┐     ┌─────────────┐
│   You       │────▶│   Unleashed   │────▶│ Claude Code │
│  (keyboard) │◀────│  (PTY proxy)  │◀────│   (PTY)     │
└─────────────┘     └───────────────┘     └─────────────┘
```

Key components:
- **PTY spawning** via `pywinpty` (Windows) or standard PTY (Unix)
- **Footer detection** using regex to find "Esc to cancel · Tab to add additional instructions"
- **Countdown overlay** showing remaining seconds
- **Keystroke passthrough** so you can still type normally
- **Event logging** for debugging and analytics

### The Smart Option Problem

After the first version worked, I noticed something annoying. Permission prompts come in two flavors:

**2-option prompts:**
```
1. Yes
2. No
```

**3-option prompts:**
```
1. Yes
2. Yes, and don't ask again for this command type
3. No
```

Option 2 in the 3-option prompt is gold—it remembers the permission so Claude won't ask again. But my auto-approval was always selecting option 1, meaning I'd get the same prompt again 20 seconds later.

The fix: detect 3-option prompts by looking for "3. No" in the buffer, then send Shift+Tab before Enter to select option 2.

```python
def _detect_three_options(self) -> bool:
    """Check if the current prompt has 3 options."""
    clean_text = strip_ansi(self.screen_buffer)
    recent = clean_text[-500:]
    return bool(re.search(r'3\.\s*No', recent))
```

This simple addition dramatically reduced repeat prompts.

### Windows Keyboard Hell

The hardest bugs weren't in the approval logic—they were in keyboard handling.

**Bug 1: Arrow keys showing as garbage**

Windows returns arrow keys as two-byte sequences: `\xe0` followed by a scan code. My first implementation only read the first byte, turning "down arrow" into `à█`.

**Bug 2: Shift+Tab not working**

Users couldn't cycle through Claude's mode selector because Shift+Tab (`\x00\x0f`) wasn't being translated to the ANSI sequence (`\x1b[Z`).

The fix was a comprehensive key mapping:

```python
key_map = {
    'H': '\x1b[A',   # Up
    'P': '\x1b[B',   # Down
    'K': '\x1b[D',   # Left
    'M': '\x1b[C',   # Right
    '\x0f': '\x1b[Z', # Shift+Tab
    # ... etc
}
```

### Versioning for Multi-Agent Chaos

I often run 3-4 Claude agents simultaneously across different projects. After pushing an update, I'd forget which windows had the new code.

Solution: embed a version number and log it:

```python
VERSION = "1.1.1"

# In the START event:
self.logger.log_event("START", version=VERSION, ...)
```

Now `/unleashed --check` scans all recent logs and tells me which agents need restarting:

```
| Session | Project  | Version | Status           |
|---------|----------|---------|------------------|
| 145420  | AgentOS  | 1.1.1   | Current          |
| 131517  | Aletheia | (none)  | Restart needed   |
| 124833  | Talos    | (none)  | Restart needed   |
```

## The Results

Before Unleashed:
- Constant context switching to approve prompts
- ~25 manual Enter presses per session
- Can't step away without Claude stalling

After Unleashed:
- Set it and forget it (with 10-second safety net)
- Smart selection remembers permissions
- Actually able to take a coffee break

The productivity gain isn't just the keystrokes saved—it's the **mental bandwidth recovered**. I can actually think about the code instead of playing permission whack-a-mole.

## Try It Yourself

Unleashed is part of [AgentOS](https://github.com/martymcenroe/AgentOS), my collection of tools for working with AI coding assistants.

```bash
# Clone and install
git clone https://github.com/martymcenroe/AgentOS
cd AgentOS
poetry install

# Add alias to your shell config
alias unleashed='poetry run --directory /path/to/AgentOS python /path/to/AgentOS/tools/unleashed.py --cwd "$(pwd)"'

# Run it
cd your-project
unleashed
```

**Security note:** This tool auto-approves everything. Use at your own risk. The 10-second countdown is your safety net—pay attention to what Claude is about to do.

## What's Next

Open issues:
- **Countdown visibility** (#13): The overlay currently fights with Claude's TUI. Need a better display strategy.
- **Cross-platform testing**: Built on Windows, needs Unix validation.

The permission prompt UX in Claude Code will probably improve over time. Until then, Unleashed keeps me sane.

---

*Built with Claude Opus 4.5, battle-tested across Aletheia, Talos, and AgentOS. Special thanks to the permission prompts that made this tool necessary.*
