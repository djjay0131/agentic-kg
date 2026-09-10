# Bubble Tea Framework Reference

Complete capability map for designing TUIs with Bubble Tea (Go)
and the Charmbracelet ecosystem.

---

## The Elm Architecture

Bubble Tea uses a functional Model-Update-View architecture.

```go
type Model interface {
    Init() tea.Cmd                      // Initial command
    Update(tea.Msg) (Model, tea.Cmd)    // Handle messages, return new state
    View() string                       // Render entire UI as a string
}
```

### Data Flow
```
User Input -> Msg -> Update(model, msg) -> (newModel, cmd) -> View(newModel) -> Screen
                         ^                                          |
                         |     cmd runs in goroutine                |
                         +---- produces Msg -------------------------+
```

- **Model**: Single struct holding all application state
- **Update**: Pattern-matches on `tea.Msg` types, returns new model + optional command
- **View**: Renders complete UI as a string on every cycle (framework diffs efficiently)
- **Init**: Returns the first command to execute on startup

### Commands
A `Cmd` is `func() tea.Msg` -- performs I/O in a goroutine and returns a message.

| Pattern                  | Description                                    |
|--------------------------|------------------------------------------------|
| `tea.Cmd`                | Single async operation                         |
| `tea.Batch(cmds...)`     | Run multiple commands concurrently             |
| `tea.Sequence(cmds...)`  | Run commands sequentially                      |
| `tea.Quit`               | Signal program exit                            |
| `tea.ClearScreen`        | Clear the terminal                             |
| `tea.EnterAltScreen`     | Enter full-screen mode                         |
| `tea.ExitAltScreen`      | Exit full-screen mode                          |
| `tea.SetWindowTitle(s)`  | Set terminal window title                      |

---

## Bubbles Component Library

Pre-built `tea.Model` components designed for embedding/composition.

| Component    | Description                                                      |
|-------------|------------------------------------------------------------------|
| `textinput`  | Single-line text input with unicode, paste, inline scrolling     |
| `textarea`   | Multi-line text input with vertical scrolling                    |
| `table`      | Tabular data with columns, rows, vertical scrolling              |
| `list`       | Browsable list with fuzzy filtering, pagination, status, spinner |
| `viewport`   | Scrollable content viewport with keybindings + mouse wheel       |
| `spinner`    | Animated loading indicator (many styles)                         |
| `progress`   | Customizable progress bar with optional animation                |
| `paginator`  | Pagination (dot-style or numeric)                                |
| `filepicker` | Filesystem navigation with extension filtering                   |
| `timer`      | Countdown timer                                                  |
| `stopwatch`  | Incrementing counter                                             |
| `help`       | Auto-generated help view from keybindings                        |
| `key`        | Non-visual keybinding manager                                    |

### Composition Pattern
```go
type Model struct {
    list     list.Model      // embedded sub-model
    viewport viewport.Model  // embedded sub-model
    input    textinput.Model // embedded sub-model
}

func (m Model) Update(msg tea.Msg) (Model, tea.Cmd) {
    var cmds []tea.Cmd

    // Delegate to sub-models
    m.list, cmd = m.list.Update(msg)
    cmds = append(cmds, cmd)

    m.viewport, cmd = m.viewport.Update(msg)
    cmds = append(cmds, cmd)

    return m, tea.Batch(cmds...)
}
```

---

## Lip Gloss Styling

### Style Properties
```go
style := lipgloss.NewStyle().
    Bold(true).
    Foreground(lipgloss.Color("#FF00FF")).
    Background(lipgloss.Color("228")).
    Padding(1, 2).
    Margin(1, 2).
    Width(40).
    Align(lipgloss.Center).
    Border(lipgloss.RoundedBorder()).
    BorderForeground(lipgloss.Color("63"))
```

### Semantic Color System
```go
var (
    successStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("#2ecc71"))
    warningStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("#f39c12"))
    errorStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("#e74c3c"))
    selectStyle  = lipgloss.NewStyle().Foreground(lipgloss.Color("#3498db"))
    metaStyle    = lipgloss.NewStyle().Foreground(lipgloss.Color("#95a5a6"))
)
```

