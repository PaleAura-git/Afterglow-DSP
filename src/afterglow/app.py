from textual.app import App, ComposeResult
from textual.containers import Center, Middle
from textual.widgets import Footer, Static

from afterglow.config import Config


class AfterglowApp(App):
    TITLE = "Afterglow"
    CSS = """
    Screen {
        align: center middle;
    }

    #title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        width: auto;
    }

    #status {
        color: $text-muted;
        content-align: center middle;
        width: auto;
        margin-top: 1;
    }
    """
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                yield Static("afterglow", id="title")
            with Center():
                yield Static(
                    f"client {self.config.client_id[:6]}… ready",
                    id="status",
                )
        yield Footer()
