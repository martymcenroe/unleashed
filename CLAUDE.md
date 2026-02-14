# CLAUDE.md - Unleashed

Permission bypass system for Claude Code - enables autonomous coding sessions.

## First: Read AgentOS Core Rules

Before any work, read: `C:\Users\mcwiz\Projects\AgentOS\CLAUDE.md`

## Project Structure

```
unleashed/
├── src/                    # Active Python files
│   ├── unleashed.py        # Main production version
│   ├── unleashed-test.py   # Test variant
│   ├── unleashed-danger.py # Danger mode (minimal safeguards)
│   ├── unleashed-guarded.py # AI-gated approval
│   ├── sentinel.py         # Security gatekeeper
│   └── sentinel-test.py
├── archive/                # Historical versions (25+ files)
├── docs/                   # Documentation
├── plan/                   # Planning artifacts (Gemini conversations)
└── logs/                   # Forensic session logs
```

## Running

From any project directory:
```bash
unleashed          # Launches Claude with auto-approval
unleashed-test     # Test variant
sentinel           # Security monitoring mode
```

## Development

```bash
cd /c/Users/mcwiz/Projects/unleashed
poetry install     # Install dependencies
poetry run python src/unleashed.py --cwd "C:\Users\mcwiz\Projects\SomeProject"
```

## Key Files

- `src/unleashed.py` - Production PTY wrapper with auto-approval
- `src/sentinel.py` - Security gatekeeper for sensitive operations
- `docs/commands.md` - User-facing documentation

## Logs

All session logs are stored in `logs/`:
- `unleashed_YYYYMMDD_HHMMSS.log` - Raw session output
- `unleashed_events_YYYYMMDD_HHMMSS.jsonl` - Structured event stream

