"""Microphone capture for ``shazam listen``.

Recording happens at whatever rate the input device prefers, and the samples
then go through :func:`shazam.audio._process` — the same function the corpus
builder uses. Design decision #1: the microphone must not get its own signal
path any more than the browser does.
"""

from __future__ import annotations

import sys

import numpy as np
import numpy.typing as npt

from shazam.audio import _process
from shazam.config import DspConfig

FALLBACK_DEVICE_RATE = 44100


def record(
    seconds: float,
    config: DspConfig | None = None,
    device_rate: int | None = None,
) -> npt.NDArray[np.float32]:
    """Capture ``seconds`` of audio from the default input device.

    Args:
        seconds: How long to record.
        config: DSP parameters.
        device_rate: Rate to request. Defaults to the device's own preferred
            rate — see below for why that matters.

    Returns:
        A mono signal at ``config.sample_rate``, ready to fingerprint.

    Raises:
        RuntimeError: If no input device is available.
    """
    config = config or DspConfig()

    # Imported here, not at module scope: sounddevice binds PortAudio at import
    # time and raises OSError where no audio backend exists — which is every
    # container the API runs in. The API imports this package but never records.
    try:
        import sounddevice
    except OSError as exc:
        raise RuntimeError("No audio backend available for recording") from exc

    if device_rate is None:
        device_rate = _preferred_rate(sounddevice)

    # PortAudio reports a missing or busy device as PortAudioError, which is a
    # plain Exception. Converted here so the documented contract is the real
    # one and callers can catch a single type.
    try:
        frames = int(seconds * device_rate)
        captured = sounddevice.rec(frames, samplerate=device_rate, channels=1, dtype="float32")
        sounddevice.wait()
    except Exception as exc:
        raise RuntimeError(f"Recording failed: {exc}") from exc

    return _process(np.asarray(captured, dtype=np.float32), device_rate, config)


def _preferred_rate(sounddevice: object) -> int:
    """The input device's native rate, falling back if it cannot be determined.

    Asking for a rate the hardware does not run at makes CoreAudio resample
    before we ever see the samples — a third resampling path, invisible from
    here, and precisely what design decision #1 exists to rule out. Recording
    at the device's own rate keeps every conversion inside
    :func:`shazam.audio._process`. MacBook microphones typically report 48 kHz,
    the same rate the browser delivers.

    If the rate cannot be determined we fall back, but say so: the fallback
    reintroduces exactly the hidden conversion this function exists to avoid,
    and silently accepting that would undo the argument above.
    """
    try:
        info = sounddevice.query_devices(kind="input")  # type: ignore[attr-defined]
        return int(float(info["default_samplerate"]))
    except Exception as exc:
        print(
            f"Warning: could not read the input device rate ({exc}); "
            f"requesting {FALLBACK_DEVICE_RATE} Hz. If the device runs at a "
            f"different rate, the OS will resample before we see the audio.",
            file=sys.stderr,
        )
        return FALLBACK_DEVICE_RATE
