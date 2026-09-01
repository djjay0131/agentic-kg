# Textual Framework Reference

Complete capability map for designing TUIs with Textual (Python).

---

## Layout Containers

All containers live in `textual.containers`.

| Container             | Layout     | Scrolling    | Expanding | Use Case                          |
|-----------------------|------------|-------------|-----------|-----------------------------------|
| `Container`           | Vertical   | None        | Yes       | Basic vertical wrapper            |
| `Horizontal`          | Horizontal | None        | Yes       | Side-by-side panes (master-detail)|
| `HorizontalGroup`     | Horizontal | None        | No        | Tight horizontal row of items     |
| `HorizontalScroll`    | Horizontal | X-axis auto | Yes       | Horizontally scrollable content   |
| `Vertical`            | Vertical   | None        | Yes       | Stacked sections                  |
| `VerticalGroup`       | Vertical   | None        | No        | Tight vertical stack              |
| `VerticalScroll`      | Vertical   | Y-axis auto | Yes       | Vertically scrollable content     |
| `Grid`                | Grid       | None        | Yes       | CSS Grid-based layout             |
| `ItemGrid`            | Grid       | None        | Yes       | Auto-calculated grid columns      |
| `ScrollableContainer` | Vertical   | Both axes   | Yes       | Full 2D scrolling                 |

**Key distinction**: `Horizontal`/`Vertical` expand to fill space (macro layouts).
`HorizontalGroup`/`VerticalGroup` fit to content (tight packing).

---

## Widget Catalog

### Input Widgets

| Widget          | Description                                                    |
|-----------------|----------------------------------------------------------------|
| `Button`        | Clickable button (default, primary, success, warning, error)   |
| `Checkbox`      | Toggle checkbox                                                |
| `Input`         | Single-line text input                                         |
| `MaskedInput`   | Input with template mask (phone, date)                         |
| `RadioButton`   | Single radio button                                            |
| `RadioSet`      | Radio button group (single selection)                          |
| `Select`        | Dropdown selection                                             |
| `SelectionList` | Multi-select list with checkboxes                              |
| `Switch`        | On/off toggle                                                  |
| `TextArea`      | Multi-line editor with syntax highlighting                     |

### Display Widgets

| Widget           | Description                                                   |
|------------------|---------------------------------------------------------------|
| `DataTable`      | Tabular data with cursor modes (cell, row, column)            |
| `Digits`         | Large number display                                          |
| `Label`          | Simple text label                                             |
| `Link`           | Clickable URL                                                 |
| `Log`            | Append-only scrolling text                                    |
| `Markdown`       | Rendered Markdown content                                     |
| `MarkdownViewer` | Markdown with TOC and navigation                              |
| `Placeholder`    | Design-time placeholder                                       |
| `Pretty`         | Pretty-formatted Rich renderable                              |
| `ProgressBar`    | Progress bar with ETA and percentage                          |
| `RichLog`        | Scrolling panel for Rich renderables                          |
| `Rule`           | Horizontal separator                                          |
| `Sparkline`      | Inline data visualization                                     |
| `Static`         | Simple static content (base class for custom widgets)         |

### Navigation & Structure Widgets

| Widget            | Description                                                  |
|-------------------|--------------------------------------------------------------|
| `Collapsible`     | Toggle-able content section                                  |
| `ContentSwitcher` | Switches between child widgets                               |
| `DirectoryTree`   | File/folder tree browser                                     |
| `Footer`          | Displays active key bindings (KEY LEGEND)                    |
| `Header`          | App title and subtitle bar                                   |
| `ListView`        | Vertical list of widget items                                |
| `LoadingIndicator`| Animated loading spinner                                     |
| `OptionList`      | Selectable option list (supports Rich renderables)           |
| `Tabs`            | Row of selectable tabs                                       |
| `TabbedContent`   | Tabs + ContentSwitcher combined                              |
| `Tree`            | General-purpose expandable tree                              |
| `Toast`           | Notification popup (via `app.notify()`)                      |

---

## Pattern-to-Widget Mapping

| ASCII Pattern            | Textual Implementation                           |
|--------------------------|--------------------------------------------------|
| Master-Detail Split      | `Horizontal` + `ListView`/`DataTable` + `Static` |
| Full-Screen Table        | `DataTable` with row cursor mode                 |
| Tree Navigation          | `Tree` or `DirectoryTree`                        |
| Tab Navigation           | `TabbedContent` or `Tabs` + `ContentSwitcher`    |
| Filter-as-You-Type       | `Input` + reactive filter on `ListView`           |
| Status Strip             | `Footer` or custom `Static` in dock              |
| Key Legend               | `Footer` (auto-generated from BINDINGS)          |
| Command Mode Overlay     | `Input` widget shown/hidden at bottom            |
| Modal Confirmation       | `ModalScreen` subclass                           |
| Help Overlay             | `ModalScreen` with `Markdown` or `Static`        |
| Loading State            | `LoadingIndicator` or `widget.loading = True`    |
| Log/Output Viewer        | `RichLog` or `Log`                               |
| Progress                 | `ProgressBar`                                    |

