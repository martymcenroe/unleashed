#!/usr/bin/env python3
"""
Mirror Pipeline Test Harness — replay .raw transcripts through the mirror pipeline.

Reads a .raw PTY transcript file, processes it through the exact same pipeline
as the live session mirror in unleashed-c-21.py, and writes clean output.

This enables deterministic, repeatable testing: change mirror_strip_ansi() or
transcript_filters.py, re-run on the same .raw file, diff the output.

Usage:
    poetry run python src/test_mirror.py <raw_file> [output_file]
    poetry run python src/test_mirror.py <raw_file> --stats    # show garbage stats
    poetry run python src/test_mirror.py <raw_file> --debug    # show what gets filtered

If output_file is omitted, writes to stdout.
"""
import sys
import os
import re
import importlib.util
from collections import deque
from pathlib import Path

# Import is_garbage from transcript_filters (clean import)
from transcript_filters import is_garbage

# Import mirror_strip_ansi from the latest version (hyphenated filename)
# Update this when promoting a new version.
_UNLEASHED_SRC = "unleashed-c-23.py"
_spec = importlib.util.spec_from_file_location(
    "unleashed_mod",
    os.path.join(os.path.dirname(__file__), _UNLEASHED_SRC)
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

mirror_strip_ansi = _mod.mirror_strip_ansi
ORPHAN_CSI_RE = _mod.ORPHAN_CSI_RE
ORPHAN_OSC_RE = _mod.ORPHAN_OSC_RE
SPINNER_PREFIX_RE = _mod.SPINNER_PREFIX_RE
SPINNER_FRAG_RE = _mod.SPINNER_FRAG_RE
THINKING_RE = _mod.THINKING_RE
BARE_CSI_PARAM_RE = _mod.BARE_CSI_PARAM_RE
PURE_DIGITS_RE = _mod.PURE_DIGITS_RE
MIRROR_FLUSH_INTERVAL = _mod.MIRROR_FLUSH_INTERVAL

# Import the sparse fragment detector
_is_sparse_fragment = _mod.Unleashed._is_sparse_fragment

# Chunk size matching pty.read() in the live system
PTY_READ_SIZE = 8192

# Dedup window matching the live system
DEDUP_WINDOW = 32


def replay_mirror(raw_path: str, *, debug: bool = False, stats: bool = False) -> list[str]:
    """Replay a .raw transcript through the mirror pipeline.

    Returns list of clean output lines.
    """
    with open(raw_path, "rb") as f:
        raw_data = f.read()

    # Simulate the rate-limited buffer by processing in chunks.
    # In the live system, chunks accumulate for 200ms before flushing.
    # Here we simulate by grouping PTY_READ_SIZE chunks into flush batches.
    # A 200ms batch at typical throughput is roughly 4-8 chunks.
    CHUNKS_PER_FLUSH = 6  # ~200ms worth at typical throughput

    output_lines = []
    recent = deque(maxlen=DEDUP_WINDOW)
    pending_prompt = None  # v23: typeahead buffer

    # Stats tracking
    total_lines = 0
    garbage_lines = 0
    orphan_lines = 0
    dedup_lines = 0
    empty_lines = 0
    prompt_collapsed = 0
    debug_log = []

    def flush_prompt():
        """Flush pending prompt to output."""
        nonlocal pending_prompt, prompt_collapsed
        if pending_prompt is None:
            return
        normalized = SPINNER_PREFIX_RE.sub("", pending_prompt).strip()
        if normalized not in recent:
            recent.append(normalized)
            output_lines.append(pending_prompt)
        pending_prompt = None

    # Process in chunks, accumulating into flush batches
    offset = 0
    flush_buffer = b""

    while offset < len(raw_data):
        # Accumulate CHUNKS_PER_FLUSH chunks into one flush buffer
        for _ in range(CHUNKS_PER_FLUSH):
            end = min(offset + PTY_READ_SIZE, len(raw_data))
            flush_buffer += raw_data[offset:end]
            offset = end
            if offset >= len(raw_data):
                break

        if not flush_buffer:
            break

        # Stage 1: ANSI strip via cursor-tracking parser
        clean = mirror_strip_ansi(flush_buffer)
        text = clean.decode("utf-8", errors="replace")

        # Stage 2+: Line-by-line processing
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                empty_lines += 1
                continue

            total_lines += 1

            # Stage 2: Strip orphaned escape sequence payloads
            before_orphan = stripped
            stripped = ORPHAN_CSI_RE.sub("", stripped)
            stripped = ORPHAN_OSC_RE.sub("", stripped)
            stripped = stripped.strip()
            if not stripped:
                orphan_lines += 1
                if debug:
                    debug_log.append(f"[ORPHAN] {before_orphan[:80]}")
                continue

            # Stage 3: Prompt detection FIRST — ❯ lines are user input,
            # not garbage. Route straight to typeahead buffer.
            if stripped.startswith('\u276f'):
                if pending_prompt is not None:
                    prev = pending_prompt
                    if stripped.startswith(prev) or prev.startswith(stripped):
                        pending_prompt = stripped if len(stripped) >= len(prev) else prev
                        prompt_collapsed += 1
                        if debug:
                            debug_log.append(f"[TYPEAHEAD] {stripped[:80]}")
                        continue
                    else:
                        flush_prompt()
                pending_prompt = stripped
                continue

            # Stage 4a: Short-line filter (TUI rendering fragments)
            if len(stripped) < 3:
                garbage_lines += 1
                if debug:
                    debug_log.append(f"[SHORT] {stripped[:80]}")
                continue

            # Stage 4b: Spinner fragment filter
            if SPINNER_FRAG_RE.match(stripped):
                garbage_lines += 1
                if debug:
                    debug_log.append(f"[SPINNER] {stripped[:80]}")
                continue

            # Stage 4c: v23 sparse fragment filter
            if _is_sparse_fragment(stripped):
                garbage_lines += 1
                if debug:
                    debug_log.append(f"[SPARSE] {stripped[:80]}")
                continue

            # Stage 4d: v23 thinking/ANSI/digit noise
            if THINKING_RE.match(stripped) or BARE_CSI_PARAM_RE.match(stripped) or PURE_DIGITS_RE.match(stripped):
                garbage_lines += 1
                if debug:
                    debug_log.append(f"[NOISE] {stripped[:80]}")
                continue

            # Stage 4e: v23 short ellipsis fragments
            if len(stripped) <= 8 and stripped.endswith('\u2026'):
                garbage_lines += 1
                if debug:
                    debug_log.append(f"[ELLIPSIS] {stripped[:80]}")
                continue

            # Stage 4f: Garbage filter (95 patterns)
            if is_garbage(stripped):
                garbage_lines += 1
                if debug:
                    debug_log.append(f"[GARBAGE] {stripped[:80]}")
                continue

            # Stage 5: Dedup BEFORE flushing prompt.
            # TUI chrome repeats between keystrokes; flushing prompt
            # on duplicates would break typeahead collapsing.
            normalized = SPINNER_PREFIX_RE.sub("", stripped).strip()
            if normalized in recent:
                dedup_lines += 1
                if debug:
                    debug_log.append(f"[DEDUP] {stripped[:80]}")
                continue

            # New content — flush any pending prompt first (preserves order)
            flush_prompt()

            recent.append(normalized)
            output_lines.append(stripped)

        flush_buffer = b""

    # Final flush of any pending prompt
    flush_prompt()

    if stats or debug:
        summary = [
            "",
            "=" * 60,
            "  Mirror Pipeline Statistics",
            "=" * 60,
            f"  Raw file size:     {len(raw_data):,} bytes",
            f"  Total lines:       {total_lines:,}",
            f"  Empty lines:       {empty_lines:,}",
            f"  Garbage filtered:  {garbage_lines:,} ({garbage_lines*100//max(total_lines,1)}%)",
            f"  Orphan stripped:   {orphan_lines:,}",
            f"  Typeahead collapsed: {prompt_collapsed:,}",
            f"  Deduped:           {dedup_lines:,}",
            f"  Output lines:      {len(output_lines):,} ({len(output_lines)*100//max(total_lines,1)}% pass rate)",
            "=" * 60,
        ]
        for s in summary:
            print(s, file=sys.stderr)

    if debug:
        print("\n--- Debug: First 100 filtered lines ---", file=sys.stderr)
        for entry in debug_log[:100]:
            print(f"  {entry}", file=sys.stderr)

    return output_lines


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    raw_path = sys.argv[1]
    if not os.path.exists(raw_path):
        print(f"Error: {raw_path} not found", file=sys.stderr)
        sys.exit(1)

    debug = "--debug" in sys.argv
    stats_only = "--stats" in sys.argv

    # Find output path (first arg that isn't a flag)
    output_path = None
    for arg in sys.argv[2:]:
        if not arg.startswith("--"):
            output_path = arg
            break

    lines = replay_mirror(raw_path, debug=debug, stats=(stats_only or debug))

    if stats_only:
        # Just show stats, don't write output
        print(f"\nSample output (first 20 lines):", file=sys.stderr)
        for line in lines[:20]:
            print(f"  {line}", file=sys.stderr)
        sys.exit(0)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        print(f"Wrote {len(lines)} lines to {output_path}", file=sys.stderr)
    else:
        for line in lines:
            print(line)


if __name__ == "__main__":
    main()
