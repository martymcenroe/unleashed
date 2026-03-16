# ADR-006: Drop Poetry from Runtime Invocation

**Status:** Accepted
**Date:** 2026-03-16
**Deciders:** mcwiz, Claude

## Context

Unleashed has been invoked via `poetry run python src/unleashed-c-XX.py` since v00010. Poetry was adopted to manage the virtualenv and ensure `pywinpty` was available without polluting the system Python.

On 2026-03-16, a memory-constrained Windows environment (large PDF opened during 4 active unleashed sessions) caused `poetry run` to hang indefinitely. All existing sessions continued running, but no new sessions could start. The hang persisted after freeing memory — poetry's internal resolver or lock mechanism did not recover. `python` (system) and the venv python both worked fine. Only `poetry run` was broken.

## Decision

**Invoke unleashed via bare `python` instead of `poetry run python`.**

### Rationale

1. **Unleashed's runtime dependency is exactly one package: `pywinpty`.** Every other import is stdlib. The `anthropic` and `wordninja` packages declared in `pyproject.toml` are for sentinel (on-ice) and transcript post-processing — neither is imported by the main unleashed scripts.

2. **`pywinpty` is already installed system-wide.** Verified: `python -c "import winpty"` succeeds without any virtualenv. The poetry virtualenv provides no isolation benefit when the only dependency is already globally available.

3. **Poetry adds ~2-3 seconds of startup latency** (resolver, venv activation) for zero runtime benefit. At 15+ session starts per day, this is measurable friction.

4. **Poetry is a single point of failure.** When poetry hangs, all new sessions are blocked — including production. The underlying python and packages work fine. Poetry's role is dependency installation, not runtime invocation.

5. **Multiple concurrent sessions.** The user routinely runs 8-16 simultaneous unleashed sessions. Poetry was not designed for this concurrency level on Windows and has exhibited lock contention under load.

### What Changes

**Before:**
```bash
_unleashed_run() {
  local script="$1"; shift
  local project_path="$(cygpath -w "$(pwd)")"
  (cd /c/Users/mcwiz/Projects/unleashed && \
   poetry run python "$script" --cwd "$project_path" "$@")
}
```

**After:**
```bash
unleashed-alpha() {
  local project_path="$(cygpath -w "$(pwd)")"
  python /c/Users/mcwiz/Projects/unleashed/src/unleashed-c-27.py \
    --cwd "$project_path" --sentinel-shadow --mirror --friction "$@"
}
```

Key differences:
- No `cd` to the unleashed repo (python uses absolute path)
- No `poetry run` wrapper
- Direct `python` invocation
- `PYTHONPATH` not needed — `transcript_filters.py` is imported via relative path from `src/`

### What Stays

- **Poetry for installation:** `poetry add` / `poetry install` remain the way to manage dependencies. This ADR only drops poetry from the **runtime invocation** path.
- **`pyproject.toml`:** Unchanged. Still defines the project and its dependencies.
- **Virtualenv:** Still exists. Still useful for development (`pytest`, linting). Just not needed for running unleashed.

## Consequences

- **Positive:** Eliminates the poetry hang failure mode. Faster startup. Simpler bash functions.
- **Positive:** Removes the `cd` to the unleashed repo, which was required for poetry to find `pyproject.toml`.
- **Negative:** If `pywinpty` is ever uninstalled from system python, unleashed silently fails at import time. Mitigated by the existing `try/import winpty/except` block that prints a clear error.
- **Negative:** If sentinel goes live and needs `anthropic`, the system python would need that package installed. Cross that bridge when sentinel activates.
- **Risk:** `transcript_filters.py` is imported as a relative module from `src/`. If python's CWD is not the unleashed repo, the import fails. Mitigated: the scripts already handle this via `sys.path` manipulation, or we add the src dir to PYTHONPATH in the bash function.

## Rollout

1. **Alpha (c-27):** Updated to bare python invocation — 2026-03-16
2. **Production (c-26):** Update `_unleashed_run()` after alpha validates — TBD
3. **Gemini (g-19):** Same change — TBD
