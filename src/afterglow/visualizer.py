import asyncio
import shutil
import tempfile
from pathlib import Path

from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

LEVELS = " ▁▂▃▄▅▆▇█"
MAX_VALUE = 100
FRAMERATE = 30
RESTART_DELAY = 0.3

CAVA_CONFIG = """[general]
bars = {bars}
framerate = {framerate}

[input]
method = pulse
source = auto

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = {max_value}
bar_delimiter = 59
frame_delimiter = 10
"""


class Visualizer(Widget):
    DEFAULT_CSS = """
    Visualizer {
        height: 1fr;
        min-height: 3;
        width: 100%;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.levels: list[int] = []
        self.bars = 0
        self.cava_process: asyncio.subprocess.Process | None = None
        self.cava_task: asyncio.Task | None = None
        self.config_file = Path(tempfile.gettempdir()) / f"afterglow-cava-{id(self)}.conf"
        self.active = False

    @property
    def available(self) -> bool:
        return shutil.which("cava") is not None

    def start(self) -> None:
        self.active = True
        self.bars = 0
        self.on_resize()

    async def stop(self) -> None:
        self.active = False
        if self.cava_task:
            self.cava_task.cancel()
            self.cava_task = None
        await self.stop_cava()
        self.levels = []
        self.refresh()

    def on_resize(self) -> None:
        if not self.active:
            return
        bars = max(2, (self.size.width + 1) // 2 // 2 * 2)
        if bars != self.bars and self.available:
            self.bars = bars
            if self.cava_task:
                self.cava_task.cancel()
            self.cava_task = asyncio.create_task(self.run_cava(bars))

    async def on_unmount(self) -> None:
        if self.cava_task:
            self.cava_task.cancel()
        await self.stop_cava()
        self.config_file.unlink(missing_ok=True)

    async def stop_cava(self) -> None:
        if self.cava_process and self.cava_process.returncode is None:
            self.cava_process.kill()
            await self.cava_process.wait()
        self.cava_process = None

    async def run_cava(self, bars: int) -> None:
        await asyncio.sleep(RESTART_DELAY)
        await self.stop_cava()
        self.config_file.write_text(
            CAVA_CONFIG.format(bars=bars, framerate=FRAMERATE, max_value=MAX_VALUE)
        )
        self.cava_process = await asyncio.create_subprocess_exec(
            "cava",
            "-p",
            str(self.config_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            while line := await self.cava_process.stdout.readline():
                values = line.decode(errors="ignore").strip().rstrip(";").split(";")
                try:
                    self.levels = [int(value) for value in values if value]
                except ValueError:
                    continue
                self.refresh()
        finally:
            await self.stop_cava()

    def render_line(self, y: int) -> Strip:
        width, height = self.size.width, self.size.height
        if not self.levels or height == 0:
            return Strip.blank(width)
        style = Style(color=self.app.art_color())
        row_from_bottom = height - 1 - y
        cells = []
        for level in self.levels:
            eighths = round(level / MAX_VALUE * height * 8)
            fill = max(0, min(8, eighths - row_from_bottom * 8))
            cells.append(LEVELS[fill])
        line = " ".join(cells)[:width]
        return Strip([Segment(line.ljust(width), style)], width)