### Adaptive Colors
```go
lipgloss.AdaptiveColor{Light: "236", Dark: "248"}  // auto-adapts to terminal
lipgloss.CompleteColor{TrueColor: "#FF00FF", ANSI256: "205", ANSI: "13"}
```

### Layout Functions
```go
lipgloss.JoinHorizontal(lipgloss.Top, left, center, right)
lipgloss.JoinVertical(lipgloss.Left, top, middle, bottom)
lipgloss.Width(str)   // measure rendered width
lipgloss.Height(str)  // measure rendered height
```

### Border Styles
`NormalBorder()`, `RoundedBorder()`, `ThickBorder()`, `DoubleBorder()`,
`HiddenBorder()`, plus custom definitions.

### Sub-packages
| Package          | Description                                            |
|------------------|--------------------------------------------------------|
| `lipgloss/table` | Styled tables with per-cell style functions            |
| `lipgloss/list`  | Nested list rendering with custom enumerators          |
| `lipgloss/tree`  | Tree rendering with custom connectors                  |

---

## Pattern-to-Component Mapping

| ASCII Pattern            | Bubble Tea Implementation                             |
|--------------------------|-------------------------------------------------------|
| Master-Detail Split      | `list.Model` + `viewport.Model` joined horizontally   |
| Full-Screen Table        | `table.Model` with custom row styles                  |
| Filter-as-You-Type       | `list.Model` (built-in fuzzy filter) or custom        |
| Tree Navigation          | Custom model with `lipgloss/tree` rendering           |
| Tab Navigation           | Custom model with tab state + content switching        |
| Command Mode Overlay     | `textinput.Model` toggled by `:` key                  |
| Modal Confirmation       | State-based overlay rendering in `View()`             |
| Help Overlay             | `help.Model` (auto-generates from keybindings)        |
| Loading State            | `spinner.Model`                                       |
| Progress                 | `progress.Model`                                      |
| Status Strip             | Custom string in `View()` at bottom                   |
| Key Legend               | `help.Model` in short mode, or custom string          |

---

## Broader Charmbracelet Ecosystem

| Library      | Description                                          |
|-------------|------------------------------------------------------|
| **Bubble Tea** | Core TUI framework (Elm architecture)             |
| **Bubbles**    | Pre-built TUI components                          |
| **Lip Gloss**  | Styling and layout                                |
| **Harmonica**  | Spring-based animation                            |
| **BubbleZone** | Mouse event tracking zones                        |
| **ntcharts**   | Terminal charting                                 |
| **Glamour**    | Stylesheet-based Markdown rendering               |
| **Gum**        | Shell script interface to Charm tools             |
| **Termenv**    | Terminal environment detection and colors         |
| **Reflow**     | ANSI-aware text wrapping                          |

---

## Architectural Differences from Textual

| Aspect         | Textual (Python)                    | Bubble Tea (Go)                     |
|----------------|-------------------------------------|--------------------------------------|
| Architecture   | OOP widget tree with DOM/CSS        | Functional Elm: Model/Update/View    |
| Rendering      | Widget-level incremental updates    | Full string re-render (framework diffs) |
| Styling        | CSS files (TCSS) with selectors     | Programmatic Lip Gloss styles        |
| Layout         | CSS Grid, flexbox-like containers   | Manual string joining                |
| Components     | Built-in widget classes             | Composable `tea.Model` sub-models    |
| State          | Reactive attributes with watchers   | Single model struct, replaced each cycle |
| Async          | Workers (@work decorator, threads)  | Commands (tea.Cmd) in goroutines     |
| Events         | Message bubbling through DOM        | Pattern matching on tea.Msg types    |
| Screens        | Screen stack with push/pop/modal    | No built-in (community patterns)     |
| Theming        | Built-in theme system + CSS vars    | Manual via AdaptiveColor             |
| Distribution   | pip install                         | Single static binary                 |

### When to Choose Which

**Choose Textual when**:
- Rapid prototyping is needed
- Complex layouts with many panes
- Rich CSS-based theming desired
- Web deployment via Textual-Web is attractive
- Team knows Python

**Choose Bubble Tea when**:
- Single-binary distribution required
- Maximum performance needed
- Functional programming style preferred
- Simpler, flatter component model desired
- Team knows Go
