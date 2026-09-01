"""Centralised prompt_toolkit Style for the TUI.

Catppuccin-Mocha-derived palette.  Widget code references tokens by
class name (``"class:task.icon.run"`` etc.); bare tokens in
formatted-text tuples crash the renderer (parsed as colour specs), so
always use the ``class:`` prefix.

The catalogue below documents every token and where it is emitted.
"""

from prompt_toolkit.styles import Style

# ---------------------------------------------------------------------------
# Token catalogue
# ---------------------------------------------------------------------------
# tui.border          – panel borders / titles
# tui.statusbar       – bottom status bar background
# tui.statusbar.key   – keyboard hint labels in the status bar
# tui.statusbar.value – metric values (speeds, counts) in the status bar
# tui.statusbar.region – available wrapper regions
# tui.statusbar.scroll – [SCROLL] indicator
# tui.statusbar.batch  – [BATCH] indicator
# tui.input.prompt    – the ">" prefix in the input bar
# tui.input.batch     – the "[BATCH]" prefix when batch mode is active
#
# task.*   – task-list tree colours
# task.icon.run   task.icon.wait  task.icon.done
# task.icon.exist task.icon.fail
# task.kind.album task.kind.playlist task.kind.artist task.kind.mv task.kind.song
# task.progress   – dl/dec byte counters
# task.error      – inline error text
# task.tree       – tree branch characters  ├─  └─  │
# task.heading    – parent node name
#
# log.*   – log-view line colours (mirrors loguru levels)
# log.time   log.info   log.warning   log.error   log.success   log.debug
# log.tag    – [SONG] / [ALBUM] / … identifier in log lines
# ---------------------------------------------------------------------------

TUI_STYLE = Style.from_dict({
    # ── borders ──────────────────────────────────────────────────────────────
    "frame.border":                 "#4a4a6a",
    "frame.label":                  "bold #8888cc",

    # ── status bar ────────────────────────────────────────────────────────────
    "tui.statusbar":                "bg:#1e1e2e #cdd6f4",
    "tui.statusbar.key":            "bg:#1e1e2e bold #89b4fa",
    "tui.statusbar.value":          "bg:#1e1e2e #a6e3a1",
    "tui.statusbar.region":         "bg:#1e1e2e bold #cba6f7",
    "tui.statusbar.scroll":         "bg:#1e1e2e bold #f38ba8",
    "tui.statusbar.batch":          "bg:#1e1e2e bold #fab387",
    "tui.statusbar.sep":            "bg:#1e1e2e #45475a",

    # ── input bar ────────────────────────────────────────────────────────────
    "tui.input.prompt":             "bold #89b4fa",
    "tui.input.batch":              "bold #fab387",

    # ── task icons ───────────────────────────────────────────────────────────
    "task.icon.run":                "bold #89b4fa",   # ▶ downloading/decrypting
    "task.icon.wait":               "#6c7086",        # ⏸ waiting
    "task.icon.done":               "#a6e3a1",        # ✓
    "task.icon.exist":              "#6c7086",        # ✓ already existed
    "task.icon.fail":               "bold #f38ba8",   # ✗

    # ── task node kinds ──────────────────────────────────────────────────────
    "task.kind.album":              "bold #cba6f7",
    "task.kind.playlist":           "bold #89dceb",
    "task.kind.artist":             "bold #f9e2af",
    "task.kind.mv":                 "bold #f38ba8",
    "task.kind.song":               "#cdd6f4",

    # ── task tree chrome ─────────────────────────────────────────────────────
    "task.tree":                    "#45475a",
    "task.heading":                 "bold #cdd6f4",
    "task.progress":                "#6c7086",
    "task.error":                   "#f38ba8",

    # ── log colours ──────────────────────────────────────────────────────────
    "log.time":                     "#6c7086",
    "log.info":                     "#89b4fa",
    "log.warning":                  "#f9e2af",
    "log.error":                    "#f38ba8",
    "log.success":                  "#a6e3a1",
    "log.debug":                    "#6c7086",
    "log.tag":                      "bold #cba6f7",
    "log.text":                     "#cdd6f4",

    # ── completion menu ──────────────────────────────────────────────────────
    "completion-menu.completion":           "bg:#313244 #cdd6f4",
    "completion-menu.completion.current":   "bg:#89b4fa #1e1e2e",
    "scrollbar.background":                 "bg:#313244",
    "scrollbar.button":                     "bg:#89b4fa",
})
