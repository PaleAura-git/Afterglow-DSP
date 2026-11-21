import io

import httpx
from PIL import Image, ImageOps
from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual import work
from textual.strip import Strip
from textual.widget import Widget

from afterglow.state import load_state, save_state

HALF_BLOCK = "▀"
BRAILLE_BASE = 0x2800
BRAILLE_DOTS = (
    (0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04), (0, 3, 0x40),
    (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20), (1, 3, 0x80),
)
MODES = ("blocks", "braille")

Pixels = list[list[tuple[int, int, int]]]


def load_image(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _pixels(image: Image.Image, width: int, height: int) -> Pixels:
    resized = image.resize((width, height), Image.Resampling.LANCZOS)
    data = resized.load()
    return [[data[x, y] for x in range(width)] for y in range(height)]


def half_block_strips(image: Image.Image, width: int, height: int) -> list[Strip]:
    pixels = _pixels(image, width, height * 2)
    strips = []
    for top_row, bottom_row in zip(pixels[0::2], pixels[1::2]):
        segments = [
            Segment(HALF_BLOCK, Style.from_color(Color.from_rgb(*top), Color.from_rgb(*bottom)))
            for top, bottom in zip(top_row, bottom_row)
        ]
        strips.append(Strip(segments, width))
    return strips


def _grayscale(image: Image.Image, width: int, height: int) -> Image.Image:
    gray = ImageOps.autocontrast(image.convert("L"), cutoff=2)
    return gray.resize((width, height), Image.Resampling.LANCZOS)


def braille_strips(image: Image.Image, width: int, height: int, color: str) -> list[Strip]:
    style = Style(color=color)
    dots = _grayscale(image, width * 2, height * 4).convert("1").load()
    strips = []
    for cell_y in range(height):
        line = ""
        for cell_x in range(width):
            code = BRAILLE_BASE
            for dx, dy, bit in BRAILLE_DOTS:
                if dots[cell_x * 2 + dx, cell_y * 4 + dy]:
                    code |= bit
            line += chr(code)
        strips.append(Strip([Segment(line, style)], width))
    return strips


class Cover(Widget):
    DEFAULT_CSS = """
    Cover {
        width: 20;
        height: 10;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.url = ""
        saved = load_state().get("art_mode")
        self.mode = saved if saved in MODES else MODES[0]
        self.image: Image.Image | None = None
        self.images: dict[str, Image.Image] = {}
        self.strips: list[Strip] = []

    def show(self, url: str) -> None:
        if url == self.url:
            return
        self.url = url
        if not url:
            self.image = None
            self.draw()
            return
        self.fetch(url)

    def on_resize(self) -> None:
        self.draw()

    def cycle_mode(self) -> str:
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        save_state({"art_mode": self.mode})
        self.draw()
        return self.mode

    @work(exclusive=True, group="cover")
    async def fetch(self, url: str) -> None:
        if url not in self.images:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(url)
                    response.raise_for_status()
            except httpx.HTTPError:
                return
            self.images[url] = load_image(response.content)
        if url == self.url:
            self.image = self.images[url]
            self.draw()

    def draw(self) -> None:
        width, height = self.size.width or 20, self.size.height or 10
        if self.image is None:
            self.strips = []
        elif self.mode == "braille":
            self.strips = braille_strips(self.image, width, height, self._accent())
        else:
            self.strips = half_block_strips(self.image, width, height)
        self.refresh()

    def _accent(self) -> str:
        return self.app.art_color()

    def render_line(self, y: int) -> Strip:
        if y < len(self.strips):
            return self.strips[y]
        return Strip.blank(self.size.width)
