---
name: design:tui-designer
description: >
  Design production-grade terminal user interfaces grounded in Norman UX principles,
  Unix philosophy, structural lessons from lazygit and htop, and full Textual/Bubble Tea
  framework capabilities. Generates ASCII prototypes, interaction grammars, safety models,
  and optional framework implementation plans.
---

# Renaissance TUI Designer

Design terminal user interfaces that feel like cognitive amplifiers, not retro novelties.

This skill synthesizes Don Norman's human factors research, Unix philosophy, structural
analysis of best-in-class tools (lazygit, htop, tig), and the full capabilities of modern
TUI frameworks (Textual, Bubble Tea) into a rigorous design process.

---

## When to Use This Skill

Activate when the user:
- Asks to design a TUI, terminal app, or CLI with interactive elements
- Wants to prototype a terminal interface layout
- Needs an interaction grammar or keybinding scheme
- Wants to plan a Textual or Bubble Tea application
- Says "design a terminal UI for..."

---

## The Renaissance Engineer Terminal UX Manifesto

These ten principles are non-negotiable. Every design must align with them.

### 1. The Terminal Is Intentional
The terminal reduces distraction and favors precision. It scales with complexity
and respects cognitive flow. Choosing it is a deliberate act of engineering discipline.

### 2. Power Without Obscurity
A tool must be immediately usable, gradually powerful, and deeply scriptable.
Discoverability is kindness. If a user launches your app without reading docs,
they should be productive in 30 seconds.

### 3. Feedback Is Respect
Every action must produce visible response. Silence is ambiguity. Ambiguity is friction.
Every keystroke must change a highlight, update a pane, show status, or visibly reject input.

### 4. Density Is a Feature
Engineers are not consumers of fluff. We want context, state, structure, and speed.
The TUI should feel like a cockpit, not a brochure. Information density is good when
hierarchy is visible.

### 5. Focus Is Sacred
Only one thing can receive input at a time. The interface must make that obvious.
Cognitive drift is design failure. Never allow ambiguous interaction targets.

### 6. Composability Over Completeness
The TUI is not the universe. It must output plain text, integrate with pipes, coexist
with shell tools, and be automatable. A TUI should never fight the shell.

### 7. Modes Must Be Visible
Hidden state is the enemy of clarity. Every mode must be explicit. Every context must
be named. If modes exist, they must be displayed: `-- INSERT --`, `MODE: FILTER`.

### 8. Safety Before Speed
Destructive actions must be reversible or require confirmation. Speed is meaningless
if trust is lost. Never inline-delete without asking.

### 9. Keyboard First. Always.
The Renaissance Engineer does not reach for a mouse. The interface must assume fast
typing, predictable navigation, and muscle memory.

### 10. The Tool Should Disappear
When the engineer stops thinking about the interface and thinks only about the system
they are shaping, the design has succeeded.

---

## Design Process

### Phase 1: Discovery

Ask the user these questions (use AskUserQuestion when appropriate):

1. **Domain**: What is this tool for? (git management, monitoring, data exploration, etc.)
2. **Data nature**: Real-time streaming or static/on-demand?
3. **Density level**: Sparse (few items) or dense (tables, logs, metrics)?
4. **Layout**: Single-pane, split (master-detail), or multi-pane?
5. **Interaction model**: Navigation-only, command mode, or hybrid?
6. **Destructive actions**: Are there delete/kill/reset operations?
7. **Scriptability**: Must it support piped input/output?
8. **Target framework**: Textual (Python), Bubble Tea (Go), or framework-agnostic?

### Phase 2: ASCII Prototype

Generate an ASCII layout using the patterns in [ascii-patterns.md](references/ascii-patterns.md).

Select from these structural patterns:
- **Master-Detail Split** -- for browsing lists with detail views
- **Command Mode Overlay** -- for vim-style power interaction
- **Filter-as-You-Type** -- for real-time search narrowing
- **Status Strip** -- for persistent state display
- **Safe Destructive Confirmation** -- for delete/kill operations
- **Pane Focus Indicator** -- for multi-pane navigation
- **Tab Navigation** -- for categorical views
- **Full-Screen Table** -- for data-heavy tools (htop-style)
- **Tree Navigation** -- for hierarchical data

Always include:
- Key legend at bottom
- Status strip
- Focus indicator
- Mode indicator (if modal)

