---
name: design:tui-reviewer
description: >
  Review terminal user interfaces against Renaissance TUI standards. Audits focus management,
  interaction grammar, safety model, density, color semantics, Unix composability, and
  framework-specific implementation quality. Produces a scored report with prioritized actions.
---

# Renaissance TUI Reviewer

Audit and critique terminal user interfaces against the Renaissance TUI Design Standard.

This skill evaluates TUIs across eight dimensions derived from Don Norman's human factors
research, Unix philosophy, structural analysis of lazygit and htop, and modern framework
best practices (Textual, Bubble Tea).

---

## When to Use This Skill

Activate when the user:
- Asks to review a TUI design, prototype, or implementation
- Wants feedback on a terminal application's UX
- Says "review this TUI", "audit this terminal app", "check my TUI design"
- Provides ASCII mockups, screenshots, or code for a terminal interface
- Wants to compare their TUI against best practices

---

## Input Handling

The reviewer accepts multiple input types:

| Input Type        | Action                                                     |
|-------------------|------------------------------------------------------------|
| ASCII mockup      | Analyze layout, focus model, key legend, density           |
| Screenshot/image  | Visual analysis of color, layout, focus, density           |
| Source code        | Analyze implementation patterns, keybindings, safety       |
| Running app desc  | Ask clarifying questions, then evaluate described behavior  |
| Design spec       | Compare spec against Renaissance standards                 |

If the input is ambiguous, ask the user to clarify what they want reviewed.

---

## Review Process

### Step 1: Summarize

Write a 2-3 sentence summary of the tool's purpose and target user.

### Step 2: Evaluate Eight Dimensions

Score each dimension 1-10 and provide specific findings.

---

## Evaluation Dimensions

### 1. Structure (Layout & Visual Hierarchy)

Check for:
- [ ] Clear pane boundaries (borders, spacing)
- [ ] Persistent status strip showing mode/state
- [ ] Persistent key legend showing available actions
- [ ] Column alignment discipline (right-padded text, right-aligned numbers)
- [ ] Information hierarchy visible (bold/normal/dim progression)
- [ ] Logical grouping of related elements
- [ ] No horizontal scrolling (truncate with `...` instead)
- [ ] Scroll position indicators when content overflows

**Exemplar benchmark**: htop -- precise column alignment, meter bars at top,
function key legend at bottom, every pixel used intentionally.

### 2. Focus Management

Check for:
- [ ] Single active input target at all times
- [ ] Visible focus indicator (bright border, highlight, cursor)
- [ ] No ambiguous input zones
- [ ] Mode indicator visible when modes exist
- [ ] Predictable focus transfer (Tab cycles panes, Esc returns)
- [ ] Unfocused elements are visually receded

**Exemplar benchmark**: lazygit -- one pane active, brighter border,
key legend dynamically updates per focused pane.

### 3. Interaction Grammar

Check for:
- [ ] Arrow keys work for navigation everywhere
- [ ] `j`/`k` also work (vim convention)
- [ ] `Esc` cancels / goes back in ALL contexts
- [ ] `q` quits
- [ ] `?` shows help
- [ ] `/` starts search/filter if relevant
- [ ] `Enter` is the primary action
- [ ] No dead keystrokes (every key does something visible or is ignored)
- [ ] Key legend reflects current context
- [ ] No novel bindings without good reason

**Exemplar benchmark**: lazygit -- progressive power model (arrows -> hotkeys -> `:` commands).

### 4. Safety

Check for:
- [ ] Confirmation for all destructive actions (delete, kill, reset, force)
- [ ] Safe default on confirmation prompts (N, not Y)
- [ ] No silent destructive behavior
- [ ] `Esc` cancels any confirmation dialog
- [ ] Visual warning (red) for destructive confirmations
- [ ] Undo or reversal available where feasible

**Exemplar benchmark**: htop F9 Kill -- opens signal menu, requires selection,
then confirms. No accidental kills.

### 5. Color & Visual Semantics

Check for:
- [ ] Green = success/clean/passing
- [ ] Yellow = warning/dirty/pending
- [ ] Red = error/failed/destructive
- [ ] Blue = selection/active focus
- [ ] Grey = metadata/secondary
- [ ] No decorative rainbow usage
- [ ] Color is never the ONLY indicator (pair with text/shape)
- [ ] Works on both light and dark terminal backgrounds
- [ ] Limited palette (5-6 semantic colors max)

### 6. Density & Readability

Check for:
- [ ] Appropriate information density for the domain
- [ ] Columns precisely aligned
- [ ] No wasted vertical space
- [ ] No overwhelming clutter
- [ ] Visual hierarchy (bold -> normal -> dim)
- [ ] Whitespace used for grouping, not decoration
- [ ] Single-character status flags where appropriate (M, A, D)
- [ ] Truncation with `...` rather than wrapping

**Exemplar benchmark**: htop maximizes vertical density. lazygit uses
single-letter status flags (`M`, `A`, `D`) for file status.

### 7. Unix Composability

