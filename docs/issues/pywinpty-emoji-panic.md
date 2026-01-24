# GitHub Issue: pywinpty

## Title

`pty.write()` panics on strings containing UTF-16 surrogate pair characters

---

## Issue Body

### Description

Calling `PtyProcess.write()` with a Python string containing UTF-16 surrogate pair characters (U+D800-U+DFFF) as separate code points causes a Rust panic in PyO3's string conversion code.

This is a real-world issue when using `ReadConsoleInputW` to read user input on Windows - emoji and other non-BMP characters are delivered as two separate WCHAR values (the high and low surrogate). When these are collected and concatenated into a Python string, `pty.write()` panics.

### Environment

- **OS**: Windows 11 (MINGW64_NT-10.0-26200)
- **Python**: 3.14
- **pywinpty**: 3.0.2
- **pyo3**: 0.26.0 (bundled)

### Minimal Reproduction

Save as `test_surrogate.py` and run with `python test_surrogate.py`:

```python
import winpty

pty = winpty.PtyProcess.spawn(['cmd.exe'])

# This works - normal Python emoji string
pty.write("🚀")

# This panics - surrogate pair as separate characters
# (how Windows ReadConsoleInputW delivers emoji)
pty.write(chr(0xD83D) + chr(0xDE80))
```

### Error Output

```
thread '<unnamed>' panicked at C:\Users\runneradmin\.cargo\registry\src\index.crates.io-1949cf8c6b5b557f\pyo3-0.26.0\src\conversions\std\osstr.rs:116:13:
assertion `left == right` failed
  left: 2
 right: 3
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

pyo3_runtime.PanicException: assertion `left == right` failed
  left: 2
 right: 3
```

### Root Cause Analysis

The panic occurs in PyO3's `osstr.rs` during string conversion. The assertion compares:

- **left**: Python string length (`len()` = 2 surrogate chars)
- **right**: UTF-16 code unit count after conversion (3, because surrogates get re-encoded)

When a Python string contains surrogate code points as separate characters (e.g., `chr(0xD83D) + chr(0xDE80)`), the string length is 2. But when PyO3 converts this to UTF-16 for Windows, the encoding produces a different number of code units, failing the assertion.

The normal emoji string `"🚀"` works because Python internally represents it as a single code point (U+1F680), not as surrogate pairs.

### Real-World Context

This affects PTY wrappers that read Windows console input via `ReadConsoleInputW`. When a user pastes text containing emoji, Windows delivers each emoji as two separate WCHAR values (high and low surrogate). The wrapper collects these with:

```python
output_chars.append(key_event.uChar)  # WCHAR - could be surrogate
# ... later ...
pty.write(''.join(output_chars))  # Panic if surrogates present
```

### Possible Workaround

Normalize the string before writing to replace surrogate pairs with the actual character:

```python
text = ''.join(output_chars)
# Encode to UTF-16 and back to normalize surrogate pairs
normalized = text.encode('utf-16', 'surrogatepass').decode('utf-16')
pty.write(normalized)
```

### Possible Upstream

This may be a PyO3 issue rather than pywinpty specifically, as the panic originates in `pyo3-0.26.0\src\conversions\std\osstr.rs`. Happy to file there instead if that's more appropriate.

---

## Labels (suggested)

`bug`, `unicode`, `windows`, `surrogate`
