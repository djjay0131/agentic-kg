# ASCII Design Patterns for TUI

Reusable structural patterns derived from studying lazygit, htop, tig, ncdu,
and the Charmbracelet ecosystem.

---

## Pattern 1: Master-Detail Split

**Use for**: logs, repos, files, PRs, any list with detail view.
**Exemplar**: lazygit (files + diff), tig (commits + patch).

```
+-------------------------------+------------------------------+
| Items                         | Details                      |
|                               |                              |
| > repo-a                      | Name: repo-a                 |
|   repo-b                      | Status: Clean                |
|   repo-c                      | Branch: feature/x            |
|                               |                              |
|                               | Commits:                     |
|                               | - Fix build                  |
|                               | - Add tests                  |
|                               |                              |
+-------------------------------+------------------------------+
| Up/Dn Move  Enter Select  d Delete  / Search  q Quit         |
+---------------------------------------------------------------+
```

**Rules**:
- One highlighted row in the list pane
- Details update instantly on selection change
- Key legend always visible at bottom
- Active pane has brighter border
- Tab switches focus between panes

---

## Pattern 2: Command Mode Overlay

**Use for**: power-user interaction without mode confusion.
**Exemplar**: lazygit `:` mode, vim command line.

```
+----------------------------------------------+
|                                              |
|  Main interface content                      |
|                                              |
+----------------------------------------------+
| :                                            |
+----------------------------------------------+
```

When `:` is pressed, the bottom bar becomes an input field:

```
| :apply lens=security repo=core               |
```

**Rules**:
- `:` activates command mode
- `Esc` cancels and returns to normal mode
- `Enter` executes the command
- Tab completion is strongly recommended
- Mode indicator changes to show command mode active

---

## Pattern 3: Filter-as-You-Type

**Use for**: real-time search narrowing in lists.
**Exemplar**: fzf, lazygit filter.

```
Search: fea_
----------------------------------------------
> feature/login
  feature/oauth
  feature/cache
```

**Rules**:
- Real-time narrowing as user types
- Backspace works naturally
- `Esc` clears filter and returns to full list
- Highlighted match shows current selection
- Empty filter shows all items
- Match highlighting within results (bold or color)

---

## Pattern 4: Status Strip

**Use for**: persistent display of current mode, scope, and system state.
**Exemplar**: vim status line, htop meters.

```
+----------------------------------------------+
| Projects                                     |
+----------------------------------------------+
| ...content...                                |
+----------------------------------------------+
| MODE: NORMAL | Space: dev | Dirty: 2 | Synced |
+----------------------------------------------+
```

**Rules**:
- Always visible at bottom (above key legend) or top (below header)
- Shows current mode
- Shows current scope/context
- Shows system state (sync status, counts, errors)
- Color-coded semantically (red for errors, yellow for warnings)

---

## Pattern 5: Safe Destructive Confirmation

**Use for**: delete, kill, reset, any irreversible action.
**Exemplar**: htop F9 kill flow, lazygit force push confirm.

### Inline confirmation:
```
Delete repo-a? (y/N)
```
Default is always the safe option (capital N).

### Modal dialog:
```
+----------------------------+
| Confirm Deletion           |
|                            |
| repo-a will be removed.    |
| This cannot be undone.     |
|                            |
|   [ Enter: Confirm ]       |
|   [ Esc:   Cancel  ]       |
+----------------------------+
```

### Type-to-confirm (for critical operations):
```
+-----------------------------------+
| Confirm Force Push                |
|                                   |
| Type "force push" to confirm:     |
| > force pu_                       |
|                                   |
| [ Esc: Cancel ]                   |
+-----------------------------------+
```

**Rules**:
- Never inline-delete without asking
- Default is always the safe option
- Esc always cancels
- Visual warning (red border, red text) for destructive dialogs
- Modal blocks all other input until resolved

---

## Pattern 6: Pane Focus Indicator

**Use for**: any multi-pane layout.
**Exemplar**: lazygit pane borders.

Active pane (brighter/thicker border):
```
+===============+---------------+
| Items         | Details       |
| > repo-a      |               |
|   repo-b      |               |
+===============+---------------+
```

