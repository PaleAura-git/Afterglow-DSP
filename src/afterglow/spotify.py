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
    art_url: str = ""
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


@dataclass(frozen=True)
class Track:
    id: str
    uri: str
    name: str
    artists: str
    album: str
    duration_ms: int


@dataclass(frozen=True)
class Collection:
    kind: str
    id: str
    uri: str
    name: str
    subtitle: str
    total: int


@dataclass(frozen=True)
class Page:
    items: list
    total: int


@dataclass(frozen=True)
class Result:
    kind: str
    name: str
    subtitle: str
    uri: str
    context_uri: str
    duration_ms: int = 0
    yours: bool = False


PAGE_SIZE = 50
SEARCH_LIMIT = 10

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
            images = item.get("images") or item.get("show", {}).get("images", [])
        else:
            artists = ", ".join(artist["name"] for artist in item.get("artists", []))
            album = item.get("album", {}).get("name", "")
            images = item.get("album", {}).get("images", [])
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
            art_url=_smallest_image(images, 64),
        )

    async def play(
        self,
        device_id: str | None = None,
        context_uri: str | None = None,
        offset_uri: str | None = None,
    ) -> None:
        params = {"device_id": device_id} if device_id else None
        body: dict[str, Any] = {}
        if context_uri:
            body["context_uri"] = context_uri
            if offset_uri:
                body["offset"] = {"uri": offset_uri}
        await self.request("PUT", "/me/player/play", params=params, json=body or None)

    async def liked_tracks(self, offset: int = 0) -> Page:
        data = await self.request("GET", "/me/tracks", params={"limit": PAGE_SIZE, "offset": offset})
        return Page([_track(item.get("track")) for item in data["items"] if item.get("track")], data["total"])

    async def saved_albums(self, offset: int = 0) -> Page:
        data = await self.request("GET", "/me/albums", params={"limit": PAGE_SIZE, "offset": offset})
        return Page(
            [
                Collection(
                    kind="album",
                    id=album["id"],
                    uri=album["uri"],
                    name=album["name"],
                    subtitle=_artists(album),
                    total=album.get("total_tracks") or 0,
                )
                for album in (item["album"] for item in data["items"] if item.get("album"))
            ],
            data["total"],
        )

    async def playlists(self, offset: int = 0) -> Page:
        data = await self.request("GET", "/me/playlists", params={"limit": PAGE_SIZE, "offset": offset})
        return Page(
            [
                Collection(
                    kind="playlist",
                    id=playlist["id"],
                    uri=playlist["uri"],
                    name=playlist["name"],
                    subtitle=(playlist.get("owner") or {}).get("display_name") or "",
                    total=((playlist.get("items") or playlist.get("tracks")) or {}).get("total") or 0,
                )
                for playlist in data["items"]
                if playlist
            ],
            data["total"],
        )

    async def album_tracks(self, album_id: str, offset: int = 0) -> Page:
        data = await self.request(
            "GET", f"/albums/{album_id}/tracks", params={"limit": PAGE_SIZE, "offset": offset}
        )
        return Page([_track(item) for item in data["items"] if item], data["total"])

    async def search(self, query: str) -> list[Result]:
        data = await self.request(
            "GET",
            "/search",
            params={"q": query, "type": "track,artist,album,playlist", "limit": SEARCH_LIMIT},
        )
        return _search_results(data or {})

    async def all_pages(self, loader) -> list:
        first = await loader(0)
        items = list(first.items)
        while len(items) < first.total:
            page = await loader(len(items))
            if not page.items:
                break
            items.extend(page.items)
        return items

    async def playlist_tracks(self, playlist_id: str, offset: int = 0) -> Page:
        data = await self.request(
            "GET", f"/playlists/{playlist_id}/items", params={"limit": PAGE_SIZE, "offset": offset}
        )
        tracks = []
        for entry in data["items"]:
            item = entry.get("item") or entry.get("track")
            if item and item.get("uri") and not item.get("is_local"):
                tracks.append(_track(item))
        return Page(tracks, data["total"])

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


def _search_results(data: dict) -> list[Result]:
    results: list[Result] = []
    for track in (data.get("tracks") or {}).get("items") or []:
        if not track:
            continue
        album = track.get("album") or {}
        results.append(
            Result("track", track["name"], _artists(track), track["uri"], album.get("uri", ""), track.get("duration_ms") or 0)
        )
    for artist in (data.get("artists") or {}).get("items") or []:
        if artist:
            results.append(Result("artist", artist["name"], "", artist["uri"], artist["uri"]))
    for album in (data.get("albums") or {}).get("items") or []:
        if album:
            results.append(Result("album", album["name"], _artists(album), album["uri"], album["uri"]))
    for playlist in (data.get("playlists") or {}).get("items") or []:
        if playlist:
            owner = (playlist.get("owner") or {}).get("display_name") or ""
            results.append(Result("playlist", playlist["name"], owner, playlist["uri"], playlist["uri"]))
    return results


def _artists(item: dict) -> str:
    return ", ".join(artist["name"] for artist in item.get("artists", []))


def _track(item: dict) -> Track:
    return Track(
        id=item.get("id") or "",
        uri=item["uri"],
        name=item.get("name", ""),
        artists=_artists(item) or (item.get("show") or {}).get("name", ""),
        album=(item.get("album") or {}).get("name", ""),
        duration_ms=item.get("duration_ms") or 0,
    )


def _smallest_image(images: list[dict], minimum: int) -> str:
    usable = [image for image in images if image.get("url")]
    if not usable:
        return ""
    big_enough = [image for image in usable if (image.get("width") or 0) >= minimum]
    pick = min(big_enough or usable, key=lambda image: image.get("width") or 0)
    return pick["url"]


def _error_message(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return response.reason_phrase or "Spotify request failed"
