"""Centralised styling for the Textual TUI.

Two layers live here:

``TUI_CSS``
    Textual CSS for the widget chrome (borders, focus highlight, input row,
    status bar, task rows).  Focus highlighting is a ``:focus-within`` rule
    instead of the old per-frame ``Frame`` rebuild.

``RICH_STYLES`` / :func:`rich_style`
    Rich style strings for text that is *rendered* inside a widget rather than
    laid out by CSS: task-tree node labels and status-bar segments.

``TaskTree`` reports its colours as ``"class:task.icon.run"`` tokens;
:func:`rich_style` strips the ``class:`` prefix and maps them, so
``task_tree.py`` needs no changes at all.

Palette: Catppuccin Mocha.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Textual CSS
# ---------------------------------------------------------------------------

TUI_CSS = """
Screen {
    background: $background;
    color: #cdd6f4;
}

/* ── body: log pane | task sidebar ──────────────────────────────────── */

/* Surfaces use theme variables (``$background`` / ``$surface``) so ``F4``
   actually repaints the application and not just Textual's own widgets.
   Foreground colours and borders stay fixed: every theme offered by the cycle
   is dark, so they remain legible on all of them, and the palette in
   ``RICH_STYLES`` below cannot read theme variables anyway (it is applied to
   Rich text built in Python, not laid out by CSS). */
#body {
    height: 1fr;
}

#log-frame {
    width: 7fr;
    border: round #4a4a6a;
    border-title-color: #8888cc;
    border-title-style: bold;
}
#log-frame:focus-within {
    border: round #a6e3a1;
    border-title-color: #a6e3a1;
}

#log {
    background: $background;
    scrollbar-size-vertical: 1;
    /* Lines wrap to the pane width; never scroll sideways. */
    overflow-x: hidden;
}

#task-frame {
    /* Only the starting width; the app clamps it to the terminal size. */
    width: 52;
    border: round #4a4a6a;
    border-title-color: #8888cc;
    border-title-style: bold;
}
#task-frame:focus-within {
    border: round #a6e3a1;
    border-title-color: #a6e3a1;
}

#task-tree {
    background: $background;
    scrollbar-size-vertical: 1;
    /* Rows wrap rather than widening the canvas. */
    overflow-x: hidden;
}
#task-tree:focus {
    background-tint: #1e1e2e 0%;
}

/* Shown in place of the list until the first task is registered; docked so it
   does not get pushed around by the (empty) list below it. */
#task-empty {
    dock: top;
    height: auto;
    padding: 1 1;
    color: #6c7086;
    background: $background;
}

/* One row per visible node: an arrow cell + a label cell that wraps. */
#task-tree > ListItem {
    layout: horizontal;
    height: auto;
    padding: 0 1;
    background: transparent;
}
#task-tree > ListItem > .arrow {
    width: 2;
    height: auto;
}
#task-tree > ListItem > .label {
    width: 1fr;
    height: auto;
}
#task-tree > ListItem.-hovered {
    background: $surface;
}
#task-tree > ListItem.-highlight {
    background: $surface;
    color: #cdd6f4;
}
#task-tree:focus > ListItem.-highlight {
    background: #89b4fa;
    color: #1e1e2e;
}

/* ── notifications ─────────────────────────────────────────────────── */
/* Stacked top-right rather than the default bottom-right: the bottom of this
   screen is the input row and the status bar — what the user is actively
   reading — while the top-right of the log pane scrolls and is cheap to
   cover.  (Toasts are also rate-limited in app.py: an unbounded burst used to
   bury the entire interface.) */
#textual-toastrack {
    align: right top;
}

/* ── log filter row (shown on demand with F3 or /) ─────────────────── */
#search-bar {
    height: 1;
    display: none;
    background: $surface;
}
#search-prefix {
    width: 2;
    content-align: center middle;
    color: #f9e2af;
    text-style: bold;
}
#search-input {
    border: none;
    height: 1;
    padding: 0 1;
    background: $surface;
}
#search-input:focus {
    border: none;
    background-tint: #313244 0%;
}
#search-input > .input--placeholder { color: #6c7086; }
#search-input > .input--cursor { background: #f9e2af; color: #1e1e2e; }
#search-input > .input--selection { background: #45475a; }

