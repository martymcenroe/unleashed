# Lessons Learned — unleashed

| Date | Lesson | Rule/Action |
|:-----|:-------|:------------|
| 2026-03-16 | **Promoted untested c-28 to prod immediately after writing it.** User had to catch and correct this — stable version was c-27, c-28 had never been run. | **New code goes to alpha tier first.** alpha → beta (after testing) → prod (after burn-in). Never skip tiers, no matter how confident the code looks. |
| 2026-03-16 | **Said "admin terminal" without specifying which shell or how to open it.** User's system has bash, PowerShell 5, PowerShell 7, and cmd. "Terminal" is meaningless — user was rightfully frustrated. Gave wrong instructions multiple times, wasting the user's time while the system was in crisis. | **Always specify: exact shell name + exact click path to open it.** E.g., "Right-click Start → Terminal (Admin) — this opens bash. In that bash window, run..." Never assume which shell opens. |
| 2026-03-16 | **Used `/y` flag with `net stop` — not a valid flag.** Then gave `&&` (bash syntax) for PowerShell, then backtracked to `net stop` alone in bash. Three wrong commands in a row while user's machine was hung. | **Test Windows admin commands mentally before prescribing.** `net stop` has no `/y` flag — use `echo Y | net stop <service>` or just let the user type Y. Verify shell syntax matches the shell the user is actually in. |