### Phase 3: Interaction Grammar

Define the complete keybinding table. Start from the standard grammar:

| Action         | Key              | Notes                          |
|----------------|------------------|--------------------------------|
| Move down      | `Down` or `j`    | Always both                    |
| Move up        | `Up` or `k`      | Always both                    |
| Jump to top    | `g`              | vim convention                 |
| Jump to bottom | `G`              | vim convention                 |
| Select/Open    | `Enter`          | Primary action                 |
| Search/Filter  | `/`              | Begin filter-as-you-type       |
| Command mode   | `:`              | Optional, for power users      |
| Help           | `?`              | Always available               |
| Quit           | `q`              | Always available               |
| Cancel/Back    | `Esc`            | Always available               |
| Switch pane    | `Tab`            | For multi-pane layouts         |
| Delete         | `d`              | Must confirm                   |

Then add domain-specific bindings. Verify:
- No dead keystrokes
- No conflicts
- Muscle memory respected

### Phase 4: Focus Model

Document explicitly:
- Which element receives input by default
- How focus transfers between panes/widgets
- Visual indicator for focused element (border brightness, highlight color, cursor)
- What happens to unfocused panes (dimmed? static?)
- Prevention of ambiguous input targets

### Phase 5: Color Semantics

Use ONLY semantic color. No decorative rainbow.

| Meaning    | Color   | Usage                              |
|------------|---------|------------------------------------|
| Success    | Green   | Completed, clean, passing          |
| Warning    | Yellow  | Attention needed, dirty, pending   |
| Error      | Red     | Failed, broken, destructive action |
| Selection  | Blue    | Current focus, active item         |
| Metadata   | Grey    | Secondary info, timestamps, IDs   |
| Default    | White   | Primary text                       |

### Phase 6: Safety Model

For every destructive action, define:
- What triggers it (key, command)
- Confirmation mechanism (inline y/N, modal dialog, type-to-confirm)
- Visual warning (red highlight, explicit label)
- Escape route (Esc always cancels)

### Phase 7: Framework Implementation Plan (Optional)

If the user specifies a framework, map the design to concrete components.

**For Textual** -- see [textual-reference.md](references/textual-reference.md):
- Choose layout containers (Horizontal, Vertical, Grid)
- Select widgets (DataTable, Tree, ListView, Static, etc.)
- Plan CSS for focus styling and semantic colors
- Define key bindings via BINDINGS
- Use reactive properties for state
- Use ModalScreen for destructive confirmations
- Use Footer for key legend
- Use Worker for async operations

**For Bubble Tea** -- see [bubbletea-reference.md](references/bubbletea-reference.md):
- Define Model struct (all state)
- Plan Update message types
- Design View string composition
- Use Bubbles components (list, table, viewport, textinput)
- Style with Lip Gloss (semantic colors only)
- Use tea.Cmd for async I/O

---

## Output Structure

Every design deliverable must include these sections:

1. **Overview** -- one paragraph describing the tool
2. **ASCII Prototype** -- the visual layout
3. **Interaction Grammar** -- complete keybinding table
4. **Focus Model** -- focus flow documentation
5. **Color System** -- semantic color assignments
6. **Safety Model** -- destructive action handling
7. **Framework Implementation Plan** -- (if framework specified)
8. **Manifesto Alignment Summary** -- which principles are most critical for this design

---

## Validation Checklist

Before delivering any design, verify:

- [ ] Visible focus indicator on active element
- [ ] `?` shows help
- [ ] `Esc` cancels/goes back
- [ ] `q` quits
- [ ] Status strip present
- [ ] Key legend visible
- [ ] No hidden modes
- [ ] All destructive actions require confirmation
- [ ] No dead keystrokes
- [ ] No decorative color
- [ ] Column alignment correct
- [ ] Arrow keys AND j/k work for navigation
- [ ] Composable with shell (piped output where relevant)

---

## References

- [ASCII Design Patterns](references/ascii-patterns.md) -- Reusable structural patterns
- [TUI Style Guide](references/style-guide.md) -- Color, typography, density rules
- [Renaissance Manifesto](references/manifesto.md) -- Full philosophy with examples
- [Textual Framework Reference](references/textual-reference.md) -- Complete widget/layout catalog
- [Bubble Tea Framework Reference](references/bubbletea-reference.md) -- Elm architecture and components
