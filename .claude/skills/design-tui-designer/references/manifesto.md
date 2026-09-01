# The Renaissance Engineer Terminal UX Manifesto

This is not marketing copy. It is a philosophy for building terminal interfaces
that serve serious engineers doing serious work.

---

## Lineage

This manifesto draws from three traditions:

### Human Factors (Don Norman)
From *The Design of Everyday Things*:
- **Affordance** -- Visual cues that suggest interaction. In TUIs: highlighted rows,
  borders, cursor position.
- **Feedback** -- Immediate redraw on action. Every keystroke must visibly respond.
- **Mapping** -- Arrow keys move in the visible direction. Tab moves focus predictably.
- **Constraints** -- Don't let users do destructive things accidentally. Confirmation
  flows prevent irreversible mistakes.

### Unix Philosophy
From *The Unix Programming Environment* and *The Art of Unix Programming*:
- Small composable tools
- Plain text as the universal interface
- Predictable keybindings
- Scriptability
- Discoverable help (`--help`, `?`, `man`)

### Modern Exemplars
From studying lazygit, htop, tig, and ncdu:
- Strong focus indication
- Minimal semantic color
- Bottom key legends
- Pane clarity
- No ambiguity in input target

---

## The Ten Principles

### 1. The Terminal Is Not Retro. It Is Intentional.

The Renaissance Engineer chooses the terminal because:
- It reduces distraction
- It favors precision
- It respects cognitive flow
- It scales with complexity

The terminal is not a constraint. It is a medium chosen with purpose.

### 2. Power Without Obscurity

A tool must be:
- **Immediately usable** -- productive in 30 seconds without docs
- **Gradually powerful** -- deeper features revealed through exploration
- **Deeply scriptable** -- automatable by experts

Discoverability is kindness. The bottom bar shows what keys do. Help is always
one keystroke away. Command mode exists for power users but is never required.

**lazygit exemplifies this**: beginners use arrows, intermediates use hotkeys,
experts use `:` command mode.

### 3. Feedback Is Respect

Every action must respond:
- Change a highlight
- Update a detail pane
- Show a status message
- Or visibly reject the input

Silence is ambiguity. Ambiguity is friction. Dead keystrokes feel broken.

**htop exemplifies this**: bars animate, numbers update, selection persists.
The system feels alive.

### 4. Density Is a Feature

Engineers are not consumers of fluff. We want:
- Context
- State
- Structure
- Speed

The TUI should feel like a cockpit, not a brochure.

Information density is good when:
- Hierarchy is visible
- Whitespace is used to group, not to decorate
- Columns are aligned
- Related items are adjacent

**htop exemplifies this**: it maximizes vertical information, embraces terminal
height, and assumes engineers are comfortable with dense tables.

### 5. Focus Is Sacred

Only one thing can receive input at a time. The interface must make that obvious.

- The focused pane has brighter borders
- The focused item has a clear highlight
- Unfocused panes are visually receded
- There is never ambiguity about what will respond to a keypress

Cognitive drift is design failure.

**lazygit exemplifies this**: one pane is active, its border is brighter, the
key legend updates to show only relevant actions.

### 6. Composability Over Completeness

The TUI is not the universe. It must:
- Output plain text when piped
- Integrate with shell tools
- Coexist with `grep`, `awk`, `jq`
- Be automatable via scripts

A TUI should never fight the shell. `ctx apply` should be composable.
UI and CLI should not conflict.

### 7. Modes Must Be Visible

If you use modes, always show:
```
-- INSERT --
```
or:
```
MODE: FILTER
```

Hidden state is the enemy of clarity. Every mode must be explicit. Every context
must be named. Never allow silent modal switches.

### 8. Safety Before Speed

Destructive actions must:
- Be reversible, OR
- Require confirmation

Speed is meaningless if trust is lost.

Patterns:
- `Delete repo-a? (y/N)` -- inline confirmation with safe default
- Modal dialog with explicit Enter/Esc
- Type-to-confirm for irreversible operations

### 9. Keyboard First. Always.

The Renaissance Engineer does not reach for a mouse. The interface must assume:
- Fast typing
- Predictable navigation
- Muscle memory

Use common bindings:
- `j/k` and arrows for movement
- `g`/`G` for top/bottom
- `/` for search
- `:` for command mode (optional)
- `?` for help
- `q` for quit
- `Esc` for cancel

### 10. The Tool Should Disappear

The ultimate goal: the engineer stops thinking about the interface and thinks
only about the system they are shaping.

When the interface fades into cognition, the design has succeeded.

This is the measure of Renaissance-grade terminal craftsmanship.
