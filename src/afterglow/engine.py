import asyncio
import subprocess

from afterglow.config import Config
from afterglow.spotify import Device, Spotify

ENGINE_WAIT_SECONDS = 25
POLL_SECONDS = 1.0


class EngineError(Exception):
    pass


def _find(devices: list[Device], name: str) -> Device | None:
    wanted = name.casefold()
    return next((device for device in devices if device.name.casefold() == wanted), None)


def _launch(command: str) -> None:
    subprocess.Popen(
        command,
        shell=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


async def connect(spotify: Spotify, config: Config, on_status=lambda text: None) -> Device:
    device = _find(await spotify.devices(), config.device_name)

    if device is None:
        if not config.engine_command:
            raise EngineError(f"'{config.device_name}' isn't online")
        on_status("starting engine...")
        _launch(config.engine_command)
        for _ in range(int(ENGINE_WAIT_SECONDS / POLL_SECONDS)):
            await asyncio.sleep(POLL_SECONDS)
            device = _find(await spotify.devices(), config.device_name)
            if device:
                break
        else:
            raise EngineError(f"'{config.device_name}' didn't come online")

    if not device.is_active:
        on_status("taking over playback...")
        await spotify.transfer(device.id, play=False)

    return device
