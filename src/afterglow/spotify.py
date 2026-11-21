import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from afterglow.auth import Session

API_URL = "https://api.spotify.com/v1"
MAX_RATE_LIMIT_WAIT = 30


class SpotifyError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{message} ({status})")
        self.status = status


@dataclass(frozen=True)
class User:
    id: str
    name: str


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    type: str
    is_active: bool
    volume: int | None


@dataclass
class Playback:
    is_playing: bool
    progress_ms: int
    duration_ms: int
    track: str
    artists: str
    album: str
    shuffle: bool
    repeat: str
    volume: int | None
    device: str
    fetched_at: float = field(default_factory=time.monotonic)

    @property
    def position_ms(self) -> int:
        if not self.is_playing:
            return self.progress_ms
        elapsed = int((time.monotonic() - self.fetched_at) * 1000)
        return min(self.progress_ms + elapsed, self.duration_ms)

    def moved_to(self, position_ms: int) -> None:
        self.progress_ms = max(0, min(position_ms, self.duration_ms))
        self.fetched_at = time.monotonic()


REPEAT_CYCLE = {"off": "context", "context": "track", "track": "off"}


class Spotify:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.http = httpx.AsyncClient(base_url=API_URL, timeout=15)

    async def close(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in range(3):
            token = await self.session.access_token()
            response = await self.http.request(
                method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
            )

            if response.status_code == 401 and attempt == 0:
                self.session.token.expires_at = 0
                continue

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", "1"))
                if wait > MAX_RATE_LIMIT_WAIT:
                    raise SpotifyError(429, f"Rate limited for {wait}s")
                await asyncio.sleep(wait)
                continue

            if response.status_code >= 400:
                raise SpotifyError(response.status_code, _error_message(response))

            if "application/json" not in response.headers.get("content-type", ""):
                return None
            try:
                return response.json()
            except ValueError:
                return None

        raise SpotifyError(response.status_code, _error_message(response))

    async def me(self) -> User:
        data = await self.request("GET", "/me")
        return User(id=data["id"], name=data.get("display_name") or data["id"])

    async def devices(self) -> list[Device]:
        data = await self.request("GET", "/me/player/devices")
        return [
            Device(
                id=item["id"],
                name=item["name"],
                type=item["type"].lower(),
                is_active=item["is_active"],
                volume=item.get("volume_percent"),
            )
            for item in data.get("devices", [])
            if item.get("id")
        ]

    async def transfer(self, device_id: str, play: bool = True) -> None:
        await self.request(
            "PUT", "/me/player", json={"device_ids": [device_id], "play": play}
        )

    async def playback(self) -> Playback | None:
        data = await self.request("GET", "/me/player", params={"additional_types": "episode"})
        if not data or not data.get("item"):
            return None
        item = data["item"]
        if item.get("type") == "episode":
            artists = item.get("show", {}).get("name", "")
            album = item.get("show", {}).get("publisher", "")
        else:
            artists = ", ".join(artist["name"] for artist in item.get("artists", []))
            album = item.get("album", {}).get("name", "")
        device = data.get("device") or {}
        return Playback(
            is_playing=data.get("is_playing", False),
            progress_ms=data.get("progress_ms") or 0,
            duration_ms=item.get("duration_ms") or 0,
            track=item.get("name", ""),
            artists=artists,
            album=album,
            shuffle=data.get("shuffle_state", False),
            repeat=data.get("repeat_state", "off"),
            volume=device.get("volume_percent"),
            device=device.get("name", ""),
        )

    async def play(self, device_id: str | None = None) -> None:
        params = {"device_id": device_id} if device_id else None
        await self.request("PUT", "/me/player/play", params=params)

    async def pause(self) -> None:
        await self.request("PUT", "/me/player/pause")

    async def next(self) -> None:
        await self.request("POST", "/me/player/next")

    async def previous(self) -> None:
        await self.request("POST", "/me/player/previous")

    async def seek(self, position_ms: int) -> None:
        await self.request("PUT", "/me/player/seek", params={"position_ms": max(0, position_ms)})

    async def set_volume(self, percent: int) -> None:
        await self.request(
            "PUT", "/me/player/volume", params={"volume_percent": max(0, min(100, percent))}
        )

    async def set_shuffle(self, state: bool) -> None:
        await self.request("PUT", "/me/player/shuffle", params={"state": str(state).lower()})

    async def set_repeat(self, state: str) -> None:
        await self.request("PUT", "/me/player/repeat", params={"state": state})


def _error_message(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return response.reason_phrase or "Spotify request failed"
