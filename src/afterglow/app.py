import webbrowser

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from afterglow.auth import AuthError, Session, start_login
from afterglow.config import Config

CSS = """
Screen {
    align: center middle;
}

#card {
    width: 60;
    height: auto;
    padding: 1 2;
    border: round $accent;
}

.title {
    text-style: bold;
    color: $accent;
    width: 100%;
    content-align: center middle;
}

.muted {
    color: $text-muted;
    width: 100%;
    content-align: center middle;
    margin-top: 1;
}

#login {
    width: 100%;
    margin-top: 1;
}

#link {
    color: $text-muted;
    margin-top: 1;
    display: none;
}

#link.visible {
    display: block;
}
"""


class LoginScreen(Screen):
    BINDINGS = [("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("afterglow", classes="title")
            yield Static("connect your spotify account to get started", classes="muted", id="status")
            yield Button("Log in with Spotify", id="login", variant="primary")
            yield Static("", id="link")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "login":
            self.begin_login()

    def begin_login(self) -> None:
        request = start_login(self.app.session.config)
        button = self.query_one("#login", Button)
        button.disabled = True
        button.label = "Waiting for Spotify…"
        self.query_one("#status", Static).update("approve access in your browser")
        link = self.query_one("#link", Static)
        link.update(f"browser didn't open? visit:\n{request.url}")
        link.add_class("visible")
        webbrowser.open(request.url)
        self.complete_login(request)

    @work(exclusive=True)
    async def complete_login(self, request) -> None:
        try:
            await self.app.session.login(request)
        except AuthError as error:
            self.query_one("#status", Static).update(f"[red]{error}[/]")
            button = self.query_one("#login", Button)
            button.disabled = False
            button.label = "Try again"
            return
        self.app.switch_screen(HomeScreen())


class HomeScreen(Screen):
    BINDINGS = [("q", "app.quit", "Quit"), ("L", "logout", "Log out")]

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("afterglow", classes="title")
            yield Static("you're in ✨", classes="muted")
        yield Footer()

    def action_logout(self) -> None:
        self.app.session.logout()
        self.app.switch_screen(LoginScreen())


class AfterglowApp(App):
    TITLE = "Afterglow"
    CSS = CSS

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.session = Session(config)

    def on_mount(self) -> None:
        self.push_screen(HomeScreen() if self.session.logged_in else LoginScreen())
