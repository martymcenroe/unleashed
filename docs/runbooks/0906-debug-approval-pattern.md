# Runbook: Debug Approval Pattern Matching

## Purpose

Diagnose why unleashed is not auto-approving permission prompts when it should be.

## Prerequisites

- unleashed-00015.py with debug logging enabled
- A permission prompt that fails to auto-approve

## Steps

### 1. Trigger the Issue

Run unleashed and perform an action that triggers a permission prompt:

```bash
cd /c/Users/mcwiz/Projects/SomeProject
unleashed
```

Wait for Claude to request a permission (Read, Write, Bash, etc.)

### 2. Capture the Debug Output

When the permission prompt appears and does NOT auto-approve, the raw bytes are written to:

```
C:\Users\mcwiz\Projects\unleashed\logs\pattern_debug.bin
```

### 3. Analyze the Raw Bytes

View the hex dump:

```bash
xxd /c/Users/mcwiz/Projects/unleashed/logs/pattern_debug.bin | head -100
```

Or in Python:

```bash
poetry run python -c "
data = open('logs/pattern_debug.bin', 'rb').read()
# Find 'Esc to cancel' and show surrounding bytes
idx = data.find(b'Esc to cancel')
if idx >= 0:
    chunk = data[idx:idx+100]
    print('Raw bytes:', chunk)
    print('Hex:', chunk.hex())
    print('Repr:', repr(chunk))
"
```

### 4. Identify the Pattern

Look for:
- The middle dot character (expected: `\xc2\xb7` for U+00B7)
- ANSI escape codes (e.g., `\x1b[0m`, `\x1b[90m`) breaking the text
- Different Unicode characters (bullet `\xe2\x80\xa2` for U+2022)

### 5. Update the Pattern

Edit `unleashed-00015.py` line ~84:

```python
FOOTER_PATTERN = b'Esc to cancel \xc2\xb7 Tab to add'
```

Adjust based on findings:
- If ANSI codes present: Match shorter pattern or strip codes
- If different dot char: Update the hex bytes

### 6. Remove Debug Logging

After fixing, remove the debug logging from `_reader_pty()`:

```python
# DELETE these lines:
if b'Esc to cancel' in search_chunk:
    with open('C:/Users/mcwiz/Projects/unleashed/logs/pattern_debug.bin', 'wb') as f:
        f.write(search_chunk)
```

### 7. Test the Fix

Run unleashed again and verify auto-approval works.

## Common Patterns

| Character | UTF-8 Bytes | Description |
|-----------|-------------|-------------|
| · (U+00B7) | `\xc2\xb7` | Middle dot (expected) |
| • (U+2022) | `\xe2\x80\xa2` | Bullet |
| ∙ (U+2219) | `\xe2\x88\x99` | Bullet operator |
| ‧ (U+2027) | `\xe2\x80\xa7` | Hyphenation point |

## Related

- `src/unleashed-00015.py` - Main script with pattern matching
- Issue: Resume Session picker also has "Esc to cancel" (fixed in v00015)
