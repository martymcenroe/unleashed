# ADR-002: Worker Thread for Sentinel Gate

**Status:** Implemented
**Date:** 2026-02-14
**Categories:** Security, Performance, Reliability

## 1. Context

Unleashed auto-approves every Claude Code permission prompt by sending a carriage return to the PTY. This happens in the PTY reader thread (`_reader_pty`), which is the single thread responsible for reading all Claude Code output.

Issue #12 required integrating Sentinel — a Haiku-based safety evaluator — to check commands before approval. The integration point is `do_approval()`, called from the PTY reader thread when a permission prompt pattern is detected.

The first integration attempt (`archive/unleashed-guarded.py`) called the Anthropic API **synchronously** inside the PTY reader thread. This blocked all terminal output for 1-3 seconds per API call. During that time:
- Claude Code's Ink rendering froze visibly
- Terminal buffer accumulated unread bytes
- The session felt broken

The PTY reader thread cannot block. It must continuously read and pass through terminal output to maintain the illusion of a normal terminal session.

## 2. Decision

**We will spawn a daemon worker thread for each sentinel check, allowing the PTY reader to return immediately.**

The worker thread handles the Haiku API call, interprets the verdict, and either sends a CR (ALLOW/ERROR) or withholds it (BLOCK). The `in_approval` flag serializes these operations — only one sentinel check can be in flight at a time.

## 3. Alternatives Considered

### Option A: Worker Thread — SELECTED

**Description:** `do_approval()` spawns a `threading.Thread(daemon=True)` that calls `SentinelGate.check()`. The PTY reader returns immediately. The worker thread sends CR to PTY on ALLOW, withholds it on BLOCK.

**Pros:**
- PTY reader never blocks — terminal output flows normally
- `pty.write()` is already thread-safe (confirmed: stdin reader thread writes from t1 while PTY reader runs on t2)
- Claude Code is paused at the permission prompt, so no output is missed during the check
- Simple to implement — 15 lines of code

**Cons:**
- No bound on concurrent threads (mitigated by `in_approval` flag)
- Thread lifecycle not explicitly managed
- Potential race between `finally` block clearing `in_approval` and next pattern match

### Option B: Inline API with Short Timeout — Rejected

**Description:** Call `SentinelGate.check()` synchronously in the PTY reader with a 500ms timeout.

**Pros:**
- No threading complexity
- Sequential, easy to reason about

**Cons:**
- **Still blocks PTY reader for 500ms minimum** — visible lag on every permission prompt
- 500ms is often not enough for Haiku to respond (p50 latency ~800ms)
- Timeout means most sentinel checks would be inconclusive
- This is exactly what `unleashed-guarded.py` tried (and failed)

### Option C: Async with Event Loop — Rejected

**Description:** Run an asyncio event loop in the PTY reader thread, use `asyncio.create_task()` for API calls.

**Pros:**
- No thread management
- Natural for API calls

**Cons:**
- PTY reader is a synchronous thread — would need to wrap entire reader in async
- Python's GIL means async doesn't actually parallelize CPU work
- `winpty` is synchronous — mixing async and sync is a recipe for deadlocks
- Architectural complexity disproportionate to the problem

## 4. Rationale

Option A was selected because the problem is specifically about **not blocking the PTY reader thread**. A worker thread is the simplest primitive that achieves this. The alternatives either still block (Option B) or add unjustified complexity (Option C).

The key insight came from analyzing the failed `unleashed-guarded.py`: the API call doesn't need to happen in the reader thread. It just needs to happen before the CR is sent. A separate thread that "owns" the approval decision achieves this cleanly.

The `in_approval` flag is the critical synchronization primitive. It's set by the PTY reader before spawning the thread, and cleared by the worker thread in `finally`. During this window, the PTY reader skips any additional permission pattern matches — preventing double-approval and ensuring at most one sentinel check is in flight.

## 5. Security Risk Analysis

| Risk | Impact | Likelihood | Severity | Mitigation |
|------|--------|------------|----------|------------|
| Thread race: `in_approval` cleared before worker finishes | Med | Low | 3 | `finally` block guarantees clearing; no code path bypasses it |
| Unbounded thread creation | Low | Low | 2 | `in_approval` serializes; only one thread active at a time |
| `in_approval` stuck True (thread dies) | High | Low | 6 | Issue #41: Add watchdog timeout (15s) |
| Worker thread sends CR after PTY dies | Low | Low | 1 | `pty.write()` raises; caught in try/except |
| API key in exception propagation | High | Med | 6 | Issue #38: Sanitize exception before returning |

**Residual Risk:** The `in_approval` flag is a boolean with no timeout. If the worker thread hangs (e.g., PTY write blocks), all subsequent approvals are suppressed. Issue #41 tracks adding a watchdog.

## 6. Consequences

### Positive
- PTY reader never blocks — terminal rendering is unaffected by sentinel
- Session feels identical to non-sentinel mode (except for the 1-3s approval delay)
- Failed API calls don't freeze the session (fail-open in worker thread)
- Simple implementation: the worker thread pattern is 15 lines of code

### Negative
- One additional thread per sentinel check (mitigated by serialization)
- No explicit thread lifecycle management (issue #40)
- Debugging is harder — sentinel verdict may arrive asynchronously relative to PTY output

### Neutral
- Sentinel latency (1-3s) is visible to the user as a pause before permission approval
- This is inherent to the API call, not the threading model

## 7. Implementation

- **Related Issues:** #12 (sentinel integration), #40 (thread management), #41 (approval timeout)
- **Files:** `src/unleashed-c-21.py` (`do_approval()`, `_sentinel_check()`), `src/sentinel_gate.py`
- **Status:** Implemented (2026-02-14)

```
sequenceDiagram
    participant PTY as PTY Reader Thread
    participant WRK as Worker Thread
    participant API as Haiku API
    participant TERM as PTY (terminal)

    PTY->>PTY: Detect permission pattern
    PTY->>PTY: Set in_approval = True
    PTY->>WRK: Spawn daemon thread
    PTY->>PTY: Return (resume reading)

    WRK->>API: SentinelGate.check()
    API-->>WRK: ALLOW / BLOCK / ERROR

    alt ALLOW or ERROR (fail-open)
        WRK->>TERM: pty.write('\r')
    else BLOCK
        WRK->>WRK: Print warning to stderr
    end

    WRK->>WRK: Set in_approval = False
```

## 8. References

- `archive/unleashed-guarded.py` — The failed synchronous integration
- Issue #12 — Original sentinel integration request
- [Python threading docs](https://docs.python.org/3/library/threading.html) — Daemon thread behavior

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-02-14 | Claude Opus 4.6 | Initial draft |
