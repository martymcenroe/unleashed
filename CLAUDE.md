# CLAUDE.md - Unleashed

Permission bypass system for Claude Code - enables autonomous coding sessions.

## Key Files

- `src/unleashed-c-30.py` — Claude production (c-29 + auto-onboard, session index, --sessions, config echo)
- `src/unleashed-c-29.py` — Claude historical (c-28 + compaction regex fix, timer via tab title)
- `src/unleashed-c-28.py` — Claude historical (auto-handoff: compaction trigger + timer reminder)
- `src/unleashed-g-20.py` — Gemini production (g-19 + console tab, per-repo logs, focus-back, auto-tab-naming)
- `src/unleashed-g-19.py` — Gemini historical (triplet + 3 permission patterns)
- `src/unleashed-t-03.py` — Codex production (t-02 + proxy fix, schannel→openssl, --search)
- `src/unleashed-t-01.py` — Codex historical (PTY wrapper, session logging)
- `src/unleashed-t-02.py` — Codex historical (t-01 + console tab, per-repo logs, focus-back)
- `src/sentinel.py` — Standalone security gatekeeper (CLI)
- `src/sentinel_gate.py` — Sentinel gate class (Haiku API, worker thread)
- `src/sentinel_rules.py` — Local regex rules from `~/.agentos/` safety data
- `src/clean_transcript.py` — Post-session transcript cleaner
- `src/transcript_filters.py` — Shared 95-pattern garbage filter
- `.unleashed.json` — Per-repo config (model, effort, assemblyZero, onboard settings)
- `archive/` — Historical versions

## Running

```bash
unleashed              # Claude production (c-30)
unleashed-alpha        # (no alpha configured)
unleashed-g            # Gemini production (g-20)
unleashed-t            # Codex production (t-03)
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
