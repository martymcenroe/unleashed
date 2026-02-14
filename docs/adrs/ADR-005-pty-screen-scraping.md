# ADR-005: PTY Screen Scraping for Permission Detection

**Status:** Implemented
**Date:** 2026-02-14
**Categories:** Infrastructure, Security, UX

## 1. Context

Claude Code is a CLI tool that asks the user for permission before executing potentially dangerous operations (Bash commands, file writes, web requests). These permission prompts appear as interactive UI elements rendered with [Ink](https://github.com/vadimdemedes/ink), a React-like framework for terminal applications.

Unleashed needs to detect these prompts and auto-approve them. The fundamental question: **how do you programmatically detect a permission prompt in a terminal application?**

Claude Code provides no machine-readable interface for permission events:
- No IPC socket or named pipe
- No file-based event system (no `.claude/pending-approval.json`)
- No command-line flag for auto-approval
- No environment variable to suppress prompts
- No API endpoint for permission state

The only observable output is the raw byte stream from the PTY (pseudo-terminal). This stream contains a mix of:
- Human-readable text (Claude's responses)
- ANSI escape sequences (colors, cursor positioning, screen clearing)
- Ink rendering artifacts (progressive repaints, cursor-addressed updates)
- Permission prompt UI (the "Allow this action?" text with Yes/No options)

## 2. Decision

**We will detect permission prompts by matching byte patterns in the raw PTY output stream.**

Four patterns identify permission prompts:
```python
PERMISSION_PATTERNS = [
    b"Allow this action?",
    b"Allow tool?",
    b"Do you want to proceed?",
    b"Press Enter to allow",
]
```

When matched, `extract_permission_context_structured()` uses a regex to identify the tool type (Bash, Write, Edit, etc.) and arguments from the surrounding context buffer.

## 3. Alternatives Considered

### Option A: PTY Byte Stream Pattern Matching — SELECTED

**Description:** Read raw PTY output, search for known permission prompt strings, extract tool context via regex.

**Pros:**
- **Works today** — no upstream changes needed
- Zero coupling to Claude Code's internals (treats it as a black box)
- Simple implementation: 4 byte patterns + 1 regex
- Battle-tested through 21 versions and 28 archived iterations
- Handles all current permission prompt variants

**Cons:**
- **Fragile:** Any change to Claude Code's permission prompt text breaks detection
- **No semantic understanding:** Matched text in LLM output could trigger false positives (issue #39)
- **ANSI interference:** Escape sequences interspersed in text can prevent pattern matching
- Tool type extraction depends on context buffer size (issue #43)
- Testing requires a running Claude Code instance (no unit testable abstraction)

### Option B: Fork Claude Code — Rejected

**Description:** Fork the Claude Code repository, add a machine-readable permission event (e.g., write to a file or named pipe when a permission prompt appears).

**Pros:**
- Clean, reliable interface — no screen scraping
- Could expose rich context (tool type, args, file paths)
- Unit testable

**Cons:**
- **Maintenance burden:** Every Claude Code update requires a merge
- Claude Code is actively developed — upstream changes would constantly break the fork
- Forking closed-source tooling (even if technically possible) creates legal ambiguity
- Defeats the purpose: unleashed exists because Claude Code doesn't have this interface

### Option C: Monkey-Patching Claude Code — Rejected

**Description:** Intercept Claude Code's internal permission function at runtime (e.g., via Node.js `--require` or module patching).

**Pros:**
- No fork needed — patches at runtime
- Could intercept the actual permission decision point

**Cons:**
- **Extremely fragile:** Depends on internal function names and module paths
- Any refactoring of Claude Code's internals (which happens frequently) breaks the patch
- No documented internal API — would require reverse-engineering every version
- Security risk: arbitrary code injection into Claude Code's process

### Option D: Wait for Official API — Rejected

**Description:** Don't build unleashed. Wait for Anthropic to add an auto-approval flag or API.

**Pros:**
- Clean, supported solution
- No maintenance burden

**Cons:**
- **Doesn't exist as of February 2026** — and no indication it's planned
- The user needs autonomous sessions now, not eventually
- Even if added, may not be configurable enough (e.g., "approve all" vs. "approve with conditions")

## 4. Rationale

This isn't a "best option" decision — it's an "only viable option" decision. Options B, C, and D are all theoretically cleaner but practically impossible or unacceptable.

Screen scraping is fragile by nature. The mitigation strategy is:
1. **Multiple patterns:** 4 different prompt strings reduce the chance of total detection failure
2. **Overlap buffer:** 32-byte overlap between read chunks prevents missing patterns split across reads
3. **Context buffer:** 2KB of ANSI-stripped context for tool type extraction (acknowledged as too small — issue #43)
4. **Sentinel as safety net:** Even if detection fails (false positive), sentinel evaluates the command before approval
5. **Version monitoring:** GitHub Action (`release-watch.yml`) alerts when Claude Code releases a new version, enabling proactive pattern validation

The fundamental constraint is architectural: Claude Code is a closed-source terminal application with no plugin API. Until that changes, screen scraping is the only way to build tooling around it.

## 5. Security Risk Analysis

| Risk | Impact | Likelihood | Severity | Mitigation |
|------|--------|------------|----------|------------|
| Pattern spoofing — LLM output triggers false approval | High | Low | 4 | Issue #39; mitigated by ANSI anchoring and sentinel evaluation |
| Claude Code changes prompt text — detection fails | Med | Med | 4 | Release-watch GitHub Action; patterns are simple strings, easy to update |
| ANSI sequences break pattern match | Med | Med | 4 | Overlap buffer + ANSI stripping on context buffer |
| Tool type misidentification | Med | Med | 4 | Issue #43; context buffer size and extraction method need improvement |
| Double-approval (two CRs sent) | Low | Low | 2 | `in_approval` flag serializes approvals |

**Residual Risk:** The screen scraping approach is inherently coupled to Claude Code's UI. Any upstream change to permission prompt rendering, text, or Ink component structure could silently break detection. The release-watch action provides early warning but not prevention.

## 6. Consequences

### Positive
- Unleashed exists — the only viable approach actually works
- 21 versions and 4+ weeks of daily production use validate the approach
- Simple enough that a single developer can maintain pattern updates
- Sentinel integration adds a safety layer on top of the detection mechanism

### Negative
- Every Claude Code update is a potential breaking change
- No way to unit test detection without a running Claude Code instance
- False positives are possible (issue #39) — LLM output could contain prompt text
- The approach can never be fully reliable — it's parsing rendered output, not structured data

### Neutral
- This is the same approach used by other terminal automation tools (expect, pexpect, autoexpect)
- The terminal scraping community has decades of experience with these tradeoffs
- If Anthropic ever adds an official API, migration would be straightforward — replace pattern matching with API subscription

## 7. Implementation

- **Related Issues:** #39 (pattern spoofing), #43 (context buffer), #31 (long-term strategy)
- **Files:** `src/unleashed-c-21.py` (`PERMISSION_PATTERNS`, `TOOL_CALL_RE`, `extract_permission_context_structured()`, `_reader_pty()`)
- **Status:** Implemented (v00010, continuously evolved through c-21)

### Detection Flow

1. PTY reader receives raw bytes from `pty.read(4096)`
2. Bytes are prepended with 32-byte overlap from previous read
3. Search for any of 4 PERMISSION_PATTERNS in the combined chunk
4. If matched: extract tool type/args from ANSI-stripped context buffer via `TOOL_CALL_RE`
5. Pass to `do_approval()` → sentinel check or immediate approval

### Pattern Maintenance

When Claude Code updates permission prompt text:
1. `release-watch.yml` GitHub Action fires on new release
2. Manual testing in a test project confirms detection
3. If broken: update `PERMISSION_PATTERNS` (single-line change)
4. If fundamentally changed: update `_reader_pty()` detection logic

## 8. References

- [Ink](https://github.com/vadimdemedes/ink) — React for CLIs (Claude Code's rendering framework)
- [pexpect](https://pexpect.readthedocs.io/) — Python module for spawning and controlling terminal applications
- [autoexpect](https://linux.die.net/man/1/autoexpect) — Classic terminal automation via pattern matching
- Issue #31 — Pattern matching: fragility and long-term strategy
- Issue #14 — Research: How does the Claude Code team test their TUI?

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-02-14 | Claude Opus 4.6 | Initial draft |
