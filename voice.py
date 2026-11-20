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

log = logging.getLogger("chimney.voice")
AUDIO = AudioParameters(bitrate=48000, channels=2)

# ── Audio DSP constants ──────────────────────────────────────────────────────
_SAMPLE_RATE        = 48000
_PRE_EMPHASIS       = 0.97      # pre-emphasis filter coeff (voice clarity boost)
_NOISE_GATE_DB      = -42.0     # dB below which gate closes (kills background hiss)
_NOISE_GATE_THRESH  = 10 ** (_NOISE_GATE_DB / 20.0)
_COMP_THRESHOLD     = 0.35      # compressor knee (normalized 0-1 float)
_COMP_RATIO         = 6.0       # compression ratio above threshold
_SOFT_CLIP_DRIVE    = 3.5       # tanh drive — higher = more saturation headroom
# ─────────────────────────────────────────────────────────────────────────────


def _pre_emphasis(signal: np.ndarray) -> np.ndarray:
    """Boost high-freq content → voice sounds crisp and cuts through mix."""
    out = np.empty_like(signal)
    out[0] = signal[0]
    out[1:] = signal[1:] - _PRE_EMPHASIS * signal[:-1]
    return out


def _noise_gate(signal: np.ndarray) -> np.ndarray:
    """Silence frames whose RMS is below the gate threshold."""
    rms = np.sqrt(np.mean(signal ** 2))
    if rms < _NOISE_GATE_THRESH:
        return np.zeros_like(signal)
    return signal


def _compress(signal: np.ndarray) -> np.ndarray:
    """
    Soft-knee downward compressor.
    Squashes peaks above threshold by ratio, lifting average loudness.
    Result: punchy, controlled — not flat.
    """
    abs_sig = np.abs(signal)
    over = abs_sig > _COMP_THRESHOLD
    gain = np.ones_like(signal)
    # gain reduction only on samples above threshold
    gain[over] = (
        _COMP_THRESHOLD + (abs_sig[over] - _COMP_THRESHOLD) / _COMP_RATIO
    ) / abs_sig[over]
    return signal * gain


def _soft_clip(signal: np.ndarray) -> np.ndarray:
    """
    tanh soft clipper — no hard edge, no fati awaaz.
    Drive controls how aggressively it saturates.
    Output range stays -1..1.
    """
    return np.tanh(signal * _SOFT_CLIP_DRIVE) / np.tanh(_SOFT_CLIP_DRIVE)


def _process_audio(pcm_frames: list[np.ndarray], gain: float) -> bytes:
    """
    Full DSP chain:
      mix → normalize → pre-emphasis → noise gate →
      gain → compress → soft-clip → int16 out
    """
    size   = max(len(c) for c in pcm_frames)
    mixed  = np.zeros(size, dtype=np.float64)
    for chunk in pcm_frames:
        mixed[:len(chunk)] += chunk
    # average mix, normalize to -1..1 float
    mixed /= len(pcm_frames)
    peak = np.max(np.abs(mixed)) or 1.0
    normalized = mixed / 32768.0          # int16 → float

    # DSP chain
    sig = _pre_emphasis(normalized)
    sig = _noise_gate(sig)
    sig *= gain                           # Kovak's level multiplier
    sig = _compress(sig)
    sig = _soft_clip(sig)                 # clean saturation, never fata hua

    return (sig * 32767).astype("<i2").tobytes()


@dataclass
class BridgeInfo:
    source_id: int
    target_id: int
    source_name: str
    target_name: str


class VoiceRuntime:
    """One hosted user account; source VC audio is relayed one-way to target VC."""

    def __init__(self, user_id: int, session: str, settings: Settings):
        self.user_id  = user_id
        self.session  = session
        self.settings = settings
        self.client   = None
        self.calls    = None
        self.bridge   = None
        self.level    = 8.0          # default: 8x gain — dominant from the start
        self.lock     = asyncio.Lock()
        self.started  = False

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

            chunks = [
                np.frombuffer(frame.frame, dtype="<i2").astype(np.float64)
                for frame in update.frames if frame.frame
            ]
            if not chunks:
                return

            pcm = _process_audio(chunks, self.level)

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
            await self.calls.play(source_id, MediaStream(ExternalMedia.AUDIO, AUDIO))
            await self.calls.record(
                source_id, RecordStream(audio=True, audio_parameters=AUDIO)
            )
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

    async def stop(self):
        await self.stop_bridge()
        if self.client is not None and self.client.is_connected():
            await self.client.disconnect()
        self.started = False
