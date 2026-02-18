# CLAUDE.md - Unleashed

Permission bypass system for Claude Code - enables autonomous coding sessions.

## Key Files

- `src/unleashed-c-18.py` — Claude production (PTY wrapper with auto-approval)
- `src/unleashed-c-23.py` — Claude latest (mirror noise filters + typeahead prompt collapsing)
- `src/unleashed-g-19.py` — Gemini production (triplet + 3 permission patterns)
- `src/sentinel.py` — Standalone security gatekeeper (CLI)
- `src/sentinel_gate.py` — Sentinel gate class (Haiku API, worker thread)
- `src/sentinel_rules.py` — Local regex rules from `~/.agentos/` safety data
- `src/clean_transcript.py` — Post-session transcript cleaner
- `src/transcript_filters.py` — Shared 95-pattern garbage filter
- `archive/` — Historical versions

## Running

```bash
unleashed              # Claude production (c-18)
unleashed-c-21-triplet # Claude latest with mirror + friction tabs
unleashed-g            # Gemini production (g-19)
unleashed-sentinel     # Latest with sentinel gate (Bash-only)
sentinel               # Standalone security check
```

## Sentinel Flags

```bash
--sentinel-shadow      # Log what sentinel would evaluate (no API calls)
--sentinel             # Enable sentinel gate for Bash commands
--sentinel-scope bash  # Same as --sentinel
--sentinel-scope write # Gate Bash + Write + Edit
--sentinel-scope all   # Gate all tool types
```
