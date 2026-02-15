"""
transcript_filters.py — Shared garbage pattern matching for PTY transcripts.

Single source of truth for TUI garbage detection. Used by:
- clean_transcript.py (post-session cleaning)
- unleashed-c-21+.py (live session mirror filtering)

95 compiled regex patterns covering spinners, timing fragments, permission UI,
status bars, agent trees, checklist repaints, CLI help, and garbled TUI artifacts.
"""
import re


# --- Compiled patterns (order matters — most frequent first) ---
GARBAGE_PATTERNS = [
    # 1. GENERIC spinner pattern: spinner char(s) + Capitalized verb + optional words + …
    #    Claude uses dozens of creative words: Synthesizing, Prestidigitating, Hyperspacing,
    #    Meandering, Pouncing, Slithering, Churning, Baking, Sautéing, etc.
    #    Also: Razzle-dazzling, Beboppin', etc. (hyphenated/apostrophed)
    #    Allow words between the verb and the … (e.g., "Creating name extraction pipeline…")
    re.compile(
        r'^[\s*·✶✻✽✢●◼❯⏵▐▝▘☵⎿]*\s*'
        r"[A-Z][a-zéèêë'-]+(?:ing|ed|ion|ting|in')\b.*…"
    ),
    # Catch word-merged spinner + number: "Razzle-dazzling… 3", "Beboppin'… 40"
    re.compile(
        r'^[\s*·✶✻✽✢●◼❯⏵▐▝▘☵⎿]*\s*'
        r"[A-Z][a-zéèêë'-]+(?:ing|ed|ion|ting|in')…?\s*\d*\s*$"
    ),
    # Also catch word-merged variants: "Auto-updating", "Pasting text"
    # NOTE: Compacting/compacted lines are PRESERVED — see COMPACTION_KEEP below
    re.compile(
        r'^[\s*·✶✻✽✢●◼❯⏵▐▝▘☵⎿]*\s*'
        r'(?:Auto-updating|Pasting\s*text)'
    ),

    # 2. Character-by-character thinking fragments
    #    "p i thinking", "✶ 3 4 thinking", "spa thinking"
    #    Also: "g (thinking)", "✢ n (thinking)", "i …(thinking)"
    re.compile(r'^[\s*·✶✻✽✢]*\s*.{0,10}\s+\(?thinking\)?\s*\)?$'),
    re.compile(r'.{0,6}\(thinking\)\s*$'),

    # 3. Status bar timestamps — with or without spaces
    #    "[02-13 18:31:29]  Context left until auto-compact: 0%"
    #    "[02-1318:31:29] ... Contextleftuntilauto-compact:0%"
    re.compile(r'^\[?\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2}\]?\s+'),

    # 4. Auto-update failed line — with or without spaces
    re.compile(r'✗\s*Auto-?update\s*failed'),
    re.compile(r'Auto-?update\s*failed\s*·\s*Try'),

    # 5. Accept edits chrome — with or without spaces
    re.compile(r'^⏵+\s*accept\s*edits?\s*on', re.IGNORECASE),
    re.compile(r'⏵+\s*accepteditson', re.IGNORECASE),

    # 6. Permission prompt UI lines — with or without spaces
    re.compile(r'^>>>\s*\[PERMISSION\]'),
    re.compile(r'Esc\s*to\s*cancel\s*·'),
    re.compile(r'Esctocancel'),
    re.compile(r'^\s*❯\s*\d+\.\s*(?:Yes|No)'),
    re.compile(r'^\s*\d+\.\s*(?:Yes|No)\s*$'),
    re.compile(r'Do\s*you\s*want\s*to\s*proceed\s*\?'),
    re.compile(r'Doyouwanttoproceed'),

    # 7. Claude Code ASCII logo
    re.compile(r'^[\s▐▝▜▛█▘]*[▐▝▜▛█▘]{3,}'),

    # 8. Ctrl hints
    re.compile(r'^\s*ctrl\+[a-z]\s+to\s+\w+', re.IGNORECASE),

    # 9. Token/timing-only fragments — very broad: short lines with tokens/↑/↓
    #    "· 3 0s · ↑ 98 tokens)", "1.0k tokens · thought for 13s)"
    #    "✶ 5 0s · ↑ 98 tokens · thought for 5s)", "4 0s · ↑ 98 tokens)"
    #    "1  · 5.6k tokens", "2 s · 5.5k tokens"
    re.compile(r'^\s*[\s·✶✻✽✢*]*\s*\d*\.?\d*k?\s*(?:tokens|↑|↓)'),
    re.compile(r'^\s*[\d\s·↑↓]+(?:tokens|thinking)\s*\)?$'),
    re.compile(r'^\s*[\s·✶✻✽✢*]*\d+\s+0?s\s*·\s*[↑↓]'),
    re.compile(r'^\s*\d+\s+·\s+\d+\.?\d*k?\s+tokens'),

    # 10. Bare timing fragments
    #     "· 3 0s · ↑ 98 tokens · thought for 5s)", "(2s · timeout 15s)"
    #     "(thought for 15s)", "thought for 5s)"
    re.compile(r'^\s*[\(·]\s*\d+s\s*·\s*(?:timeout|↑|↓)'),
    re.compile(r'^[\s*·✶✻✽✢]*\s*\(?\s*thought for \d+s\)?\s*$'),

    # 11. Press up / image tags / Wait — with or without spaces
    re.compile(r'Press\s*up\s*to\s*edit\s*queued'),
    re.compile(r'Pressuptoeditqueued'),
    re.compile(r'^\s*\[Image #\d+\]\s*(?:\(↑ to select\))?\s*$'),
    re.compile(r'^\s*⎿\s*\[Image #\d+\]'),
    re.compile(r'^Wait$'),

    # 12. Path-only status lines (bare path with optional git branch)
    #     May have trailing nerd font glyphs (\uf113 git branch icon etc.)
    re.compile(r'^(?:s/|/c/Users/mcwiz/Projects/)\S+\s*(?:\(main\)\s*)?.{0,5}$'),
    re.compile(r'^\s*~\\Projects\\\S+\s*.{0,5}$'),
    re.compile(r'^C:\\Users\\mcwiz\\Projects\\\S+\s*.{0,5}$'),

    # 13. Running N agents lines
    re.compile(r'^Running\s+\d+\s+Bash\s+ag[ne]+ts'),

    # 14. Wrangler boilerplate (repeated on every D1 command)
    re.compile(r'^⛅️\s*wrangler\s*\d'),
    re.compile(r'^🌀\s*(?:Executing on|To execute on)'),
    re.compile(r'^Resource\s*location:\s*remote$'),

    # 15. Orphan spinner/timing lines: just a number or spinner + number
    re.compile(r'^[\s*·✶✻✽✢]*\s*\d{1,3}\s*$'),

    # 16. Bare "Bash command" label
    re.compile(r'^Bash\s+command\s*$'),

    # 17. yperspacing / partial spinner word (truncated repaints)
    re.compile(r'^[a-z]perspacing'),

    # 18. Token count fragments with thought
    re.compile(r'^\s*[\s·✶✻✽✢*]*\s*\d+\.?\d*k?\s*tokens\s*·\s*thought'),

    # 19. "↑ to select" standalone or "(↑ to select)"
    re.compile(r'^\s*\(?↑\s*to\s*select\)?\s*$'),

    # 20. Lines that are ONLY timing like "(2s · timeout 30s)" or "(timeout 30s)"
    #     Also: "(2s · timeot30s)" (word-merged)
    re.compile(r'^\s*⎿?\s*\((?:timeout\s*\d+\w?s?|\d+s\s*·\s*time\s*o[tu]+\s*\d+\w?s?)\)\s*$'),

    # 21. Plan mode chrome
    re.compile(r'^⏸\s*plan\s*mode\s*on'),
    re.compile(r'^⏸planmodeon'),
    re.compile(r'^●?\s*Entered plan mode'),
    re.compile(r'^Claude is now exploring and designing'),

    # 22. "Worked for Nm Ns" summary
    re.compile(r'^[\s*·✶✻✽✢]*\s*Worked for \d+'),

    # 23. Agent tree lines (├─, │ ⎿, └─) with initializing/tool counts
    # Excludes table content rows (│ text │ text │) which have 2+ │ with text between
    re.compile(r'^[├│└─]+\s(?!.*\w+.*│)'),

    # 24. Done summary: "⎿ Done(8tooluses·54.8ktokens·22s)"
    re.compile(r'^⎿\s*Done\s*\('),

    # 25. Garbled repaints — very short lines with truncated tool names
    #     "S rch(pattern:", "R d(migraios\", "6rtry-count.sql)"
    #     Lines under 30 chars with garbled words (consonant clusters + truncation)
    re.compile(r'^[A-Z]\s+[a-z]{1,3}\('),  # "S rch(", "R d("
    re.compile(r'^\d+[a-z].*\.sql\)?\s*$'),  # "6rtry-count.sql)"
    re.compile(r'^\d+\s+more\s+to[lo]*\s+uses'),  # "11 more tol uses"

    # 26. Duplicate content markers — bare tool descriptions
    #     when the TUI repaints the same Bash() line, we get it 2-3 times
    #     (we'll handle dedup separately if needed)

    # 27. Partial/bare path fragments
    re.compile(r'^s/\w+\s*\(main\)\s*$'),  # "s/Hermes (main)"

    # 28. Generic spinner+timing: any line that's spinner char(s) + activity + timing
    #     "✽ Creating migration 0008… (35s · ↓ 824 tokens · thinking)"
    #     "· Creating migration 0008… (39s · ↓ 975 tokens · thinking)"
    re.compile(r'^[\s*·✶✻✽✢]+.*\(\d+[ms]?\s*\d*s?\s*·\s*[↑↓]'),

    # 29. Bare timing with "tokens" anywhere in short line
    #     "1m 0s· ↓ 3.7k tokens· thinking)"
    re.compile(r'^\s*\d+m?\s*\d*s?\s*·?\s*[↑↓]\s*\d'),

    # 30. Garbled checklist repaint lines
    #     "Remov ai_model gate on emoj ratings"
    #     "la _ _at to reinitConversaton n poke.js"
    #     "Upd schema.sql withlast_init_at"
    #     Short lines with garbled words — detect by known checklist patterns
    re.compile(r'^Remov\s+ai_model'),
    re.compile(r'^la\s+_\s+_at'),
    re.compile(r'^Upd\s+schema'),

    # 31. Checklist status lines (◼/◻) — these are TUI progress repaints
    #     Keep the FIRST occurrence, remove subsequent identical ones
    #     For now, just mark duplicated checklist items
    re.compile(r'^◻\s*(?:Remove|Add|Update|Create)'),
    re.compile(r'^◼\s*(?:Remove|Add|Update|Create)'),

    # 32. Standalone "(ctrl+o to expand)" without leading content
    re.compile(r'^\s*\(ctrl\+o\s*to\s*(?:expand|see)'),

    # 33. Lines that are just "↑" or "↓" with whitespace
    re.compile(r'^\s*[↑↓]\s*$'),

    # 34. "… +N lines" collapsed section markers — keep these, they provide context
    #     (NOT matched — intentionally excluded)

    # 35. Bare status line fragments
    re.compile(r'^\s*\+\d+\s+more\s+tool\s+uses'),  # "+2 more tool uses (ctrl+o..."

    # 36. Permission auto-approve artifacts (word-merged)
    #     "2.Yes,anddon'taskagainforpython3commandsin..."
    re.compile(r'^\s*\d+\.\s*Yes\s*,?\s*and\s*don'),
    re.compile(r"don'taskagain"),

    # 37. Bare spinner characters (just ✶ or ✻ or ● alone on a line)
    re.compile(r'^[\s*·✶✻✽✢●]+\s*$'),

    # 38. "Waiting…" with garbled suffix
    re.compile(r'^Waiting…'),

    # 39. Activity summary: "Baked for", "Churned for", "Sautéed for", etc
    #     Match any word ending in -ed/-éed + "for" + number
    re.compile(r'^[\s*·✶✻✽✢]*\s*[A-Z][a-zéèêë]+(?:ed|éed)\s*for\s*\d'),
    re.compile(r'^[\s*·✶✻✽✢]*\s*[A-Z][a-zéèêë]+(?:edfor|éedfor)\d'),

    # 40. ANSI color code fragment at start of line
    re.compile(r'^\s*;?\d+m\s'),
    re.compile(r'^255;255;255m\s'),

    # 41. "Reading N file(s)…" — with or without spaces, with ctrl+o
    re.compile(r'^Reading\s*\d+\s*file'),

    # 42. Short garbled fragments (under 30 chars, no sentence structure)
    #     "2 s… (ctrl+o to expand)", "●  4 files (ctrl+o to expand)"
    re.compile(r'^\s*[\s●⎿]*\s*\d+\s+(?:s…|files?\s*\(ctrl)'),

    # 43. Garbled truncated checklist repaints (no ✔/◻ prefix)
    #     "name-extractor.js + fix mail-parser.js" (without ✔ prefix = repaint)
    #     "Create ecruiters mgration + module + schema update" (garbled)
    #     These are hard to catch generically. Catch specific short patterns.
    re.compile(r'^Create\s+[a-z]*cruiters?\s+m[a-z]*gration'),
    re.compile(r'^Create\s+firs-'),
    re.compile(r'^name-extractor\.js\s*\+'),

    # 44. "⎿  daily follow-up cr system" — garbled/truncated tool result
    re.compile(r'^⎿\s+\w+\s+follow-up\s+cr\b'),

    # 45. Timing with garbled text: "✶ athi 5 0s · ↑ 12.8k tokens · thinking)"
    re.compile(r'^[\s*·✶✻✽✢]*\s*\w{0,5}\s+\d+\s*0?s\s*·\s*[↑↓]'),

    # 46. Lines starting with "Hatch" (truncated spinner word)
    re.compile(r'^Hatch\s+…'),

    # 47. "2 s… (ctrl+o..." and similar ultra-short fragment lines
    re.compile(r'^\d+\s+s?…'),

    # 48. Ultra-short garbled fragments (under 20 chars, not a real sentence)
    #     "g in the world.", "deploy and verify", fragments from repaints
    #     We can't be too aggressive here — short user messages are valid.
    #     Only catch lines that look like mid-word fragments.
    re.compile(r'^[a-z]\s+\w{1,3}\s+the\s+\w+\.\s*$'),  # "g in the world."

    # 49. Checklist items with › blocked by (progress tracking)
    re.compile(r'^◻?\s*Wire\s.*›\s*blocked\s*by'),

    # 50. ⎿ lines with partial checklist content (garbled repaints)
    re.compile(r'^⎿\s+(?:◼|◻)?\s*(?:Create|Wire|Add|Remove|Update)\s+\w'),

    # 51. Repeated ◼/◻/✔ checklist lines (TUI progress bar repaints)
    #     These appear dozens of times as the checklist refreshes
    re.compile(r'^[◼◻✔]\s+(?:Deploy|Wire|Create|Add|Remove|Update|Run|Verify)'),
    re.compile(r'^⎿\s+[◼◻✔]\s+'),

    # 52. "ctrl+o to expand/see all" markers — terminal UI, not content
    #     Also word-merged: "ctrl+oto expand", "ctrl+otoseeall"
    re.compile(r'ctrl\+o\s*to\s*(?:expand|see)'),

    # 53. Very short garbled fragments (1-3 chars + whitespace)
    #     "n c 2", "c r", "r n", "✢ g ur", "en c", "t"
    re.compile(r'^[\s*·✶✻✽✢]*\s*[a-z]{1,2}\s+[a-z]{1,3}\s*\d*\s*$'),

    # 54. "Running in the background" UI status
    re.compile(r'Running in the background'),

    # 55. "shift+tab to cyc…" and similar truncated UI hints
    re.compile(r'shift\+tab\s+to\s+cyc'),

    # 56. "Tab to amend" standalone (may have leading · or spinner chars)
    re.compile(r'^[\s*·✶✻✽✢]*\s*Tab\s+to\s+amend'),

    # 57. Word-merged task descriptions without bullet (repaint artifacts)
    #     "DumpALLusertextmessagesfromtranscript"
    #     "Checkallcontenttypesinusermessages"
    #     Detect: 20+ chars, ALL lowercase/mixed, NO spaces
    re.compile(r'^[A-Z][a-z]+[A-Z][a-z]+[A-Z][a-z]+\w{10,}$'),

    # 58. Standalone "thought for Ns)" or "ought for Ns)" fragments
    re.compile(r'^\s*[\s*·✶✻✽✢]*\s*\w{0,6}ought\s+for\s+\d+s?\)'),

    # 59. Garbled spinner fragments with timing: "✽D l 30s · ↑ 686tokens"
    re.compile(r'^[\s*·✶✻✽✢]+[A-Z]?\s*[a-z]?\s+\d+s\s*·\s*[↑↓]'),

    # 60. Standalone "running N file…" (lowercase)
    re.compile(r'^running\s+\d+\s+file'),

    # 61. Bare timing: "2 s · 5.5k tokens", "7 s · 12k tokens"
    #     (number + space + s · tokens — space split the "2s" into "2 s")
    re.compile(r'^\s*\d+\s+s\s*·\s*\d+\.?\d*k?\s*tokens'),

    # 62. Bare "N thought for Ns)" fragments
    re.compile(r'^\s*\d+\s+thought\s+for\s+\d+'),

    # 63. Garbled spinner with … in middle: "✶ i … 8 0s · ↓ 4.6k tokens · thinking)"
    re.compile(r'^[\s*·✶✻✽✢]+\s*\w{0,5}\s*…\s*\d+'),

    # 64. Truncated "shift+tab" hint: "hift+tab to cyc…" (missing leading s)
    re.compile(r'ift\+tab\s+to\s+cyc'),

    # 65. Bare timing without opening paren: "10s · timeout 1m 30s)"
    re.compile(r'^\s*\d+s?\s*·\s*timeout\s+\d+'),

    # 66. Truncated "Running in the background": "Runn  in the background"
    re.compile(r'^Runn\w*\s+in\s+the\s+background'),

    # 67. Timeout with Nm Ns format: "⎿ (timeout 1m 30s)"
    re.compile(r'^\s*⎿?\s*\(timeout\s+\d+m?\s*\d*s?\)'),

    # 68. "Task Output <hex>" — background task reference
    re.compile(r'^Task\s+Output\s+[a-f0-9]+'),

    # 69. "Waiting for task" with or without spaces
    re.compile(r'Waiting\s*for\s*task'),
    re.compile(r'Waitingfortask'),

    # 70. Ultra-garbled spinner fragments: "spa ng… ↓ 3  · thinking)"
    #     Short prefix + … + arrow + timing
    re.compile(r'^\w{1,5}\s+\w{0,3}…\s*[↑↓]'),

    # 71. CLI help listings — lines ending in (user), (usr), (sr)
    re.compile(r'\((?:user|usr|sr)\)\s*$'),

    # 72. CLI help — slash command format: "/command  description"
    re.compile(r'^/\w[\w-]+\s{2,}\S'),

    # 73. CLI help — garbled command descriptions without slash
    #     "unleashed-version Unleashd Version Check(usr)"
    #     "upgrde Upgrade t Max for hgher rate limits and more Opus"
    #     "usage Showplanusagelimits"
    #     "sage             Show planusage limits"
    #     "ext -usage Configure extra usage"
    re.compile(r'^(?:unleashed-version|upgrde|usage|sage|ext\s+-usage)\s'),

    # 74. Plan mode chrome: "⎿ /plan to preview", "Ready to code?"
    re.compile(r'^⎿\s*/plan\s+to\s+preview'),
    re.compile(r'^Ready\s+to\s+code\s*\?'),

    # 75. Garbled spinner without leading spinner char but with … + (timing · ↓)
    #     "Dep y  current changes… (57s · ↓886 tokens)"
    re.compile(r'^[A-Z]\w*\s+\w*\s+\w*\s*…\s*\(\d+[ms]?\s*\d*s?\s*·\s*[↑↓]'),

    # 76. Lone bullet point ● on its own line
    re.compile(r'^\s*●\s*$'),

    # 77. "+N more lines" (tool output collapsed section with timing)
    re.compile(r'^\s*\+\d+\s+more\s+lines?\s'),

    # 78. Task list summary: "N tasks (0 done, 1 in progress, 0 open)"
    re.compile(r'^\d+\s+tasks?\s*\(\d+\s+done'),

    # 79. Garbled "ought for" with … prefix: "i … ought for 11s)"
    re.compile(r'^[\s*·✶✻✽✢]*\s*\w{0,5}\s*…\s*ought\s+for\s+\d+'),

    # 80. Ultra-garbled thought fragments: "r n 2 6 thought for 2s)"
    #     Short chars + spaces + number + "thought for"
    re.compile(r'^[a-z\s]{1,10}\d+\s+\d*\s*thought\s+for\s+\d+'),

    # 81. Playwright "No open tabs" message (word-merged)
    re.compile(r'No\s*open\s*tabs\s*\.?\s*Navigate\s*to', re.IGNORECASE),
    re.compile(r'Noopentabs'),

    # 82. Garbled timing with single leading char: "T 1m 0s· ↓ 2.9k tokens)"
    re.compile(r'^[A-Z]\s+\d+m?\s*\d*s?\s*·\s*[↑↓]'),

    # 83. Truncated tool status: "ing 2 files…", "arching 3 files…"
    re.compile(r'^[a-z]{1,6}(?:ing|ching)\s+\d+\s+(?:files?|patterns?)'),

    # 84. Garbled "Searching for N pattern": "S rching or 1 pattern, reading 3 files…"
    re.compile(r'^S\s*\w*rching\s+\w+\s+\d+\s+pattern'),

    # 85. Garbled "N tool uses · tokens": "10 tol uses · 4.7k tokens"
    re.compile(r'^\d+\s+to[lo]*\s+uses?\s*·'),

    # 86. Short garbled timing + reading: "2 s, reading 4 files…"
    re.compile(r'^\d+\s+s[,·]\s*(?:reading|searching)'),

    # 87. CLI help — garbled command listings (no / prefix)
    #     "status  how Claude Code statusincluding version..."
    #     "statusline Set up Claude Code's stats line UI"
    #     "fronend-design (frontend-desgn) Crate distinctive..."
    #     "passes         Share afree week..."
    #     "conext Visualize current context usage..."
    re.compile(r'^(?:status|statusline|fronend|passes|conext|sage|ext\s+-usage)\s{2,}'),
    re.compile(r'^(?:status|statusline|fronend|passes|conext)\s+\w'),

    # 88. "+N more tool use(s)" — fix singular and word-merged (ctrl+
    re.compile(r'^\s*\+\d+\s+more\s+tool\s+use\w*'),

    # 89. "+N more lines" followed by ( instead of space
    re.compile(r'^\s*\+\d+\s+more\s+lines?\s*\('),

    # 90. Wrangler deploy stats: "Total Upload: 610.12 KiB / gzip:"
    re.compile(r'^Total\s*Upload:\s*\d'),

    # 91. Garbled spinner with timing in parens — broader: any line with
    #     … + (Ns · ↓) pattern regardless of prefix
    re.compile(r'…\s*\(\d+m?\s*\d*s?\s*·\s*[↑↓]'),

    # 92. Truncated spinner word fragments: "*Bebo", "*Razzl", etc.
    #     Short line with optional spinner char + capitalized fragment
    re.compile(r'^[*·✶✻✽✢]*[A-Z][a-z]{1,6}$'),

    # 93. Spinner verb + … + bare timing (no parens):
    #     "Beboppin'… 5 0s · ↓ 9.6k tokens)"
    re.compile(r"^[A-Z][a-zéèêë'-]+(?:ing|ed|ion|ting|in')…?\s+\d+\s"),

    # 94. Lines that are only Unicode whitespace (NBSP, etc.)
    re.compile(r'^[\s\xa0\u200b\u2000-\u200a\u2028\u2029\u3000]+$'),

    # 95. Garbled "Hatch g…" truncated spinner + short fragment + …
    re.compile(r'^[A-Z][a-z]+\s+[a-z]?…'),
]


# Lines containing these strings are ALWAYS kept (compaction markers)
COMPACTION_KEEP = [
    'compacting conversation',
    'conversation compacted',
    'compactingconversation',
    'conversationcompacted',
]


def is_garbage(line: str) -> bool:
    """Return True if line is TUI garbage that should be removed."""
    stripped = line.strip()
    if not stripped:
        return True  # blank lines (we'll re-add spacing intelligently later)

    # Preserve compaction markers — these track context lobotomy events
    lower = stripped.lower()
    for marker in COMPACTION_KEEP:
        if marker in lower:
            return False

    for pattern in GARBAGE_PATTERNS:
        if pattern.search(stripped):
            return True
    return False


def normalize_for_dedup(line: str) -> str:
    """Normalize a line for deduplication comparison.

    TUI repaints produce near-identical lines with slight differences
    in spacing, spinner chars, or word merging. Normalize by stripping
    spinner chars, collapsing spaces, and lowering.
    """
    s = line.strip()
    # Remove leading spinner/bullet chars
    s = re.sub(r'^[\s*·✶✻✽✢●◼◻❯⏵⎿]+', '', s)
    # Remove all whitespace for comparison (handles word-merge variants)
    s = re.sub(r'\s+', '', s)
    return s.lower()