/* ── command input row ─────────────────────────────────────────────── */
#input-frame {
    /* Grows by one row while the completion hints are visible. */
    height: auto;
    border: round #4a4a6a;
    border-title-color: #8888cc;
}
#input-frame:focus-within {
    border: round #a6e3a1;
    border-title-color: #a6e3a1;
}
#input-row {
    height: 1;
}
#input-hints {
    height: 1;
    display: none;
    background: $background;
}

#input-prefix {
    width: 2;
    content-align: center middle;
    color: #89b4fa;
    text-style: bold;
}
#input-prefix.batch {
    width: 8;
    color: #fab387;
}

#command-input {
    border: none;
    height: 1;
    padding: 0 1;
    background: $background;
}
#command-input:focus {
    border: none;
    background-tint: #1e1e2e 0%;
}
#command-input > .input--placeholder { color: #6c7086; }
#command-input > .input--cursor { background: #89b4fa; color: #1e1e2e; }
#command-input > .input--selection { background: #45475a; }

/* ── status bar ────────────────────────────────────────────────────── */
#status-bar {
    height: 1;
    background: $background;
    color: #cdd6f4;
}

/* ── batch panel (modal) ───────────────────────────────────────────── */
BatchPanel {
    align: center middle;
    background: $background 60%;
}
#batch-dialog {
    width: 80%;
    height: 70%;
    border: round #fab387;
    border-title-color: #fab387;
    border-title-style: bold;
    background: $background;
    padding: 0 1;
}
#batch-hint {
    height: 1;
    color: #6c7086;
}
#batch-stats {
    height: 1;
    background: $background;
}
#batch-text {
    height: 1fr;
    border: none;
    background: $background;
}
#batch-text:focus { border: none; }

/* ── quality table (modal) ─────────────────────────────────────────── */
QualityScreen {
    align: center middle;
    background: $background 60%;
}
#quality-dialog {
    width: 92%;
    max-width: 140;
    height: 82%;
    border: round #89dceb;
    border-title-color: #89dceb;
    border-title-style: bold;
    background: $background;
    padding: 0 1;
}
#quality-hint {
    height: 1;
    color: #6c7086;
}
#quality-table {
    height: 1fr;
    background: $background;
}
#quality-table > .datatable--header {
    background: $surface;
    text-style: bold;
}
#quality-table > .datatable--cursor {
    background: #89b4fa;
    color: #1e1e2e;
}

/* ── help panel (modal) ────────────────────────────────────────────── */
HelpScreen {
    align: center middle;
    background: $background 60%;
}
#help-dialog {
    width: 86%;
    max-width: 108;
    height: 82%;
    border: round #89b4fa;
    border-title-color: #89b4fa;
    border-title-style: bold;
    background: $background;
    padding: 0 1;
}
#help-scroll {
    width: 1fr;
    height: 1fr;
    scrollbar-size-vertical: 1;
}
#help-body {
    width: 1fr;
    height: auto;
}