---

## Screen System

### Screen Types
- **`Screen`** -- Full terminal screen, one active at a time
- **`ModalScreen`** -- Blocks parent, shows overlay with transparency
- **`SystemModalScreen`** -- System-level dialogs

### Screen Stack
```python
app.push_screen(screen, callback)     # Push onto stack
app.push_screen_wait(screen)          # Async push, await result
app.pop_screen()                      # Pop top screen
app.switch_screen(screen)             # Replace top screen
screen.dismiss(result)                # Pop and return data
```

### Modes
Independent screen stacks for different app contexts:
```python
MODES = {"dashboard": DashboardScreen, "settings": SettingsScreen}
app.switch_mode("settings")
```

---

## Key Binding System

### Declaration
```python
class MyApp(App):
    BINDINGS = [
        ("d", "toggle_dark", "Dark mode"),           # tuple shorthand
        Binding("ctrl+q", "quit", "Quit", priority=True),  # full object
    ]

    def action_toggle_dark(self) -> None:
        self.theme = "dark" if self.theme == "light" else "light"
```

### Properties
- `key` -- the key combination
- `action` -- action method name (without `action_` prefix)
- `description` -- shown in Footer
- `show` -- whether to display in Footer
- `priority` -- checked before DOM traversal
- `key_display` -- custom display label

### Resolution Order
1. Priority bindings: focused widget -> ancestors -> Screen -> App
2. Normal bindings: focused widget -> ancestors -> Screen -> App

### Dynamic Bindings
Override `check_action()` to conditionally enable/disable based on state.

---

## Reactive State System

```python
from textual.reactive import reactive, var

class MyWidget(Widget):
    count = reactive(0)                    # triggers refresh on change
    name = reactive("", layout=True)       # triggers layout recalc
    data = var([])                          # no auto-refresh

    def validate_count(self, value: int) -> int:
        return max(0, min(100, value))     # clamp

    def watch_count(self, old: int, new: int) -> None:
        self.log(f"Count: {old} -> {new}")  # side effects

    def compute_full_name(self) -> str:
        return f"{self.first} {self.last}"  # derived state
```

### Advanced
- `set_reactive()` -- assign without triggering watchers
- `mutate_reactive()` -- notify changes to mutable objects
- `data_bind()` -- one-way binding from parent to child
- `recompose=True` -- re-run `compose()` on change

---

## Worker / Async System

```python
# Async worker (for async I/O)
@work(exclusive=True)
async def fetch_data(self) -> None:
    result = await httpx.AsyncClient().get(url)
    self.data = result.json()

# Thread worker (for blocking I/O)
@work(thread=True)
def heavy_computation(self) -> None:
    result = expensive_sync_call()
    self.call_from_thread(self.update_ui, result)  # safe UI update
```

### Rules
- Never modify widgets directly from a thread worker
- Use `app.call_from_thread()` for safe UI updates from threads
- `exclusive=True` cancels previous worker with same name
- Monitor via `on_worker_state_changed` event

---

## CSS Styling (TCSS)

### Focus Styling
```css
/* Active pane gets bright border */
.pane:focus-within {
    border: heavy $accent;
}

/* Inactive pane gets dim border */
.pane {
    border: round $surface-lighten-2;
}

/* Selection highlight */
ListView > ListItem.-selected {
    background: $primary;
    color: $text;
}
```

### Semantic Color Classes
```css
.success { color: $success; }
.warning { color: $warning; }
.error   { color: $error; }
.muted   { color: $text-muted; }
```

### Layout Example
```css
/* Master-detail split */
#main {
    layout: horizontal;
}

#list-pane {
    width: 1fr;
    min-width: 20;
}

#detail-pane {
    width: 2fr;
}

/* Footer key legend */
Footer {
    dock: bottom;
}
```

### Live Editing
```bash
textual run --dev app.py   # hot-reload CSS changes
```

---

## Event / Message System

- Handler convention: `on_<namespace>_<event>` (e.g., `on_button_pressed`)
- Custom messages: nested `Message` subclass + `self.post_message()`
- Events bubble up the DOM tree
- `@on(Widget.Event, "selector")` decorator for targeted handling

## Notifications

```python
app.notify("Operation complete", severity="information", timeout=5)
app.notify("Check your input", severity="warning")
app.notify("Connection failed", severity="error")
```

## Rich Integration

- Full support for Rich tables, panels, syntax highlighting, trees
- `RichLog` accepts any Rich renderable
- `TextArea` supports syntax highlighting for many languages
- `Markdown` widget renders Rich Markdown

## Animation

```python
widget.styles.animate("opacity", value=1.0, duration=0.5)
widget.loading = True  # shows animated loading indicator
```
