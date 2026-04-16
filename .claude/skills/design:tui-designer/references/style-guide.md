# TUI Style Guide

Practical engineering rules for visual design in terminal interfaces.

---

## 1. Color System

### Semantic Color Only

| Meaning    | Color   | ANSI      | Use Cases                                  |
|------------|---------|-----------|-------------------------------------------|
| Success    | Green   | 32 / #2ecc71 | Clean, passing, completed, synced       |
| Warning    | Yellow  | 33 / #f39c12 | Dirty, pending, needs attention         |
| Error      | Red     | 31 / #e74c3c | Failed, broken, destructive action      |
| Selection  | Blue    | 34 / #3498db | Active focus, current item, highlight   |
| Metadata   | Grey    | 90 / #95a5a6 | Timestamps, IDs, secondary info         |
| Default    | White   | 37 / #ecf0f1 | Primary text, content                   |

### Forbidden

- No decorative color (rainbow dashboards, gradient backgrounds)
- No color as the ONLY indicator (always pair with shape/text for accessibility)
- No background color fills except for selection highlight
- No blinking text, ever

### Adaptive Color

Support both light and dark terminal backgrounds:
- Test with both `COLORFGBG` settings
- Use Lip Gloss `AdaptiveColor` (Go) or TCSS variables (Python) for adaptation
- Prefer ANSI 256 colors for maximum terminal compatibility

---

## 2. Typography Rules

### Monospace Discipline

- Monospace only. No proportional font assumptions.
- No emojis unless semantically meaningful AND terminal-safe (check width).
- Avoid wide glyphs (CJK, some symbols) in alignment-critical areas.
- Test rendering in at least: iTerm2, Terminal.app, Alacritty, Windows Terminal.

### Column Alignment

This is critical for data-heavy interfaces.

**Bad**:
```
repo   status
repo-long-name   clean
```

**Good**:
```
repo             status
repo-long-name   clean
```

Rules:
- Right-pad text columns to uniform width
- Right-align numeric columns
- Use consistent separator width (2+ spaces or `|` with padding)
- Truncate with `...` rather than wrapping

### Text Density Tiers

| Tier    | Lines Between Groups | Use Case                    |
|---------|---------------------|-----------------------------|
| Dense   | 0                   | Tables, logs, process lists |
| Normal  | 1                   | Forms, detail views         |
| Spacious| 2                   | Help screens, dialogs       |

### Status Flags

Use single-character flags for density (lazygit-style):

| Flag | Meaning      |
|------|-------------|
| `M`  | Modified     |
| `A`  | Added        |
| `D`  | Deleted      |
| `R`  | Renamed      |
| `?`  | Untracked    |
| `!`  | Conflict     |

---

## 3. Interaction Grammar

### Standard Keybinding Table

Every TUI should support these as baseline:

| Action          | Primary Key | Alternate | Notes                    |
|-----------------|-------------|-----------|--------------------------|
| Move down       | `Down`      | `j`       | Always both              |
| Move up         | `Up`        | `k`       | Always both              |
| Move left       | `Left`      | `h`       | In tree/pane navigation  |
| Move right      | `Right`     | `l`       | In tree/pane navigation  |
| Jump to top     | `g`         |           | vim convention           |
| Jump to bottom  | `G`         |           | vim convention           |
| Page down       | `PgDn`      | `Ctrl+f`  | For long lists           |
| Page up         | `PgUp`      | `Ctrl+b`  | For long lists           |
| Select / Open   | `Enter`     |           | Primary action           |
| Search / Filter | `/`         |           | Begin filter-as-you-type |
| Command mode    | `:`         |           | Optional, power users    |
| Help            | `?`         | `F1`      | Always available         |
| Quit            | `q`         | `Ctrl+c`  | Always available         |
| Cancel / Back   | `Esc`       |           | Always available         |
| Switch pane     | `Tab`       |           | Multi-pane layouts       |
| Previous pane   | `Shift+Tab` |           | Reverse pane cycle       |

### Key Legend Format

Bottom of screen, single line:

```
 Up/Dn Move  Enter Open  d Delete  / Search  ? Help  q Quit
```

Rules:
- Key name first, then action
- Space-separated groups
- Dynamic: changes based on focused pane and current mode
- Shows only relevant actions for current context

### Dead Keystroke Rule

**Every keypress must do something visible.** If a key has no action in the
current context, either:
- Ignore it silently (acceptable for modifier keys)
- Show a brief "Unknown key" flash (for letter keys)
- Never produce a visible character in a non-input context

---

## 4. Feedback Discipline

### Response Time Rules

| Action Type     | Expected Feedback Time | Feedback Type              |
|-----------------|----------------------|---------------------------|
| Navigation      | Instant (< 16ms)     | Highlight moves            |
| Selection       | Instant              | Detail pane updates        |
| Filter typing   | Instant              | List narrows               |
| Command execute | < 100ms or spinner   | Status message or progress |
| Long operation  | Immediate spinner    | Loading indicator          |
| Error           | Immediate            | Red status message         |
| Success         | Immediate            | Green status message       |

### Progress Indicators

For operations > 200ms:
- Show a spinner immediately
- Show progress bar if duration is estimable
- Show elapsed time for long operations
- Always allow cancellation (Esc or Ctrl+c)

### Status Messages

Flash messages appear in the status strip and auto-dismiss:

```
STATUS: Applied lens to 3 repositories                    [2s ago]
```

Error messages persist until dismissed:

```
ERROR: Failed to connect to remote. Press any key to dismiss.
```

---

## 5. Mode Clarity

### Mode Display

If the application has modes, always show the current mode:

```
-- NORMAL --     (default, navigation)
-- INSERT --     (text input active)
-- FILTER --     (filter-as-you-type active)
-- COMMAND --    (: command mode active)
-- VISUAL --     (selection/marking mode)
```

### Mode Transitions

| From     | To       | Trigger | Exit      |
|----------|----------|---------|-----------|
| NORMAL   | FILTER   | `/`     | `Esc`     |
| NORMAL   | COMMAND  | `:`     | `Esc`     |
| NORMAL   | INSERT   | `i`/`e` | `Esc`     |
| ANY      | NORMAL   | `Esc`   | --        |

### No Silent Mode Switches

If focus moves to an input field, the mode indicator must change.
If a dialog opens, the mode indicator must reflect it.
Hidden modes are forbidden.

---

## 6. Density Philosophy

### Information Hierarchy

Use visual weight to establish hierarchy:

1. **Bold/Bright** -- Primary item (name, title)
2. **Normal** -- Secondary info (status, counts)
3. **Dim/Grey** -- Metadata (timestamps, IDs, paths)
4. **Border/Rule** -- Structure (pane boundaries, separators)

### Grouping

- Group related items visually (adjacent, same pane)
- Separate groups with blank lines or horizontal rules
- Never scatter related information across distant screen areas

### Scroll Discipline

- Avoid horizontal scrolling (truncate with `...` instead)
- Vertical scrolling is acceptable but show scroll position
- Keep context visible: headers and legends never scroll
- Scroll indicator: `[1-25 of 142]` or scroll bar