/* ── single-line prompt (modal) ────────────────────────────────────── */
PromptScreen {
    align: center middle;
    background: $background 60%;
}
#prompt-dialog {
    width: 60;
    max-width: 90%;
    height: auto;
    border: round #89b4fa;
    border-title-color: #89b4fa;
    border-title-style: bold;
    background: $background;
    padding: 0 1;
}
#prompt-label {
    /* Grows to fit: a fixed height silently truncated any prompt longer than
       the dialog is wide. */
    height: auto;
    color: #cdd6f4;
}
#prompt-input {
    border: none;
    height: 1;
    padding: 0;
    background: $background;
}
#prompt-input:focus { border: none; }
#prompt-input > .input--placeholder { color: #6c7086; }
#prompt-input > .input--cursor { background: #89b4fa; color: #1e1e2e; }
#prompt-input > .input--selection { background: #45475a; }
"""


# ---------------------------------------------------------------------------
# Rich styles (widget-internal text)
# ---------------------------------------------------------------------------

# Applied to Rich text that Python builds, so it cannot read Textual's CSS
# variables (`$background`, `$surface`) the way TUI_CSS does.  The palette is
# therefore switched explicitly — see `set_palette` — whenever the theme
# changes, or a light theme would leave dark-on-light text unreadable.
RICH_STYLES: dict[str, str] = {
    # task status icons
    "task.icon.run":      "bold #89b4fa",
    "task.icon.wait":     "#6c7086",
    "task.icon.done":     "#a6e3a1",
    "task.icon.exist":    "#6c7086",
    "task.icon.fail":     "bold #f38ba8",
    # task node kinds
    "task.kind.album":    "bold #cba6f7",
    "task.kind.playlist": "bold #89dceb",
    "task.kind.artist":   "bold #f9e2af",
    "task.kind.mv":       "bold #f38ba8",
    "task.kind.song":     "#cdd6f4",
    # task row chrome
    "task.heading":       "bold #cdd6f4",
    "task.progress":      "#6c7086",
    "task.error":         "#f38ba8",
    # log levels (mirrors loguru)
    "log.text":           "#cdd6f4",
    "log.time":           "#6c7086",
    "log.info":           "#89b4fa",
    "log.warning":        "#f9e2af",
    "log.error":          "#f38ba8",
    "log.success":        "#a6e3a1",
    "log.debug":          "#6c7086",
    "log.tag":            "bold #cba6f7",
    # status bar segments
    "tui.statusbar":          "#cdd6f4",
    "tui.statusbar.key":      "bold #89b4fa",
    "tui.statusbar.value":    "#a6e3a1",
    "tui.statusbar.region":   "bold #cba6f7",
    "tui.statusbar.scroll":   "bold #f38ba8",
    "tui.statusbar.batch":    "bold #fab387",
    "tui.statusbar.sep":      "#45475a",
    "tui.statusbar.spark":    "#89dceb",
    "tui.statusbar.progress": "bold #a6e3a1",
    "tui.statusbar.eta":      "#f9e2af",
    "tui.statusbar.filter":   "bold #f9e2af",
    "tui.statusbar.alert":    "bold #f38ba8",
    "tui.statusbar.pending":  "bold #89dceb",
    "tui.help.heading":       "bold #89b4fa",
    "tui.help.key":           "bold #a6e3a1",
    "tui.help.text":          "#cdd6f4",
    "tui.help.hint":          "#6c7086",
    "tui.batch.text":         "#cdd6f4",
    "tui.batch.ok":           "#a6e3a1",
    "tui.batch.warn":         "bold #f9e2af",
    "tui.batch.dim":          "#6c7086",
    "tui.input.prompt":       "bold #89b4fa",
    "tui.input.batch":        "bold #fab387",
    "tui.input.hint":         "#6c7086",
    "tui.input.hint.first":   "bold #89b4fa",
    "tui.input.hint.more":    "#45475a",
}

# Catppuccin Latte counterparts of the Mocha colours above, so the light themes
# in the F4 cycle stay readable.  Derived rather than hand-written: a new token
# only has to be added to the dark set, and the two cannot drift apart.
_LATTE: dict[str, str] = {
    "#cdd6f4": "#4c4f69",   # text
    "#6c7086": "#8c8fa1",   # overlay0
    "#89b4fa": "#1e66f5",   # blue
    "#a6e3a1": "#40a02b",   # green
    "#f38ba8": "#d20f39",   # red
    "#f9e2af": "#df8e1d",   # yellow
    "#cba6f7": "#8839ef",   # mauve
    "#89dceb": "#04a5e5",   # sky
    "#fab387": "#fe640b",   # peach
    "#f5c2e7": "#ea76cb",   # pink
    "#45475a": "#9ca0b0",   # surface1 -> overlay1
}

def _to_latte(value: str) -> str:
    """Replace every Mocha colour in *value* with its Latte counterpart."""
    for mocha, latte in _LATTE.items():
        if mocha in value:
            value = value.replace(mocha, latte)
    return value


LIGHT_STYLES: dict[str, str] = {
    token: _to_latte(value) for token, value in RICH_STYLES.items()
}

_palette: dict[str, str] = RICH_STYLES


def set_palette(dark: bool) -> None:
    """Choose the palette :func:`rich_style` draws from.

    Called when the theme changes: ``TUI_CSS`` follows Textual's own theme
    variables, but these styles are baked into Rich text that Python builds, so
    they have to be switched by hand.
    """
    global _palette
    _palette = RICH_STYLES if dark else LIGHT_STYLES


def rich_style(token: str) -> str:
    """Map a style token to a Rich style string.

    Accepts both the legacy prompt_toolkit form (``"class:task.icon.run"``,
    which is what ``TaskTree`` emits) and a bare token, returning ``""`` for
    unknown tokens so rendering never crashes on a typo.
    """
    if token.startswith("class:"):
        token = token[len("class:"):]
    return _palette.get(token, "")
