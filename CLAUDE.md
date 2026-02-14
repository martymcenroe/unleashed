# CLAUDE.md - Unleashed

Permission bypass system for Claude Code - enables autonomous coding sessions.

## Key Files

- `src/unleashed-c-18.py` — Production (PTY wrapper with auto-approval)
- `src/unleashed-c-21.py` — Latest (shared garbage filter + rate-limited mirror)
- `src/sentinel.py` — Security gatekeeper
- `src/clean_transcript.py` — Post-session transcript cleaner
- `src/transcript_filters.py` — Shared 95-pattern garbage filter
- `archive/` — Historical versions

## Running

```bash
unleashed              # Production (c-18)
unleashed-c-21-triplet # Latest with mirror + friction tabs
sentinel               # Security monitoring
```

