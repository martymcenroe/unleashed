# Process Budget — Unleashed on Windows

Last verified: 2026-03-16 on Windows 11 (MINGW64, 32 GB RAM)

## Per-Session Process Cost

Each unleashed session spawns a tree of Windows processes. The cost depends on whether companion tabs (mirror, friction, console) are enabled.

### Bare session (no companions)

| Process | Count | Role |
|---------|-------|------|
| python.exe | 2 | Unleashed PTY wrapper |
| node.exe | 2 | Claude CLI (or Gemini/Codex equivalent) |
| cmd.exe | 3 | PTY intermediaries (`cmd /c claude.cmd`) |
| bash.exe | 2 | Terminal shell + PTY child shell |
| OpenConsole.exe | 2 | Windows Terminal tab hosts |
| **Total** | **~11** | |

### Full session (mirror + friction + console — the default)

Each companion tab adds a `wt.exe` launch (exits immediately) which creates:
- 1 bash.exe (the shell running in the tab)
- 1 tail (for mirror/friction tabs) or 1 bash child (for console tab)
- 1 conhost.exe or OpenConsole.exe (console host for the tab)

| Companion | Adds |
|-----------|------|
| Mirror tab | bash + tail + conhost |
| Friction tab | bash + tail + conhost |
| Console tab | bash + conhost |

**Full session total: ~21 processes.**

### System baseline (no unleashed)

Windows maintains ~6 conhost.exe processes at idle (system services). These are not ours and are always present.

## Capacity

| Scenario | Sessions | Processes | Notes |
|----------|----------|-----------|-------|
| Full (default) | 8 | ~168 | Comfortable |
| Full (default) | 12 | ~252 | Near limit — `tasklist` slows |
| Full (default) | 16 | ~336 | Console handle contention likely |
| Bare (no companions) | 16 | ~176 | Same as 8 full sessions |
| Bare (no companions) | 24 | ~264 | Feasible but monitor |

**The bottleneck is not RAM.** 32 GB is more than enough. The bottleneck is Windows console handle enumeration — once you cross ~250-300 processes with console handles, `tasklist` and `wmic` slow to 30+ seconds, and ConPTY allocation starts failing. Sessions die silently (`pty.isalive()` returns false).

## The Companion Tab Leak (fixed in #84)

Before #84, companion tabs were fire-and-forget. When a session died:
- The python wrapper exited
- The Claude CLI (node) exited
- The 3 companion tabs stayed alive indefinitely

After a few session restarts, orphaned processes accumulated. Observed on 2026-03-16:

```
40 bash, 17 OpenConsole, 14 conhost, 6 tail — from 4 active sessions
tasklist: 30+ seconds to enumerate
Sessions dying from handle exhaustion
```

**Fix (#84):** Each companion's bash command now includes `UNLEASHED_SID={session_timestamp}` as an environment variable. On session exit, `_cleanup_companions()` uses `wmic` to find all processes with that marker and kills them with `taskkill /T /F`.

## Practical Guidelines

1. **Default config is fine for up to 8 concurrent sessions.** Beyond that, consider `--no-console` or `--no-mirror --no-friction` to reduce the footprint.

2. **If the machine feels slow**, check process counts:
   ```bash
   powershell -NoProfile -Command 'Get-Process bash,conhost,OpenConsole,node,python,tail,cmd -EA 0 | Group-Object ProcessName | Sort-Object Count -Desc | Format-Table Count,Name -Auto'
   ```

3. **To kill orphans manually** (identify by start time, kill non-current clusters):
   ```bash
   powershell -NoProfile -Command 'Get-Process bash,python,node,cmd,OpenConsole -EA 0 | Select-Object ProcessName,Id,@{N="Start";E={$_.StartTime.ToString("HH:mm:ss")}} | Sort-Object ProcessName,Start | Format-Table -Auto'
   ```

4. **`--no-console` is the biggest win** if you need more headroom. The console tab is the most expensive companion (full bash login shell) and the least critical (you already have a terminal).
