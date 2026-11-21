import asyncio
from collections.abc import Awaitable, Callable

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from afterglow.auth import AuthError
from afterglow.nowplaying import format_time
from afterglow.spotify import Collection, Page, SpotifyError, Track

Loader = Callable[[int], Awaitable[Page]]
LOAD_AHEAD = 10
PREVIEW_DELAY = 0.2

SECTIONS = (("liked", "liked songs"), ("albums", "albums"), ("playlists", "playlists"))


def _label(main: str, extra: str = "") -> Text:
    text = Text(main, no_wrap=True, overflow="ellipsis")
    if extra:
        text.append(f"  {extra}", style="dim")
    return text


def _row(item) -> Text:
    if isinstance(item, Track):
        return _label(item.name, f"{item.artists}  {format_time(item.duration_ms)}")
    if isinstance(item, Collection):
        return _label(item.name, f"{item.subtitle}  {item.total}".strip())
    return _label(str(item))


class Column(OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "first", "Top", show=False),
        Binding("G", "last", "Bottom", show=False),
    ]

    DEFAULT_CSS = """
    Column {
        width: 1fr;
        height: 100%;
        border: solid $panel;
        border-title-color: $text-muted;
        padding: 0;
        scrollbar-size-vertical: 1;
    }

    Column:focus {
        border: solid $accent;
        border-title-color: $accent;
    }

    Column > .option-list--option-highlighted {
        text-style: bold;
    }
    """

    def render_row(self, item) -> Text:
        return _row(item)

    def __init__(self, title: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.border_title = title
        self.items: list = []
        self.total = 0
        self.loader: Loader | None = None
        self.key = ""
        self.loading = False

    def action_cursor_down(self) -> None:
        if self.option_count == 0:
            return
        if self.highlighted is None:
            self.highlighted = 0
        elif self.highlighted < self.option_count - 1:
            self.highlighted += 1

    def action_cursor_up(self) -> None:
        if self.option_count == 0:
            return
        if self.highlighted is None:
            self.highlighted = 0
        elif self.highlighted > 0:
            self.highlighted -= 1

    def show_items(self, title: str, key: str, items: list, total: int, loader: Loader | None) -> None:
        self.border_title = title
        self.key = key
        self.items = list(items)
        self.total = total
        self.loader = loader
        self.clear_options()
        self.add_options([Option(self.render_row(item)) for item in self.items])
        if self.items:
            self.highlighted = 0

    def show_text(self, title: str, text: str) -> None:
        self.border_title = title
        self.key = ""
        self.items = []
        self.total = 0
        self.loader = None
        self.clear_options()
        self.add_option(Option(Text(text, style="dim"), disabled=True))

    @property
    def current(self):
        if self.highlighted is None or self.highlighted >= len(self.items):
            return None
        return self.items[self.highlighted]

    def near_end(self) -> bool:
        return self.highlighted is not None and self.highlighted >= len(self.items) - LOAD_AHEAD

    @work(group="more")
    async def load_more(self) -> None:
        if self.loading or self.loader is None or len(self.items) >= self.total:
            return
        self.loading = True
        key = self.key
        try:
            page = await self.loader(len(self.items))
        except (SpotifyError, AuthError):
            return
        finally:
            self.loading = False
        if key != self.key or not page.items:
            return
        self.items.extend(page.items)
        self.add_options([Option(self.render_row(item)) for item in page.items])


class LibraryView(Horizontal):
    BINDINGS = [
        Binding("h,left", "focus_column(-1)", "Left", show=False),
        Binding("l,right", "focus_column(1)", "Right", show=False),
    ]

    DEFAULT_CSS = """
    LibraryView {
        padding: 0 1;
    }

    LibraryView #sections {
        width: 22;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cache: dict[str, Page] = {}
        self.user_id = ""

    def compose(self) -> ComposeResult:
        yield Column("library", id="sections")
        yield Column("", id="items")
        yield Column("", id="preview")

    def on_mount(self) -> None:
        sections = self.query_one("#sections", Column)
        sections.show_items("library", "sections", [label for _, label in SECTIONS], len(SECTIONS), None)
        sections.focus()

    @property
    def spotify(self):
        return self.app.spotify

    def columns(self) -> list[Column]:
        return list(self.query(Column))

    def action_focus_column(self, step: int) -> None:
        columns = self.columns()
        focused = next((index for index, column in enumerate(columns) if column.has_focus), 0)
        target = columns[max(0, min(len(columns) - 1, focused + step))]
        if target.items:
            target.focus()

    async def fetch(self, key: str, loader: Loader) -> Page:
        if key not in self.cache:
            self.cache[key] = await loader(0)
        return self.cache[key]

    def section_loader(self, section: str) -> Loader:
        return {
            "liked": self.spotify.liked_tracks,
            "albums": self.spotify.saved_albums,
            "playlists": self.spotify.playlists,
        }[section]

    def collection_loader(self, collection: Collection) -> Loader:
        if collection.kind == "album":
            return lambda offset: self.spotify.album_tracks(collection.id, offset)
        return lambda offset: self.spotify.playlist_tracks(collection.id, offset)

    @on(OptionList.OptionHighlighted)
    def maybe_load_more(self, event: OptionList.OptionHighlighted) -> None:
        column = event.option_list
        if isinstance(column, Column) and column.near_end():
            column.load_more()

    @on(OptionList.OptionHighlighted, "#sections")
    def section_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.load_section(SECTIONS[event.option_index][0], SECTIONS[event.option_index][1])

    @work(exclusive=True, group="section")
    async def load_section(self, section: str, title: str) -> None:
        items = self.query_one("#items", Column)
        items.show_text(title, "loading...")
        self.query_one("#preview", Column).show_text("", "")
        try:
            page = await self.fetch(section, self.section_loader(section))
        except (SpotifyError, AuthError) as error:
            items.show_text(title, str(error))
            return
        items.show_items(f"{title}  {page.total}", section, page.items, page.total, self.section_loader(section))

    @on(OptionList.OptionHighlighted, "#items")
    def item_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.load_preview(self.query_one("#items", Column).current)

    @work(exclusive=True, group="preview")
    async def load_preview(self, item) -> None:
        preview = self.query_one("#preview", Column)
        if isinstance(item, Track):
            preview.show_text(
                "track",
                f"{item.name}\n{item.artists}\n{item.album}\n\n{format_time(item.duration_ms)}",
            )
            return
        if not isinstance(item, Collection):
            preview.show_text("", "")
            return
        await asyncio.sleep(PREVIEW_DELAY)
        preview.show_text(item.name, "loading...")
        loader = self.collection_loader(item)
        try:
            page = await self.fetch(item.uri, loader)
        except SpotifyError as error:
            if error.status == 403:
                preview.show_text(
                    item.name,
                    "spotify doesn't let apps list the tracks\nof playlists you don't own\n\nenter: play it anyway",
                )
            else:
                preview.show_text(item.name, str(error))
            return
        except AuthError as error:
            preview.show_text(item.name, str(error))
            return
        preview.show_items(f"{item.name}  {page.total}", item.uri, page.items, page.total, loader)

    @on(OptionList.OptionSelected)
    def selected(self, event: OptionList.OptionSelected) -> None:
        column = event.option_list
        if not isinstance(column, Column):
            return
        if column.id == "sections":
            self.action_focus_column(1)
            return
        item = column.current
        if column.id == "items":
            if isinstance(item, Collection):
                self.play(item.uri, None, item.name)
            elif isinstance(item, Track):
                self.play(self.liked_context(), item.uri, item.name)
        elif column.id == "preview" and isinstance(item, Track):
            parent = self.query_one("#items", Column).current
            if isinstance(parent, Collection):
                self.play(parent.uri, item.uri, item.name)
            else:
                self.play(self.liked_context(), item.uri, item.name)

    def liked_context(self) -> str:
        return f"spotify:user:{self.user_id}:collection" if self.user_id else ""

    def play(self, context_uri: str, offset_uri: str | None, name: str) -> None:
        screen = self.screen
        screen.show_message(f"playing {name}")
        screen.control(self.spotify.play, screen.device_id, context_uri or None, offset_uri)
