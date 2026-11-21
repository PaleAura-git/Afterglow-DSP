import asyncio
import webbrowser

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from afterglow.auth import SPARKLE, AuthError, Session, start_login
from afterglow.config import Config
from afterglow import engine
from afterglow.cover import Cover
from afterglow.nowplaying import ProgressLine, TrackInfo
from afterglow.spotify import REPEAT_CYCLE, Spotify, SpotifyError

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
    width: 80;
    height: auto;
    max-height: 100%;
    padding: 1 2;
    border: solid $accent;
}

#user {
    color: $text-muted;
    margin-bottom: 1;
}

#player {
    height: 10;
    margin-top: 1;
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


POLL_SECONDS = 2.5
TICK_SECONDS = 0.5
SEEK_MS = 5000
VOLUME_STEP = 5


class HomeScreen(Screen):
    BINDINGS = [
        ("space", "toggle_play", "Play/Pause"),
        ("h", "seek(-1)", "-5s"),
        ("l", "seek(1)", "+5s"),
        ("j", "volume(-1)", "Vol-"),
        ("k", "volume(1)", "Vol+"),
        ("n", "next", "Next"),
        ("p", "previous", "Prev"),
        ("s", "shuffle", "Shuffle"),
        ("r", "repeat", "Repeat"),
        ("a", "art_mode", "Art"),
        ("c", "reconnect", "Reconnect"),
        ("L", "logout", "Log out"),
        ("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="home"):
            yield Static("afterglow", classes="title")
            yield Static("", id="user")
            with Horizontal(id="player"):
                yield Cover()
                yield TrackInfo()
            yield ProgressLine()
            yield Static("connecting...", id="message")
        yield Footer()

    def on_mount(self) -> None:
        self.device_id: str | None = None
        self.info = self.query_one(TrackInfo)
        self.progress = self.query_one(ProgressLine)
        self.cover = self.query_one(Cover)
        self.load_user()
        self.connect_engine()
        self.set_interval(POLL_SECONDS, self.poll)
        self.set_interval(TICK_SECONDS, self.progress.refresh)

    @property
    def playback(self):
        return self.info.playback

    def redraw(self) -> None:
        self.info.refresh()
        self.progress.refresh()

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
            self.show_message(f"[red]{error}[/]  c: retry")
            return
        self.device_id = device.id
        self.show_message(f"on {device.name}")
        self.poll()

    @work(exclusive=True, group="poll")
    async def poll(self) -> None:
        try:
            playback = await self.app.spotify.playback()
        except (SpotifyError, AuthError):
            return
        self.info.playback = playback
        self.progress.playback = playback
        self.cover.show(playback.art_url if playback else "")

    @work(group="control")
    async def control(self, call, *args) -> None:
        try:
            await call(*args)
        except (SpotifyError, AuthError) as error:
            self.show_message(f"[red]{error}[/]")
        await asyncio.sleep(0.4)
        self.poll()

    def action_toggle_play(self) -> None:
        playback = self.playback
        if playback and playback.is_playing:
            playback.moved_to(playback.position_ms)
            playback.is_playing = False
            self.redraw()
            self.control(self.app.spotify.pause)
        else:
            if playback:
                playback.is_playing = True
                playback.moved_to(playback.progress_ms)
                self.redraw()
            self.control(self.app.spotify.play, self.device_id)

    def action_seek(self, direction: int) -> None:
        playback = self.playback
        if not playback:
            return
        target = playback.position_ms + direction * SEEK_MS
        playback.moved_to(target)
        self.redraw()
        self.control(self.app.spotify.seek, playback.progress_ms)

    def action_volume(self, direction: int) -> None:
        playback = self.playback
        if not playback or playback.volume is None:
            return
        playback.volume = max(0, min(100, playback.volume + direction * VOLUME_STEP))
        self.redraw()
        self.control(self.app.spotify.set_volume, playback.volume)

    def action_next(self) -> None:
        self.control(self.app.spotify.next)

    def action_previous(self) -> None:
        self.control(self.app.spotify.previous)

    def action_shuffle(self) -> None:
        playback = self.playback
        if not playback:
            return
        playback.shuffle = not playback.shuffle
        self.redraw()
        self.control(self.app.spotify.set_shuffle, playback.shuffle)

    def action_repeat(self) -> None:
        playback = self.playback
        if not playback:
            return
        playback.repeat = REPEAT_CYCLE.get(playback.repeat, "off")
        self.redraw()
        self.control(self.app.spotify.set_repeat, playback.repeat)

    def action_art_mode(self) -> None:
        self.cover.cycle_mode()

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
