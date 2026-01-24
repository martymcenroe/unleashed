# Runbook: Testing pywinpty Emoji Panic Issue

## Purpose

Reproduce the pywinpty panic that occurs when writing emoji/non-BMP Unicode characters to a PTY.

## Prerequisites

- Poetry installed
- Inside the unleashed project directory

## Steps to Reproduce

1. Navigate to unleashed:
   ```bash
   cd /c/Users/mcwiz/Projects/unleashed
   ```

2. Run the test script:
   ```bash
   poetry run python src/test_emoji.py
   ```

3. Expected result: Rust panic with assertion failure:
   ```
   thread '<unnamed>' panicked at ...pyo3-.../src/conversions/std/osstr.rs:116:13:
   assertion `left == right` failed
   ```

## Cleanup

pywinpty is already a dependency of unleashed (required for the PTY wrapper), so no cleanup needed for normal use.

If you installed pywinpty separately in your global environment and want to remove it:

```bash
pip uninstall pywinpty
```

To verify it's removed:
```bash
pip show pywinpty
```

Should return "WARNING: Package(s) not found: pywinpty"

## Related

- Issue draft: `docs/issues/pywinpty-emoji-panic.md`
- GitHub: https://github.com/andfoy/pywinpty
