# Plan: Unleashed-G (Gemini CLI Support)

## Summary

Create a Gemini CLI variant of unleashed (`unleashed-g`) and reorganize the naming convention.

## Current State

- `unleashed.py` - v00016 (Claude)
- `unleashed-00016.py` - v00016 (Claude, duplicate)
- `unleashed-00002.py` through `unleashed-00015.py` - old versions (to delete)
- Aliases in `.bash_profile`: 5-digit numbering (`unleashed-00002` through `unleashed-00020`)

## Target State

### File Structure
```
src/
├── unleashed.py          # Claude base (current, unchanged)
├── unleashed-c-16.py     # Claude v16 (renamed from unleashed-00016.py)
├── unleashed-g.py        # Gemini base (new)
└── unleashed-g-01.py     # Gemini v01 (new, first version)
```

### Aliases (new convention, 2-digit)
```bash
# Claude variants
alias unleashed-c='...'       # runs unleashed.py
alias unleashed-c-16='...'    # through unleashed-c-25 (10 slots)

# Gemini variants
alias unleashed-g='...'       # runs unleashed-g.py
alias unleashed-g-01='...'    # through unleashed-g-10 (10 slots)
```

### Gemini Approval Pattern

From screenshot, Gemini CLI shows:
```
Allow execution of: 'cd, dir'?
● 1. Allow once
  2. Allow for this session
  3. No, suggest changes (esc)
⠦ Waiting for user confirmation ...
```

Pattern to match: `1. Allow once` (appears on permission prompts)

### Gemini Command

Need to determine: Where is `gemini` installed? (likely npm global or standalone)

## Tasks

### 1. Cleanup old files
- [ ] Delete `unleashed-00002.py` through `unleashed-00015.py`

### 2. Rename Claude version
- [ ] Rename `unleashed-00016.py` → `unleashed-c-16.py`

### 3. Create Gemini base (`unleashed-g.py`)
- [ ] Copy from `unleashed.py`
- [ ] Change `VERSION` to "g-01"
- [ ] Change `CLAUDE_CMD` to gemini path
- [ ] Change `FOOTER_PATTERN` to match Gemini prompt (`1. Allow once`)
- [ ] Update docstring

### 4. Create first Gemini versioned file
- [ ] Copy `unleashed-g.py` to `unleashed-g-01.py`

### 5. Update `.bash_profile`
- [ ] Remove old aliases (`unleashed-00002` through `unleashed-00020`)
- [ ] Add new Claude aliases (`unleashed-c`, `unleashed-c-16` through `unleashed-c-25`)
- [ ] Add new Gemini aliases (`unleashed-g`, `unleashed-g-01` through `unleashed-g-10`)
- [ ] Keep existing `unleashed` alias (points to `unleashed.py`)

## Questions for User

1. Where is `gemini` CLI installed? (need exact path for `GEMINI_CMD`)
2. Confirm pattern `1. Allow once` is reliable for Gemini auto-approval?
3. Should `unleashed` alias remain pointing to Claude, or become a selector?
