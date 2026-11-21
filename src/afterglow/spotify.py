import asyncio
from dataclasses import dataclass
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

            if response.status_code == 204 or not response.content:
                return None
            return response.json()

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


def _error_message(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return response.reason_phrase or "Spotify request failed"
