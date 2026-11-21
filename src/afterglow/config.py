import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "afterglow"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8989/login"
DEFAULT_DEVICE_NAME = "Web Player (Chrome)"


def _xdg_dir(env_var: str, fallback: str) -> Path:
    base = os.environ.get(env_var) or str(Path.home() / fallback)
    return Path(base) / APP_NAME


CONFIG_DIR = _xdg_dir("XDG_CONFIG_HOME", ".config")
CACHE_DIR = _xdg_dir("XDG_CACHE_HOME", ".cache")
STATE_DIR = _xdg_dir("XDG_STATE_HOME", ".local/state")
CONFIG_FILE = CONFIG_DIR / "config.toml"
TOKEN_FILE = CACHE_DIR / "token.json"
STATE_FILE = STATE_DIR / "state.json"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    client_id: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    device_name: str = DEFAULT_DEVICE_NAME
    engine_command: str = ""
    theme_file: str = ""
    art_color: str = "accent"


def load_config(path: Path = CONFIG_FILE) -> Config:
    if not path.exists():
        raise ConfigError(
            f"No config found at {path}\n"
            f'Create it with:\n\nclient_id = "YOUR_SPOTIFY_CLIENT_ID"\n'
        )

    with path.open("rb") as file:
        data = tomllib.load(file)

    client_id = str(data.get("client_id", "")).strip()
    if not client_id:
        raise ConfigError(f"'client_id' is missing in {path}")

    return Config(
        client_id=client_id,
        redirect_uri=str(data.get("redirect_uri", DEFAULT_REDIRECT_URI)).strip(),
        device_name=str(data.get("device_name", DEFAULT_DEVICE_NAME)).strip(),
        engine_command=str(data.get("engine_command", "")).strip(),
        theme_file=str(data.get("theme_file", "")).strip(),
        art_color=str(data.get("art_color", "accent")).strip(),
    )
