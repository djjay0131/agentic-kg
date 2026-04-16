# TUI Review Quick Checklist

Quick-reference checklist for reviewing terminal user interfaces.
Use this as a rapid scan before the full dimensional review.

---

## Critical Path (Must Pass)

These are non-negotiable. Failure on any of these is an automatic flag.

- [ ] **Focus visible**: Can you always tell what has focus?
- [ ] **Help available**: Does `?` or `F1` show keybindings?
- [ ] **Escape works**: Does `Esc` cancel/go back in every context?
- [ ] **Quit works**: Does `q` exit the application?
- [ ] **Arrows work**: Do arrow keys navigate everywhere?
- [ ] **Destructive = confirmed**: Are delete/kill/reset actions confirmed?
- [ ] **No dead keys**: Does every meaningful keystroke produce visible feedback?

If any of these fail, stop and fix them before evaluating further.

---

## Structure Checklist

- [ ] Pane boundaries are clear (borders or significant whitespace)
- [ ] Status strip shows current mode/state
- [ ] Key legend visible at bottom
- [ ] Columns are precisely aligned
- [ ] Headers are visually distinct from data
- [ ] Scroll position is indicated when content overflows
- [ ] Layout is stable (doesn't jump on data changes)

## Focus Checklist

- [ ] Active element has bright/colored border or highlight
- [ ] Inactive elements are visually receded (dim border)
- [ ] Tab cycles through panes predictably
- [ ] Only one element receives input at a time
- [ ] Mode indicator shown when in non-default mode

## Interaction Checklist

- [ ] `j`/`k` work alongside arrow keys
- [ ] `g`/`G` jump to top/bottom
- [ ] `/` starts search or filter
- [ ] `Enter` is the primary action
- [ ] Key legend updates when focus changes
- [ ] No surprising keybindings

## Safety Checklist

- [ ] Destructive actions show confirmation dialog
- [ ] Default on confirmation is the safe option (N, not Y)
- [ ] Esc cancels any dialog
- [ ] Destructive dialogs use red/warning color
- [ ] Type-to-confirm for truly irreversible operations

## Color Checklist

- [ ] Green = success only
- [ ] Red = error/destructive only
- [ ] Yellow = warning only
- [ ] Blue = selection/focus only
- [ ] No decorative color
- [ ] Color is not the sole indicator (text/shape also used)
- [ ] Tested on both dark and light backgrounds

## Density Checklist

- [ ] No wasted vertical space
- [ ] Related items are grouped together
- [ ] Whitespace serves grouping, not decoration
- [ ] Text truncated with `...` instead of wrapping
- [ ] Single-character flags used where appropriate
- [ ] Numeric columns right-aligned

## Composability Checklist

- [ ] Tool detects TTY vs pipe
- [ ] Plain text output when piped
- [ ] Meaningful exit codes
- [ ] `--help` or equivalent exists
- [ ] Config via file or env vars supported

---

## Scoring Quick Reference

| Score  | Meaning                                   |
|--------|-------------------------------------------|
| 9-10   | Renaissance-grade craftsmanship            |
| 7-8    | Strong, some polish needed                 |
| 5-6    | Functional, structural weaknesses          |
| 3-4    | Significant UX discipline issues           |
| 1-2    | Core principles violated                   |

---

## Manifesto Quick Reference

| # | Principle                       | Key Question                                      |
|---|--------------------------------|---------------------------------------------------|
| 1 | Terminal Is Intentional         | Does the TUI leverage the terminal's strengths?    |
| 2 | Power Without Obscurity         | Can a newcomer be productive in 30 seconds?        |
| 3 | Feedback Is Respect             | Does every action produce visible response?         |
| 4 | Density Is a Feature            | Is information dense but hierarchical?             |
| 5 | Focus Is Sacred                 | Is the active element always obvious?               |
| 6 | Composability Over Completeness | Does it play well with shell tools?                |
| 7 | Modes Must Be Visible           | Is the current mode always displayed?              |
| 8 | Safety Before Speed             | Are destructive actions gated?                     |
| 9 | Keyboard First                  | Is everything accessible without a mouse?          |
| 10| The Tool Should Disappear       | Does the interface fade from awareness during use? |