Check for:
- [ ] Scriptable outputs (plain text when piped)
- [ ] Detects TTY vs pipe and adjusts behavior
- [ ] No shell-hostile design (doesn't fight terminal settings)
- [ ] `--help` or equivalent discoverable
- [ ] Exit codes are meaningful
- [ ] Accepts stdin where relevant
- [ ] Configuration via files or env vars (not just interactive)

### 8. Framework-Specific Quality

**If Textual (Python)**:
- [ ] Uses `reactive` properties correctly (not manual state tracking)
- [ ] Uses `Worker` / `@work` for long-running operations
- [ ] Does not block the event loop
- [ ] Uses `ModalScreen` for destructive confirmations
- [ ] Uses `Footer` for key legend (auto-generated from BINDINGS)
- [ ] Focus styling implemented via CSS (`:focus-within`, border changes)
- [ ] Key bindings registered via BINDINGS (not raw key handling)
- [ ] CSS used for theming (not inline styles)
- [ ] Events handled via message system (not polling)

**If Bubble Tea (Go)**:
- [ ] Clean Model/Update/View separation
- [ ] No side effects in `Update` (only in Commands)
- [ ] Uses `tea.Batch` for concurrent commands
- [ ] Sub-models properly delegated in `Update`
- [ ] Lip Gloss styles use `AdaptiveColor` for terminal compatibility
- [ ] `help.Model` used for key legend
- [ ] Single binary, no runtime dependencies
- [ ] `tea.Cmd` used for all I/O (not direct calls in Update)

---

## Scoring

### Renaissance Alignment Score

Calculate the overall score as the average of all applicable dimensions (drop
framework-specific if not applicable).

| Score  | Rating                                    |
|--------|-------------------------------------------|
| 9-10   | Renaissance-grade terminal craftsmanship   |
| 7-8    | Strong but improvable                      |
| 5-6    | Functional but structurally weak           |
| 3-4    | Serious UX discipline issues               |
| 1-2    | Violates core terminal design principles   |

### Manifesto Alignment

For each of the ten manifesto principles, note whether the TUI:
- **Honors** the principle (green)
- **Partially honors** the principle (yellow)
- **Violates** the principle (red)

The ten principles:
1. Terminal Is Intentional
2. Power Without Obscurity
3. Feedback Is Respect
4. Density Is a Feature
5. Focus Is Sacred
6. Composability Over Completeness
7. Modes Must Be Visible
8. Safety Before Speed
9. Keyboard First
10. The Tool Should Disappear

---

## Output Format

Every review must follow this structure:

### 1. Executive Summary
2-3 sentences: what the tool does well, what needs work, overall score.

### 2. Dimension Scores

| Dimension              | Score | Key Finding                              |
|------------------------|-------|------------------------------------------|
| Structure              | X/10  | One-line summary                         |
| Focus Management       | X/10  | One-line summary                         |
| Interaction Grammar    | X/10  | One-line summary                         |
| Safety                 | X/10  | One-line summary                         |
| Color & Semantics      | X/10  | One-line summary                         |
| Density & Readability  | X/10  | One-line summary                         |
| Unix Composability     | X/10  | One-line summary                         |
| Framework Quality      | X/10  | One-line summary (if applicable)         |
| **Overall**            | **X/10** | **Renaissance Alignment**             |

### 3. Detailed Findings

For each dimension, provide:
- Specific observations (good and bad)
- References to exemplar tools (lazygit, htop) for comparison
- Concrete code or design suggestions where applicable

### 4. Manifesto Alignment

Table showing honor/partial/violate for each of the 10 principles.

### 5. Top 5 Improvement Actions

Prioritized, actionable list. Each item includes:
1. **What to fix** (specific and concrete)
2. **Why it matters** (which principle or dimension it addresses)
3. **How to fix it** (implementation suggestion)

---

## Review References

For detailed standards, consult the TUI Designer skill's reference materials:
- [ASCII Design Patterns](../design:tui-designer/references/ascii-patterns.md)
- [TUI Style Guide](../design:tui-designer/references/style-guide.md)
- [Renaissance Manifesto](../design:tui-designer/references/manifesto.md)
- [Textual Framework Reference](../design:tui-designer/references/textual-reference.md)
- [Bubble Tea Framework Reference](../design:tui-designer/references/bubbletea-reference.md)

---

## Example Review Snippet

```
## Executive Summary

This file manager TUI provides solid tree navigation and clean column alignment
but suffers from invisible focus states and missing destructive confirmations.
Overall Renaissance Alignment: 5.5/10.

## Top 5 Improvement Actions

1. **Add visible focus indicator** -- Active pane needs a brighter border.
   Addresses: Focus Is Sacred (Principle 5). Fix: Use `:focus-within` CSS
   border in Textual or bright border style in Lip Gloss.

2. **Add delete confirmation** -- `d` key immediately removes files.
   Addresses: Safety Before Speed (Principle 8). Fix: Show modal dialog
   with y/N prompt before deletion.

3. **Add key legend** -- No visible keybindings on screen.
   Addresses: Power Without Obscurity (Principle 2). Fix: Add Footer
   widget with BINDINGS in Textual.

4. **Fix column alignment** -- File sizes are misaligned in the table.
   Addresses: Density Is a Feature (Principle 4). Fix: Right-align
   numeric columns, right-pad text columns.

5. **Support Esc to cancel** -- Esc does nothing in filter mode.
   Addresses: Keyboard First (Principle 9). Fix: Bind Esc to clear
   filter and return to normal mode.
```
