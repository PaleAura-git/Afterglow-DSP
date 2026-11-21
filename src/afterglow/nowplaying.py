from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget

from afterglow.cover import Cover
from afterglow.spotify import Playback, Track
from afterglow.scope import Scope
from afterglow.state import load_state, save_state
from afterglow.visualizer import Visualizer

VIZ_MODES = ("scope", "bars")
REPEAT_LABELS = {"off": "off", "context": "all", "track": "one"}
UP_NEXT_COUNT = 5


def format_time(ms: int) -> str:
    seconds = max(0, ms) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def progress_bar(position_ms: int, duration_ms: int, width: int) -> str:
    if width <= 2 or duration_ms <= 0:
        return ""
    inner = width - 2
    filled = round(inner * min(position_ms / duration_ms, 1))
    if filled >= inner:
        return "[" + "=" * inner + "]"
    return "[" + "=" * filled + ">" + "-" * (inner - filled - 1) + "]"


def status_line(playback: Playback) -> str:
    state = "playing" if playback.is_playing else "paused"
    volume = "-" if playback.volume is None else f"{playback.volume}%"
    repeat = REPEAT_LABELS.get(playback.repeat, playback.repeat)
    return f"{state}  shuffle {'on' if playback.shuffle else 'off'}  repeat {repeat}  vol {volume}"


class PlaybackWidget(Widget):
    playback: reactive[Playback | None] = reactive(None, always_update=True)

    def accent(self) -> str:
        return self.app.current_theme.accent or "white"


class TrackInfo(PlaybackWidget):
    DEFAULT_CSS = """
    TrackInfo {
        height: auto;
        width: 1fr;
    }
    """

    def render(self) -> Text:
        playback = self.playback
        if playback is None:
            return Text.assemble(
                ("nothing playing", "bold"),
                "\n\n",
                ("pick something in the library (2),\nor press space to resume", "dim"),
            )
        return Text.assemble(
            (playback.track, f"bold {self.accent()}"),
            "\n",
            (playback.artists, ""),
            "\n",
            (playback.album, "dim"),
            no_wrap=True,
            overflow="ellipsis",
        )


class UpNext(Widget):
    DEFAULT_CSS = """
    UpNext {
        height: auto;
        width: 1fr;
        margin-top: 2;
    }
    """

    tracks: reactive[list[Track]] = reactive(list, always_update=True, layout=True)

    def render(self) -> Text:
        if not self.tracks:
            return Text("")
        text = Text("up next", style="dim bold", no_wrap=True)
        for track in self.tracks[:UP_NEXT_COUNT]:
            text.append("\n")
            text.append(track.name, style="")
            text.append(f"  {track.artists}  {format_time(track.duration_ms)}", style="dim")
        text.overflow = "ellipsis"
        return text


class ProgressLine(PlaybackWidget):
    DEFAULT_CSS = """
    ProgressLine {
        height: 1;
        width: 1fr;
    }
    """

    def render(self) -> Text:
        playback = self.playback
        if playback is None:
            return Text("")
        position = playback.position_ms
        elapsed, total = format_time(position), format_time(playback.duration_ms)
        width = self.size.width - len(elapsed) - len(total) - 2
        return Text(f"{elapsed} {progress_bar(position, playback.duration_ms, width)} {total}", no_wrap=True)


class NowPlayingView(Vertical):
    DEFAULT_CSS = """
    NowPlayingView {
        padding: 1 2;
    }

    NowPlayingView #stage {
        height: 1fr;
    }

    NowPlayingView Cover {
        height: 100%;
        width: 40;
    }

    NowPlayingView #details {
        width: 1fr;
        height: 100%;
        padding-left: 3;
    }

    NowPlayingView Visualizer, NowPlayingView Scope {
        margin-top: 2;
    }

    NowPlayingView ProgressLine {
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="stage"):
            yield Cover(id="big-cover")
            with Vertical(id="details"):
                yield TrackInfo()
                yield UpNext()
                yield Scope()
                yield Visualizer()
        yield ProgressLine()
        yield StatusLine()

    def on_mount(self) -> None:
        saved = load_state().get("viz_mode")
        self.viz_mode = saved if saved in VIZ_MODES else VIZ_MODES[0]
        self.call_after_refresh(self.apply_viz)

    async def apply_viz(self) -> None:
        scope, bars = self.query_one(Scope), self.query_one(Visualizer)
        scope.display = self.viz_mode == "scope"
        bars.display = self.viz_mode == "bars"
        if self.viz_mode == "scope":
            await bars.stop()
            scope.start()
        else:
            await scope.stop()
            bars.start()

    async def cycle_viz(self) -> None:
        self.viz_mode = VIZ_MODES[(VIZ_MODES.index(self.viz_mode) + 1) % len(VIZ_MODES)]
        save_state({"viz_mode": self.viz_mode})
        await self.apply_viz()

    def on_resize(self) -> None:
        stage = self.query_one("#stage")
        cover = self.query_one(Cover)
        height = stage.size.height
        if height:
            cover.styles.width = min(height * 2, max(stage.size.width // 2, 20))


class StatusLine(PlaybackWidget):
    DEFAULT_CSS = """
    StatusLine {
        height: 1;
        width: 1fr;
        color: $text-muted;
    }
    """

    def render(self) -> Text:
        return Text(status_line(self.playback) if self.playback else "", no_wrap=True)


class MiniPlayer(PlaybackWidget):
    DEFAULT_CSS = """
    MiniPlayer {
        height: 2;
        width: 100%;
        padding: 0 1;
    }
    """

    def render(self) -> Text:
        playback = self.playback
        if playback is None:
            return Text("nothing playing", style="dim")
        position = playback.position_ms
        elapsed, total = format_time(position), format_time(playback.duration_ms)
        width = self.size.width - len(elapsed) - len(total) - 4
        return Text.assemble(
            (playback.track, f"bold {self.accent()}"),
            ("  ", ""),
            (playback.artists, ""),
            ("  ", ""),
            (status_line(playback), "dim"),
            "\n",
            f"{elapsed} {progress_bar(position, playback.duration_ms, width)} {total}",
            no_wrap=True,
            overflow="ellipsis",
        )
