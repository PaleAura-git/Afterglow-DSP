import tomllib
from pathlib import Path

from textual.theme import Theme

THEME_NAME = "afterglow"
FALLBACK_THEME = "textual-dark"
DEFAULT_THEME_FILE = Path.home() / ".local/state/omarchy/current/theme/colors.toml"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def load_palette(path: Path) -> dict[str, str]:
    try:
        colors = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {key: value for key, value in colors.items() if isinstance(value, str)}


def resolve_color(name: str, palette: dict[str, str], accent: str) -> str:
    if not name or name == "accent":
        return accent
    if name.startswith("#"):
        return name
    return palette.get(name, accent)


def load_theme(path: Path) -> Theme | None:
    colors = load_palette(path)
    if not colors:
        return None

    def pick(*keys: str) -> str | None:
        return next((colors[key] for key in keys if isinstance(colors.get(key), str)), None)

    accent = pick("accent", "blue")
    background = pick("background")
    foreground = pick("foreground")
    if not (accent and background and foreground):
        return None

    return Theme(
        name=THEME_NAME,
        primary=accent,
        secondary=pick("magenta", "cyan") or accent,
        accent=accent,
        foreground=foreground,
        background=background,
        surface=pick("dark_background", "background"),
        panel=pick("lighter_background", "selection", "muted"),
        boost=pick("selection"),
        warning=pick("yellow"),
        error=pick("red"),
        success=pick("green"),
        dark=colors.get("mode", "dark") != "light",
    )


class ThemeWatcher:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stamp = -1.0

    def changed(self) -> bool:
        stamp = _mtime(self.path)
        if stamp == self.stamp:
            return False
        self.stamp = stamp
        return True
