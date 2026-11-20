import asyncio
import logging
from dataclasses import dataclass

import numpy as np
from telethon import TelegramClient
from telethon.sessions import StringSession
from pytgcalls import PyTgCalls, filters as call_filters
from pytgcalls.types import Device, Direction, ExternalMedia, MediaStream, RecordStream, StreamFrames
from pytgcalls.types.raw import AudioParameters

from config import Settings
from dsp import AudioDSP, DSPSettings

log = logging.getLogger("chimney.voice")
AUDIO = AudioParameters(bitrate=48000, channels=2)


@dataclass
class BridgeInfo:
    source_id: int
    target_id: int
    source_name: str
    target_name: str


class VoiceRuntime:
    """One hosted user account; source VC audio is relayed one-way to target VC."""

    def __init__(self, user_id: int, session: str, settings: Settings):
        self.user_id = user_id
        self.session = session
        self.settings = settings
        self.client = None
        self.calls = None
        self.bridge = None
        self.level = 1.0
        self.dsp = AudioDSP(settings=DSPSettings())
        self.lock = asyncio.Lock()
        self.started = False

    async def start(self):
        self.client = TelegramClient(
            StringSession(self.session), self.settings.api_id, self.settings.api_hash
        )
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            raise RuntimeError("Hosted Telegram session is no longer authorized.")

        self.calls = PyTgCalls(self.client)
        await self.calls.start()
        self.started = True

        @self.calls.on_update(
            call_filters.stream_frame(Direction.INCOMING, Device.MICROPHONE)
        )
        async def relay(_: PyTgCalls, update: StreamFrames):
            await self._relay_audio(update)

        log.info("Voice runtime started for owner %s", self.user_id)

    async def _relay_audio(self, update: StreamFrames):
        bridge = self.bridge
        if (
            bridge is None or self.calls is None
            or update.chat_id != bridge.source_id
            or update.direction != Direction.INCOMING
            or update.device != Device.MICROPHONE
        ):
            return

        async with self.lock:
            bridge = self.bridge
            if bridge is None or update.chat_id != bridge.source_id:
                return
            for frame in update.frames:
                if not frame.frame: continue
                pcm = self.dsp.process(frame.frame)
                if self.level != 1.0:
                    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
                    pcm = np.clip(samples*self.level, -32768, 32767).astype("<i2").tobytes()
                try:
                    await self.calls.send_frame(bridge.target_id, Device.MICROPHONE, pcm)
            except Exception:
                log.exception("Frame relay failed for owner %s", self.user_id)

    async def start_bridge(self, source_id: int, target_id: int):
        if not self.started or self.client is None or self.calls is None:
            raise RuntimeError("Hosted account is not running.")
        if source_id == target_id:
            raise ValueError("Source and target groups must be different.")
        if self.bridge is not None:
            raise RuntimeError("Bridge already active. Use /leave first.")

        source = await self.client.get_entity(source_id)
        target = await self.client.get_entity(target_id)
        info = BridgeInfo(
            source_id, target_id,
            getattr(source, "title", None) or str(source_id),
            getattr(target, "title", None) or str(target_id),
        )
        self.bridge = info
        try:
            # Join source and subscribe to incoming participant audio.
            await self.calls.play(source_id, MediaStream(ExternalMedia.AUDIO, AUDIO))
            await self.calls.record(
                source_id, RecordStream(audio=True, audio_parameters=AUDIO)
            )
            # Join target with an external audio input for the relayed frames.
            await self.calls.play(target_id, MediaStream(ExternalMedia.AUDIO, AUDIO))
        except Exception:
            self.bridge = None
            for chat_id in (source_id, target_id):
                try:
                    await self.calls.leave_call(chat_id)
                except Exception:
                    pass
            raise
        log.info("Bridge active: %s -> %s", source_id, target_id)
        return info

    async def stop_bridge(self):
        info = self.bridge
        self.bridge = None
        if info is None or self.calls is None:
            return
        for chat_id in (info.source_id, info.target_id):
            try:
                await self.calls.leave_call(chat_id)
            except Exception as exc:
                log.warning("Could not leave VC %s: %s", chat_id, exc)

    async def set_level(self, level: float):
        self.level = level

    async def update_dsp(self, **changes):
        self.dsp.update(**changes)

    async def stop(self):
        await self.stop_bridge()
        if self.client is not None and self.client.is_connected():
            await self.client.disconnect()
        self.started = False
