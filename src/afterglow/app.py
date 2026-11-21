import asyncio
import webbrowser

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from afterglow.auth import SPARKLE, AuthError, Session, start_login
from afterglow.config import Config
from afterglow import engine
from afterglow.spotify import Spotify, SpotifyError

CSS = """
Screen {
    align: center middle;
}

#card {
    width: 60;
    height: auto;
    padding: 1 2;
    border: solid $accent;
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

#sparkle {
    color: $accent;
    width: 100%;
    content-align: center middle;
    margin-top: 1;
    display: none;
}

#card.done #sparkle {
    display: block;
}

#card.done #status, #card.done #login, #card.done #link {
    display: none;
}

#home {
    width: 72;
    height: auto;
    max-height: 100%;
    padding: 1 2;
    border: solid $accent;
}

#user {
    color: $text-muted;
    margin-bottom: 1;
}

#message {
    color: $text-muted;
    margin-top: 1;
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
            yield Static(SPARKLE, id="sparkle")
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
        self.query_one("#card").add_class("done")
        await asyncio.sleep(1.5)
        self.app.switch_screen(HomeScreen())


class HomeScreen(Screen):
    BINDINGS = [
        ("q", "app.quit", "Quit"),
        ("r", "reconnect", "Reconnect"),
        ("L", "logout", "Log out"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="home"):
            yield Static("afterglow", classes="title")
            yield Static("", id="user")
            yield Static("connecting...", id="message")
        yield Footer()

    def on_mount(self) -> None:
        self.load_user()
        self.connect_engine()

    @work(exclusive=True, group="user")
    async def load_user(self) -> None:
        try:
            user = await self.app.spotify.me()
        except (SpotifyError, AuthError) as error:
            self.show_message(f"[red]{error}[/]")
            return
        self.query_one("#user", Static).update(f"logged in as {user.name}")

    @work(exclusive=True, group="engine")
    async def connect_engine(self) -> None:
        self.show_message("connecting...")
        try:
            device = await engine.connect(
                self.app.spotify, self.app.session.config, self.show_message
            )
        except (engine.EngineError, SpotifyError, AuthError) as error:
            self.show_message(f"[red]{error}[/]  r: retry")
            return
        self.show_message(f"ready on {device.name}")

    def show_message(self, text: str) -> None:
        self.query_one("#message", Static).update(text)

    def action_reconnect(self) -> None:
        self.connect_engine()

    def action_logout(self) -> None:
        self.app.session.logout()
        self.app.switch_screen(LoginScreen())


class AfterglowApp(App):
    TITLE = "Afterglow"
    CSS = CSS

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.session = Session(config)
        self.spotify = Spotify(self.session)

    async def on_unmount(self) -> None:
        await self.spotify.close()

    def on_mount(self) -> None:
        self.push_screen(HomeScreen() if self.session.logged_in else LoginScreen())
