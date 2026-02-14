# ADR-003: Fail-Open Sentinel Design

**Status:** Implemented
**Date:** 2026-02-14
**Categories:** Security, Reliability, UX

## 1. Context

Sentinel is a safety gate that evaluates commands before auto-approval. It calls the Anthropic Haiku API with a 3-second timeout. API calls can fail for many reasons: network issues, rate limits, API outages, malformed responses, timeout expiration.

The design question: **what happens when the safety check itself fails?**

This decision has unusually high stakes for a single-user developer tool. Unleashed is the user's primary productivity tool — it enables 45-minute autonomous coding sessions. If the safety system makes the tool unreliable, the user will disable it. A safety system nobody uses provides zero safety.

The threat model is also specific: the adversary is **accidental destruction** (a hallucinating LLM running `rm -rf` in the wrong directory), not **targeted attack** (an attacker with network access trying to exploit the system). This distinction drives the entire decision.

## 2. Decision

**We will fail open: when the sentinel API call fails, the command is auto-approved with a visible warning.**

The user sees a yellow `[SENTINEL] API error, fail-open: {error_type}` message on stderr, and the permission prompt is approved as if sentinel wasn't running.

## 3. Alternatives Considered

### Option A: Fail-Open with Warning — SELECTED

**Description:** API error → print yellow warning to stderr → send CR to PTY → command executes.

**Pros:**
- Session never freezes due to sentinel failure
- User is informed that safety evaluation was skipped
- Preserves the core value proposition: autonomous, uninterrupted sessions
- Matches the threat model: occasional missed checks are acceptable because the adversary is probabilistic (LLM hallucination), not deterministic (targeted attack)

**Cons:**
- A command that sentinel would have blocked may execute unchecked
- An attacker who can DOS the Anthropic API can bypass all sentinel checks
- "Fail-open" sounds bad in security reviews

### Option B: Fail-Closed (Block on Error) — Rejected

**Description:** API error → withhold approval → user sees the permission prompt and must decide manually.

**Pros:**
- Maximum safety: no command executes without evaluation
- Satisfies "defense in depth" orthodoxy

**Cons:**
- **Any API hiccup halts the entire session** — the user must manually approve every prompt until the API recovers
- Anthropic API has documented rate limits, maintenance windows, and regional outages
- A 30-second API outage would surface as 5-10 manual approval prompts — enough to destroy flow state
- Users would disable sentinel after the first bad experience ← **this is the real failure mode**
- The threat model doesn't justify this tradeoff: we're protecting against accidental `rm -rf`, not APT-level attacks

### Option C: Fail-Prompt (Show User, Let Them Decide) — Deferred

**Description:** API error → print message → pause → show the user the original permission prompt with context → let them manually approve or deny.

**Pros:**
- User-in-the-loop for safety-critical decision
- More nuanced than binary fail-open/fail-closed
- Respects user agency

**Cons:**
- Requires UX design: how to present the context without overwhelming
- Still interrupts flow state (though less than fail-closed, since it's optional)
- Implementation complexity: need to preserve prompt context across the error path
- **Deferred to Phase 2** — worth building once we have shadow mode data on failure frequency

## 4. Rationale

The deciding factor is **adoption risk vs. security risk**.

Security risk of fail-open: occasionally, a command that sentinel would have blocked executes without evaluation. Given that the adversary is LLM hallucination (rare) and the local rules catch 80%+ of cases without an API call, the actual risk is: a novel dangerous command hits sentinel during an API outage. This is a low-probability intersection.

Adoption risk of fail-closed: the user disables sentinel after it freezes their session during an API hiccup. This is near-certain, because the user has demonstrated (by building unleashed in the first place) that uninterrupted sessions are their top priority.

A safety system that gets disabled provides exactly zero safety. A safety system that works 95% of the time and fails gracefully the other 5% provides substantial safety. **The fail-open design maximizes expected safety by maximizing adoption.**

## 5. Security Risk Analysis

| Risk | Impact | Likelihood | Severity | Mitigation |
|------|--------|------------|----------|------------|
| Dangerous command during API outage | High | Low | 4 | Local rules catch 80%+ without API; only UNCERTAIN commands affected |
| Attacker DOSes Anthropic API to bypass sentinel | High | Very Low | 3 | Single-user tool on local machine; attacker would need network access |
| User ignores yellow warnings (habituation) | Med | Med | 4 | Keep warnings rare; local rules minimize API calls |
| API key leak in error message | High | Med | 6 | Issue #38: Sanitize exceptions (ADR-002 ref) |
| Persistent API failure → sentinel never evaluates | Med | Low | 3 | Session-end stats show api_error count; user notices |

**Residual Risk:** During a sustained API outage, sentinel provides only local rule coverage (80%+). The remaining 20% of UNCERTAIN commands execute without AI evaluation. This is accepted because:
1. The 80% coverage from local rules is valuable on its own
2. Sustained API outages are rare (<1 hour/month historically)
3. The user is present and can Ctrl+C if they see something wrong

## 6. Consequences

### Positive
- Sentinel adoption: users trust it because it never breaks their workflow
- Session reliability: sentinel is invisible when working, merely degraded when failing
- Clear mental model: "sentinel helps when it can, stays out of the way when it can't"

### Negative
- Security reviews will flag "fail-open" as a concern (this ADR is the defense)
- Requires monitoring to detect persistent API failures (session-end stats)
- The yellow warning could habituate users to ignore sentinel messages

### Neutral
- The fail-open/fail-closed decision will need revisiting if sentinel expands to multi-user or CI/CD contexts (where the threat model changes)

## 7. Implementation

- **Related Issues:** #12 (sentinel integration), #38 (API key in errors)
- **Files:** `src/unleashed-c-21.py` (`_sentinel_check()` ERROR path), `src/sentinel_gate.py` (`_api_check()`)
- **Status:** Implemented (2026-02-14)

The fail-open path in `_sentinel_check()`:
```python
else:  # ERROR — fail open
    log(f"SENTINEL ERROR ({elapsed_ms}ms): {reason}")
    sys.stderr.write(f"\n\033[93m[SENTINEL] API error, fail-open: {reason[:80]}\033[0m\n")
    sys.stderr.flush()
    time.sleep(0.1)
    pty.write('\r')  # Approve anyway
    time.sleep(0.1)
```

## 8. References

- Netflix, "Designing for Failure" — fail-open circuit breakers in distributed systems
- [NIST SP 800-53](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final) — SC-24: Fail in Known State
- Issue #12 — Sentinel integration plan (Phase 1 design discussion)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-02-14 | Claude Opus 4.6 | Initial draft |
