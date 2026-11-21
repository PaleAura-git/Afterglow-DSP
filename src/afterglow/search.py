import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.fuzzy import Matcher
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from afterglow.auth import AuthError
from afterglow.library import Column
from afterglow.nowplaying import format_time
from afterglow.spotify import Collection, Result, SpotifyError

DEBOUNCE_SECONDS = 0.3
LOCAL_LIMIT = 8
MIN_LOCAL_SCORE = 0.3
KIND_WIDTH = 9


def _row(result: Result) -> Text:
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{result.kind:<{KIND_WIDTH}}", style="dim")
    text.append(result.name)
    details = "  ".join(
        part
        for part in (
            result.subtitle,
            format_time(result.duration_ms) if result.duration_ms else "",
            "yours" if result.yours else "",
        )
        if part
    )
    if details:
        text.append(f"  {details}", style="dim")
    return text


def _from_collection(collection: Collection) -> Result:
    return Result(
        kind=collection.kind,
        name=collection.name,
        subtitle=collection.subtitle,
        uri=collection.uri,
        context_uri=collection.uri,
        yours=True,
    )


class SearchInput(Input):
    BINDINGS = [Binding("down", "app.focus_next", "Results", show=False)]


class Results(Column):
    BINDINGS = [Binding("escape", "focus_input", "Search box", show=False)]

    def render_row(self, item) -> Text:
        return _row(item)

    def action_cursor_up(self) -> None:
        if self.highlighted in (None, 0):
            self.action_focus_input()
            return
        super().action_cursor_up()

    def action_focus_input(self) -> None:
        self.screen.query_one(SearchInput).focus()


class SearchView(Vertical):
    DEFAULT_CSS = """
    SearchView {
        padding: 0 1;
    }

    SearchView SearchInput {
        border: solid $panel;
        padding: 0 1;
    }

    SearchView SearchInput:focus {
        border: solid $accent;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.local: list[Result] = []
        self.local_loaded = False

    def compose(self) -> ComposeResult:
        yield SearchInput(placeholder="search your library and spotify", id="query")
        yield Results("results", id="results")

    def activate(self) -> None:
        self.call_after_refresh(self.query_one(SearchInput).focus)
        if not self.local_loaded:
            self.local_loaded = True
            self.load_local()

    @work(group="local")
    async def load_local(self) -> None:
        spotify = self.app.spotify
        try:
            playlists, albums = await asyncio.gather(
                spotify.all_pages(spotify.playlists), spotify.all_pages(spotify.saved_albums)
            )
        except (SpotifyError, AuthError):
            self.local_loaded = False
            return
        self.local = [_from_collection(item) for item in playlists + albums]
        query = self.query_one(SearchInput).value.strip()
        if query:
            self.run_search(query)

    @on(Input.Changed, "#query")
    def query_changed(self, event: Input.Changed) -> None:
        self.run_search(event.value.strip())

    @on(Input.Submitted, "#query")
    def query_submitted(self) -> None:
        results = self.query_one(Results)
        if results.items:
            results.focus()

    def local_matches(self, query: str) -> list[Result]:
        matcher = Matcher(query)
        scored = [(matcher.match(f"{item.name} {item.subtitle}"), item) for item in self.local]
        scored = [pair for pair in scored if pair[0] >= MIN_LOCAL_SCORE]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:LOCAL_LIMIT]]

    @work(exclusive=True, group="search")
    async def run_search(self, query: str) -> None:
        results = self.query_one(Results)
        if not query:
            results.show_text("results", "type to search")
            return
        local = self.local_matches(query)
        if local:
            results.show_items("results", query, local, len(local), None)
        await asyncio.sleep(DEBOUNCE_SECONDS)
        try:
            remote = await self.app.spotify.search(query)
        except (SpotifyError, AuthError) as error:
            if not local:
                results.show_text("results", str(error))
            return
        seen = {item.uri for item in local}
        combined = local + [item for item in remote if item.uri not in seen]
        if combined:
            results.show_items(f"results  {len(combined)}", query, combined, len(combined), None)
        else:
            results.show_text("results", "nothing found")

    @on(OptionList.OptionSelected, "#results")
    def selected(self) -> None:
        result = self.query_one(Results).current
        if not isinstance(result, Result):
            return
        screen = self.screen
        offset = result.uri if result.kind == "track" else None
        screen.show_message(f"playing {result.name}")
        screen.control(self.app.spotify.play, screen.device_id, result.context_uri, offset)
