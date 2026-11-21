from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

SECTIONS = (
    (
        "playback",
        (
            ("space", "play / pause"),
            ("n  p", "next / previous"),
            ("-  =", "volume down / up"),
            (",  .", "seek -5s / +5s"),
            ("s", "shuffle"),
            ("r", "repeat: off, all, one"),
        ),
    ),
    (
        "view",
        (
            ("1  2  3", "now playing / library / search"),
            ("tab", "next tab"),
            ("/", "search"),
            ("a", "art: blocks / braille"),
            ("v", "visualizer: scope / bars"),
        ),
    ),
    (
        "lists",
        (
            ("j  k", "down / up"),
            ("h  l", "back / into"),
            ("g  G", "top / bottom"),
            ("enter", "play"),
            ("esc", "back to search box"),
        ),
    ),
    (
        "app",
        (
            ("c", "reconnect engine"),
            ("L", "log out"),
            ("q", "quit"),
            ("?", "this help"),
        ),
    ),
)


def help_table(accent: str) -> Table:
    table = Table.grid(padding=(0, 3))
    table.add_column(justify="right", style=f"bold {accent}", no_wrap=True)
    table.add_column(style="", no_wrap=True)
    for index, (title, rows) in enumerate(SECTIONS):
        if index:
            table.add_row("", "")
        table.add_row("", Text(title, style="dim"))
        for keys, description in rows:
            table.add_row(keys, description)
    return table


class HelpScreen(ModalScreen):
    BINDINGS = [
        Binding("escape,question_mark,q", "dismiss", "Close", show=False),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: $background 60%;
    }

    HelpScreen #help {
        width: 58;
        height: auto;
        padding: 1 3;
        border: solid $accent;
        background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Static(help_table(self.app.current_theme.accent or "white"))

    def action_dismiss(self) -> None:
        self.dismiss()
