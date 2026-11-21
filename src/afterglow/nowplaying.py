from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from afterglow.spotify import Playback

REPEAT_LABELS = {"off": "off", "context": "all", "track": "one"}


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


class NowPlaying(Widget):
    DEFAULT_CSS = """
    NowPlaying {
        height: 7;
        width: 100%;
    }
    """

    playback: reactive[Playback | None] = reactive(None, always_update=True)

    def render(self) -> Text:
        accent = self.app.current_theme.accent or "white"
        muted = "dim"
        playback = self.playback

        if playback is None:
            return Text.assemble(
                ("nothing playing", "bold"),
                "\n",
                ("start something on spotify, or press space to resume", muted),
            )

        width = self.size.width
        position = playback.position_ms
        elapsed, total = format_time(position), format_time(playback.duration_ms)
        bar = progress_bar(position, playback.duration_ms, width - len(elapsed) - len(total) - 2)

        state = "playing" if playback.is_playing else "paused"
        volume = "-" if playback.volume is None else f"{playback.volume}%"
        status = (
            f"{state}  |  shuffle {'on' if playback.shuffle else 'off'}"
            f"  |  repeat {REPEAT_LABELS.get(playback.repeat, playback.repeat)}"
            f"  |  vol {volume}"
        )

        return Text.assemble(
            (playback.track, f"bold {accent}"),
            "\n",
            (playback.artists, ""),
            "\n",
            (playback.album, muted),
            "\n\n",
            (f"{elapsed} {bar} {total}", ""),
            "\n\n",
            (status, muted),
            no_wrap=True,
            overflow="ellipsis",
        )
