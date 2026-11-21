import json
from pathlib import Path
from typing import Any

from afterglow.config import STATE_FILE


def load_state(path: Path = STATE_FILE) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(updates: dict[str, Any], path: Path = STATE_FILE) -> None:
    state = load_state(path)
    state.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))