Or with color: active pane border is blue, inactive is grey.

**Rules**:
- Active pane border is visually distinct (brighter, colored, or thicker)
- Never allow hidden focus
- Tab cycles through panes in predictable order
- Key legend updates to reflect active pane's available actions

---

## Pattern 7: Tab Navigation

**Use for**: categorical views within a single application.
**Exemplar**: lazygit top tabs (Status, Files, Branches, Commits, Stash).

```
  Status | Files | Branches | Commits | Stash
  --------========---------+---------+------
  |                                          |
  | Files pane content                       |
  |                                          |
  +------------------------------------------+
```

**Rules**:
- Active tab is highlighted (underlined, bold, or colored)
- Number keys (1-5) can switch tabs directly
- Left/Right arrows navigate tabs when tab bar is focused
- Content area updates immediately on tab switch

---

## Pattern 8: Full-Screen Table

**Use for**: data-heavy monitoring, process lists, metrics.
**Exemplar**: htop, k9s.

```
  CPU [||||||||||||      ] 62%    Mem [||||||        ] 45%

  PID    USER     CPU%   MEM%   COMMAND
  -----------------------------------------------
  1234   root      5.0    2.1   nginx
> 5678   user      1.2    1.5   python app.py
  9012   user      0.8    3.2   node server.js
  3456   root      0.1    0.5   sshd

  F1 Help  F2 Setup  F3 Search  F4 Filter  F9 Kill  F10 Quit
```

**Rules**:
- Columns are precisely aligned
- Headers are visually distinct (bold or underlined)
- Selection row is highlighted
- Metric bars at top for real-time data
- Function keys for common actions (visible at bottom)
- Sortable columns (click header or keybinding)

---

## Pattern 9: Tree Navigation

**Use for**: hierarchical data, file systems, nested structures.
**Exemplar**: ranger, nnn, Textual DirectoryTree.

```
+----------------------------------------------+
| Project Structure                            |
|                                              |
| v src/                                       |
|   v components/                              |
|     > Header.tsx                             |
|     > Footer.tsx                             |
|   v pages/                                   |
|     > Home.tsx                               |
| > tests/                                     |
| > docs/                                      |
|                                              |
+----------------------------------------------+
| Up/Dn Move  Enter Expand  Left Collapse  q Quit |
+----------------------------------------------+
```

**Rules**:
- `v` for expanded, `>` for collapsed
- Enter or Right expands a node
- Left collapses a node or moves to parent
- Indentation shows depth
- Selected node is highlighted
- Tree state persists during session

---

## Pattern 10: Help Overlay

**Use for**: discoverable help that doesn't leave the current context.
**Exemplar**: lazygit `?` overlay.

```
+----------------------------------------------+
|              Keyboard Shortcuts               |
|                                              |
|  Navigation                                  |
|  ----------                                  |
|  Up/k     Move up                            |
|  Down/j   Move down                          |
|  g        Jump to top                        |
|  G        Jump to bottom                     |
|  Tab      Switch pane                        |
|                                              |
|  Actions                                     |
|  --------                                    |
|  Enter    Select / Open                      |
|  d        Delete (with confirmation)         |
|  /        Search / Filter                    |
|  :        Command mode                       |
|                                              |
|  Press ? or Esc to close                     |
+----------------------------------------------+
```

**Rules**:
- Overlays the main content (semi-transparent or full)
- `?` or `Esc` dismisses it
- Organized by category
- Shows ALL available keybindings
- Context-sensitive (shows bindings relevant to current pane/mode)

---

## Composition Rules

These patterns compose. A typical application combines several:

**Git tool** = Master-Detail + Tab Navigation + Filter + Command Mode + Help Overlay
**Monitor** = Full-Screen Table + Status Strip + Safe Destructive (kill)
**File browser** = Tree Navigation + Master-Detail + Filter
**Dashboard** = Tab Navigation + Full-Screen Table + Status Strip

When composing:
- Key legend always takes bottom row
- Status strip sits above key legend
- Help overlay covers everything
- Modal dialogs block everything
- Focus indicator is always visible
