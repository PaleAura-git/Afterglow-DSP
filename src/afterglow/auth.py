import asyncio
import base64
import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from afterglow.config import TOKEN_FILE, Config

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = [
    "user-read-private",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "user-read-recently-played",
    "user-library-read",
    "user-library-modify",
    "playlist-read-private",
    "playlist-read-collaborative",
]
REFRESH_MARGIN_SECONDS = 60

CALLBACK_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>afterglow</title>
<style>
body {{ background:#111; color:#ddd; font-family:monospace; display:grid;
       place-items:center; height:100vh; margin:0; }}
</style></head>
<body><div><h2>{title}</h2><p>{message}</p></div></body></html>"""


class AuthError(Exception):
    pass


@dataclass
class Token:
    access_token: str
    refresh_token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - REFRESH_MARGIN_SECONDS

    @classmethod
    def from_response(cls, data: dict, previous_refresh_token: str = "") -> "Token":
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token") or previous_refresh_token,
            expires_at=time.time() + int(data.get("expires_in", 3600)),
        )


def load_token(path: Path = TOKEN_FILE) -> Token | None:
    try:
        return Token(**json.loads(path.read_text()))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, KeyError):
        return None


def save_token(token: Token, path: Path = TOKEN_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(asdict(token)))


def delete_token(path: Path = TOKEN_FILE) -> None:
    path.unlink(missing_ok=True)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


@dataclass
class LoginRequest:
    url: str
    state: str
    verifier: str


def start_login(config: Config) -> LoginRequest:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    query = urlencode(
        {
            "client_id": config.client_id,
            "response_type": "code",
            "redirect_uri": config.redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
            "scope": " ".join(SCOPES),
        }
    )
    return LoginRequest(url=f"{AUTHORIZE_URL}?{query}", state=state, verifier=verifier)


async def wait_for_code(config: Config, state: str, timeout: float = 300) -> str:
    redirect = urlparse(config.redirect_uri)
    host = redirect.hostname or "127.0.0.1"
    port = redirect.port or 80
    result: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = (await reader.readline()).decode(errors="replace")
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass

            parts = request_line.split()
            target = urlparse(parts[1]) if len(parts) > 1 else urlparse("/")
            if target.path != redirect.path:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                return

            params = parse_qs(target.query)
            error = params.get("error", [None])[0]
            code = params.get("code", [None])[0]
            returned_state = params.get("state", [None])[0]

            if error:
                title, message = "login cancelled", f"Spotify said: {error}"
                outcome: str | Exception = AuthError(f"Spotify login failed: {error}")
            elif returned_state != state or not code:
                title, message = "something's off", "This login link didn't match. Try again from afterglow."
                outcome = AuthError("Login state mismatch")
            else:
                title, message = "you're in ✨", "You can close this tab and head back to afterglow."
                outcome = code

            body = CALLBACK_PAGE.format(title=title, message=message).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            if not result.done():
                if isinstance(outcome, Exception):
                    result.set_exception(outcome)
                else:
                    result.set_result(outcome)
        finally:
            await writer.drain()
            writer.close()

    try:
        server = await asyncio.start_server(handle, host, port)
    except OSError as error:
        raise AuthError(f"Couldn't listen on {host}:{port} ({error.strerror})") from error

    async with server:
        try:
            return await asyncio.wait_for(result, timeout)
        except TimeoutError as error:
            raise AuthError("Login timed out") from error


async def exchange_code(config: Config, code: str, verifier: str) -> Token:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.redirect_uri,
                "client_id": config.client_id,
                "code_verifier": verifier,
            },
        )
    if response.status_code != 200:
        raise AuthError(f"Token exchange failed ({response.status_code}): {response.text}")
    return Token.from_response(response.json())


async def refresh_token(config: Config, token: Token) -> Token:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": config.client_id,
            },
        )
    if response.status_code != 200:
        raise AuthError(f"Token refresh failed ({response.status_code}): {response.text}")
    return Token.from_response(response.json(), previous_refresh_token=token.refresh_token)


class Session:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.token = load_token()
        self._lock = asyncio.Lock()

    @property
    def logged_in(self) -> bool:
        return self.token is not None

    async def login(self, request: LoginRequest) -> None:
        code = await wait_for_code(self.config, request.state)
        self.token = await exchange_code(self.config, code, request.verifier)
        save_token(self.token)

    async def access_token(self) -> str:
        async with self._lock:
            if self.token is None:
                raise AuthError("Not logged in")
            if self.token.expired:
                self.token = await refresh_token(self.config, self.token)
                save_token(self.token)
            return self.token.access_token

    def logout(self) -> None:
        self.token = None
        delete_token()
