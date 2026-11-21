import array
import asyncio
import shutil
from collections import deque

from rich.text import Text
from textual.widget import Widget

RATE = 22050
WINDOW = 512
SEARCH = 1024
FPS = 30
MIN_GAIN = 1500
GAIN_SMOOTHING = 0.2
BRAILLE_BASE = 0x2800
BRAILLE_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))

PAREC = (
    "parec",
    "--device=@DEFAULT_MONITOR@",
    "--format=s16le",
    f"--rate={RATE}",
    "--channels=1",
    "--raw",
    "--latency-msec=20",
)


def _trigger(samples: list[int]) -> int:
    end = len(samples) - WINDOW
    start = max(1, end - SEARCH)
    for index in range(end, start, -1):
        if samples[index - 1] < 0 <= samples[index]:
            return index
    return max(0, end)


class Scope(Widget):
    DEFAULT_CSS = """
    Scope {
        height: 1fr;
        min-height: 3;
        width: 100%;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.samples: deque[int] = deque(maxlen=WINDOW + SEARCH + 1)
        self.gain = float(MIN_GAIN)
        self.capture_process: asyncio.subprocess.Process | None = None
        self.capture_task: asyncio.Task | None = None
        self.ticker = None

    def start(self) -> None:
        if self.capture_task or not shutil.which("parec"):
            return
        self.capture_task = asyncio.create_task(self.capture())
        self.ticker = self.set_interval(1 / FPS, self.refresh)

    async def stop(self) -> None:
        if self.ticker:
            self.ticker.stop()
            self.ticker = None
        if self.capture_task:
            self.capture_task.cancel()
            self.capture_task = None
        if self.capture_process and self.capture_process.returncode is None:
            self.capture_process.kill()
            await self.capture_process.wait()
        self.capture_process = None
        self.samples.clear()
        self.refresh()

    async def on_unmount(self) -> None:
        await self.stop()

    async def capture(self) -> None:
        self.capture_process = await asyncio.create_subprocess_exec(
            *PAREC,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        leftover = b""
        while chunk := await self.capture_process.stdout.read(2048):
            chunk = leftover + chunk
            usable = len(chunk) // 2 * 2
            leftover = chunk[usable:]
            frame = array.array("h")
            frame.frombytes(chunk[:usable])
            self.samples.extend(frame)

    def render(self) -> Text:
        width, height = self.size.width, self.size.height
        if width == 0 or height == 0:
            return Text("")
        dots_x, dots_y = width * 2, height * 4
        center = (dots_y - 1) / 2

        samples = list(self.samples)
        if len(samples) > WINDOW:
            start = _trigger(samples)
            window = samples[start : start + WINDOW]
            peak = max(MIN_GAIN, max(abs(value) for value in window))
            self.gain += (peak - self.gain) * GAIN_SMOOTHING
            ys = [
                round(center - window[x * WINDOW // dots_x] / self.gain * center)
                for x in range(dots_x)
            ]
        else:
            ys = [round(center)] * dots_x

        grid = [[0] * width for _ in range(height)]
        previous = ys[0]
        for x, y in enumerate(ys):
            y = max(0, min(dots_y - 1, y))
            low, high = sorted((previous, y))
            for dot_y in range(low, high + 1):
                grid[dot_y // 4][x // 2] |= BRAILLE_BITS[dot_y % 4][x % 2]
            previous = y

        lines = "\n".join("".join(chr(BRAILLE_BASE | cell) for cell in row) for row in grid)
        return Text(lines, style=self.app.art_color(), no_wrap=True)
