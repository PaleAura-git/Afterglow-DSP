import asyncio
import webbrowser
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, ContentSwitcher, Static

from afterglow.auth import SPARKLE, AuthError, Session, start_login
from afterglow.config import Config
from afterglow import engine
from afterglow.cover import Cover
from afterglow.help import HelpScreen
from afterglow.library import LibraryView
from afterglow.search import SearchView
from afterglow.theme import (
    DEFAULT_THEME_FILE,
    FALLBACK_THEME,
    ThemeWatcher,
    load_palette,
    load_theme,
    resolve_color,
)
from afterglow.nowplaying import MiniPlayer, NowPlayingView, PlaybackWidget, UpNext
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

#tabs {
    height: 1;
    padding: 0 1;
    background: $panel;
}

#views {
    height: 1fr;
}

.placeholder {
    width: 100%;
    height: 100%;
    content-align: center middle;
    color: $text-muted;
}

MiniPlayer {
    border-top: solid $panel;
    height: 3;
}

#message {
    height: 1;
    padding: 0 1;
    color: $text-muted;
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
        self.app.switch_screen(MainScreen())


POLL_SECONDS = 2.5
TICK_SECONDS = 0.5
SEEK_MS = 5000
VOLUME_STEP = 5
THEME_CHECK_SECONDS = 2.0
MESSAGE_SECONDS = 4.0
TABS = (("now-playing", "now playing"), ("library", "library"), ("search", "search"))


class MainScreen(Screen):
    BINDINGS = [
        ("1", "tab('now-playing')", "Now playing"),
        ("2", "tab('library')", "Library"),
        ("3", "tab('search')", "Search"),
        ("tab", "next_tab", "Next tab"),
        ("slash", "tab('search')", "Search"),
        ("space", "toggle_play", "Play/Pause"),
        ("n", "next", "Next"),
        ("p", "previous", "Prev"),
        ("minus", "volume(-1)", "Vol-"),
        ("equals_sign", "volume(1)", "Vol+"),
        ("comma", "seek(-1)", "-5s"),
        ("full_stop", "seek(1)", "+5s"),
        ("s", "shuffle", "Shuffle"),
        ("r", "repeat", "Repeat"),
        ("a", "art_mode", "Art"),
        ("v", "viz_mode", "Visualizer"),
        ("c", "reconnect", "Reconnect"),
        ("L", "logout", "Log out"),
        ("q", "app.quit", "Quit"),
        Binding("question_mark", "help", "Help", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static("", id="tabs")
        with ContentSwitcher(id="views", initial="now-playing"):
            yield NowPlayingView(id="now-playing")
            yield LibraryView(id="library")
            yield SearchView(id="search")
        yield MiniPlayer()
        yield Static("", id="message")

    def on_mount(self) -> None:
        self.device_id: str | None = None
        self.user_name = ""
        self.playback = None
        self.queue_for = ""
        self.message_timer = None
        self.switch_tab("now-playing")
        self.load_user()
        self.connect_engine()
        self.set_interval(POLL_SECONDS, self.poll)
        self.set_interval(TICK_SECONDS, self.redraw)

    def switch_tab(self, tab: str) -> None:
        self.query_one("#views", ContentSwitcher).current = tab
        self.query_one(MiniPlayer).display = tab != "now-playing"
        if tab == "library":
            self.query_one("#library #sections").focus()
        elif tab == "search":
            self.query_one(SearchView).activate()
        accent = self.app.current_theme.accent or "white"
        labels = [
            f"[bold {accent}]{index}:{label}[/]" if key == tab else f"[dim]{index}:{label}[/]"
            for index, (key, label) in enumerate(TABS, start=1)
        ]
        right = f"[dim]{self.user_name}[/]" if self.user_name else ""
        self.query_one("#tabs", Static).update("   ".join(labels) + ("   " + right if right else ""))

    def action_tab(self, tab: str) -> None:
        self.switch_tab(tab)

    def action_next_tab(self) -> None:
        keys = [key for key, _ in TABS]
        current = self.query_one("#views", ContentSwitcher).current
        self.switch_tab(keys[(keys.index(current) + 1) % len(keys)])

    def redraw(self) -> None:
        for widget in self.query(PlaybackWidget):
            widget.refresh()

    def set_playback(self, playback) -> None:
        self.playback = playback
        for widget in self.query(PlaybackWidget):
            widget.playback = playback
        self.query_one("#big-cover", Cover).show(playback.art_url if playback else "")
        uri = playback.uri if playback else ""
        if uri != self.queue_for:
            self.queue_for = uri
            self.load_queue()

    @work(exclusive=True, group="queue")
    async def load_queue(self) -> None:
        up_next = self.query_one(UpNext)
        if not self.queue_for:
            up_next.tracks = []
            return
        try:
            up_next.tracks = await self.app.spotify.queue()
        except (SpotifyError, AuthError):
            up_next.tracks = []

    @work(exclusive=True, group="user")
    async def load_user(self) -> None:
        try:
            user = await self.app.spotify.me()
        except (SpotifyError, AuthError) as error:
            self.show_message(f"[red]{error}[/]")
            return
        self.user_name = user.name
        self.query_one(LibraryView).user_id = user.id
        self.switch_tab(self.query_one("#views", ContentSwitcher).current)

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
        self.show_message("")
        self.poll()

    @work(exclusive=True, group="poll")
    async def poll(self) -> None:
        try:
            playback = await self.app.spotify.playback()
        except (SpotifyError, AuthError):
            return
        self.set_playback(playback)

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
        playback.moved_to(playback.position_ms + direction * SEEK_MS)
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

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    async def action_viz_mode(self) -> None:
        await self.query_one(NowPlayingView).cycle_viz()

    def action_art_mode(self) -> None:
        for cover in self.query(Cover):
            cover.cycle_mode()

    def show_message(self, text: str) -> None:
        message = self.query_one("#message", Static)
        message.update(text)
        message.display = bool(text)
        if self.message_timer:
            self.message_timer.stop()
            self.message_timer = None
        if text and "c: retry" not in text:
            self.message_timer = self.set_timer(MESSAGE_SECONDS, lambda: self.show_message(""))

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
        theme_file = Path(config.theme_file).expanduser() if config.theme_file else DEFAULT_THEME_FILE
        self.theme_watcher = ThemeWatcher(theme_file)
        self.palette: dict[str, str] = {}

    async def on_unmount(self) -> None:
        await self.spotify.close()

    def on_mount(self) -> None:
        self.sync_theme()
        self.set_interval(THEME_CHECK_SECONDS, self.sync_theme)
        self.push_screen(MainScreen() if self.session.logged_in else LoginScreen())

    def art_color(self) -> str:
        accent = self.current_theme.accent or "white"
        return resolve_color(self.session.config.art_color, self.palette, accent)

    def sync_theme(self) -> None:
        if not self.theme_watcher.changed():
            return
        self.palette = load_palette(self.theme_watcher.path)
        theme = load_theme(self.theme_watcher.path)
        if theme is None:
            self.theme = FALLBACK_THEME
            return
        self.register_theme(theme)
        self.theme = theme.name
        self.refresh_css()
        for cover in self.query(Cover):
            cover.draw()
        for screen in self.screen_stack:
            if isinstance(screen, MainScreen) and screen.is_mounted:
                screen.switch_tab(screen.query_one("#views", ContentSwitcher).current)
