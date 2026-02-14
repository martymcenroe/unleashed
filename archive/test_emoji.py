"""
Minimal reproduction for pywinpty surrogate pair panic.
See: docs/issues/pywinpty-emoji-panic.md

Run with: poetry run python src/test_emoji.py

The bug: pyo3 panics when a Python string contains surrogate pair characters
(U+D800-U+DFFF) as separate code points, which is how Windows ReadConsoleInputW
delivers emoji and other non-BMP characters.
"""
import winpty

pty = winpty.PtyProcess.spawn(['cmd.exe'])

# This works - normal Python emoji string (internally normalized)
pty.write("🚀")

# This panics - surrogate pair as separate characters
# (how Windows delivers emoji via ReadConsoleInputW)
pty.write(chr(0xD83D) + chr(0xDE80))  # Same emoji, but as surrogate chars
