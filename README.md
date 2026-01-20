# Unleashed

Private repository for the permission bypass system for Claude Code.

## What is Unleashed?

Unleashed is a PTY wrapper that intercepts permission prompts from Claude Code and auto-approves them, enabling fully autonomous coding sessions.

## Components

- **unleashed.py** - Main production version
- **sentinel.py** - Security gatekeeper for sensitive operations
- **unleashed-guarded.py** - AI-gated approval using Anthropic API

## Usage

```bash
# From any project directory
unleashed          # Launch autonomous Claude session
sentinel           # Launch with security monitoring
```

## Requirements

- Python 3.10+
- Poetry
- Windows (uses pywinpty)

## Installation

```bash
poetry install
```

## License

Private - not for distribution.
