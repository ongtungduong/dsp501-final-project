"""Invariants of audio loading and resampling.

Design decision #1 in the plan: every entry point into the system must reach
the identical signal path. If the corpus builder and the query path resample
differently, fingerprints drift apart and recognition quietly degrades while
the system still appears to work. These tests pin that single path down.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
import soundfile as sf

from shazam.audio import AudioLoadError, load
from shazam.config import DspConfig


def _sine(freq: float, seconds: float, sample_rate: int) -> np.ndarray:
    t = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    return np.sin(2.0 * np.pi * freq * t).astype(np.float32)


def _write_wav(path: Path, signal: np.ndarray, sample_rate: int) -> Path:
    sf.write(path, signal, sample_rate)
    return path


def test_load_returns_mono_at_target_rate(tmp_path: Path) -> None:
    config = DspConfig()
    source = _write_wav(tmp_path / "tone.wav", _sine(440.0, 2.0, 44100), 44100)

    signal = load(source, config)

    assert signal.ndim == 1
    assert signal.dtype == np.float32
    # 2 s at 11025 Hz, allowing for filter edge effects in the decimation.
    assert abs(len(signal) - 2 * config.sample_rate) <= config.sample_rate // 100


def test_stereo_is_averaged_to_mono(tmp_path: Path) -> None:
    """Both channels must survive the fold-down, not just whichever comes first.

    Asserting only ``ndim == 1`` would pass for an implementation that returned
    the left channel, the right channel, or zeros — so check that each channel's
    distinct tone is still present in the result.
    """
    config = DspConfig()
    left = _sine(440.0, 1.0, 44100)
    right = _sine(880.0, 1.0, 44100)
    source = _write_wav(tmp_path / "stereo.wav", np.stack([left, right], axis=1), 44100)

    signal = load(source, config)

    assert signal.ndim == 1
    assert _tone_amplitude_db(signal, 440.0, config.sample_rate) > -12.0
    assert _tone_amplitude_db(signal, 880.0, config.sample_rate) > -12.0


def test_mp3_and_wav_decode_to_the_same_signal(tmp_path: Path) -> None:
    """The corpus is MP3 and test fixtures are WAV; both must load identically.

    Also proves libsndfile carries MP3 support, which is what lets the project
    skip an ffmpeg dependency entirely.
    """
    config = DspConfig()
    tone = _sine(1000.0, 1.0, 44100)
    wav_source = _write_wav(tmp_path / "tone.wav", tone, 44100)
    mp3_source = tmp_path / "tone.mp3"
    sf.write(mp3_source, tone, 44100)

    from_wav = load(wav_source, config)
    from_mp3 = load(mp3_source, config)

    assert from_mp3.ndim == 1
    assert from_mp3.dtype == np.float32
    wav_level = _tone_amplitude_db(from_wav, 1000.0, config.sample_rate)
    mp3_level = _tone_amplitude_db(from_mp3, 1000.0, config.sample_rate)
    assert abs(wav_level - mp3_level) < 1.0


def test_undecodable_input_raises_audio_load_error(tmp_path: Path) -> None:
    """The build worker catches AudioLoadError; nothing else may escape.

    FMA is known to contain truncated files. If a decode failure surfaced as
    some other exception type, one bad MP3 would kill a worker mid-build.
    """
    corrupt = tmp_path / "corrupt.mp3"
    corrupt.write_bytes(b"this is not audio")

    with pytest.raises(AudioLoadError):
        load(corrupt)


def test_audio_too_short_to_fingerprint_raises_audio_load_error(tmp_path: Path) -> None:
    """Shorter than one STFT window yields zero frames — fail loudly, not silently."""
    config = DspConfig()
    source = _write_wav(tmp_path / "blip.wav", _sine(440.0, 0.01, 44100), 44100)

    with pytest.raises(AudioLoadError, match="too short"):
        load(source, config)


def test_empty_audio_raises_audio_load_error(tmp_path: Path) -> None:
    source = _write_wav(tmp_path / "empty.wav", np.zeros(0, dtype=np.float32), 44100)

    with pytest.raises(AudioLoadError):
        load(source)


@pytest.mark.parametrize("source_rate", [44100, 48000, 22050, 8000])
def test_every_input_rate_lands_on_the_target_rate(tmp_path: Path, source_rate: int) -> None:
    """44100 divides evenly (decimate); 48000 does not (resample_poly).

    48000 Hz is the case that matters in production: it is what the browser's
    AudioContext hands us on macOS.
    """
    config = DspConfig()
    tone = _sine(440.0, 1.0, source_rate)
    source = _write_wav(tmp_path / f"tone-{source_rate}.wav", tone, source_rate)

    signal = load(source, config)

    assert abs(len(signal) - config.sample_rate) <= config.sample_rate // 50


def test_resampling_preserves_tone_frequency(tmp_path: Path) -> None:
    """The 48 kHz browser path must still put 440 Hz in bin 41."""
    from shazam.stft import stft

    config = DspConfig()
    source = _write_wav(tmp_path / "browser.wav", _sine(440.0, 2.0, 48000), 48000)

    signal = load(source, config)
    dominant_bin = int(np.argmax(stft(signal, config).magnitude.mean(axis=1)))

    assert dominant_bin == 41


def _tone_amplitude_db(signal: npt.NDArray[np.float32], freq: float, sample_rate: int) -> float:
    """Measure the amplitude of one frequency in a signal, in dB relative to full scale."""
    window = np.hanning(len(signal))
    spectrum = np.abs(np.fft.rfft(signal * window))
    freqs = np.fft.rfftfreq(len(signal), 1.0 / sample_rate)
    bin_index = int(np.argmin(np.abs(freqs - freq)))
    return float(20.0 * np.log10(spectrum[bin_index] / (np.sum(window) / 2) + 1e-12))


@pytest.mark.parametrize("freq", [1000.0, 3000.0, 4400.0, 4800.0, 5000.0])
def test_resampling_is_identical_regardless_of_source_rate(tmp_path: Path, freq: float) -> None:
    """Design decision #1, pinned at the filter level.

    The corpus is 44.1 kHz (FMA) and queries arrive at 48 kHz (browser
    AudioContext). If those two rates take differently-shaped anti-alias
    filters, every query is biased against every stored track in whatever band
    the filters disagree — recognition degrades silently while the system still
    appears to work. Both rates must arrive at the same spectrum.
    """
    config = DspConfig()
    corpus_source = _write_wav(tmp_path / "corpus.wav", _sine(freq, 1.0, 44100), 44100)
    query_source = _write_wav(tmp_path / "query.wav", _sine(freq, 1.0, 48000), 48000)

    corpus_db = _tone_amplitude_db(load(corpus_source, config), freq, config.sample_rate)
    query_db = _tone_amplitude_db(load(query_source, config), freq, config.sample_rate)

    assert abs(corpus_db - query_db) < 1.0, (
        f"{freq:.0f} Hz survives the two paths differently: "
        f"corpus {corpus_db:.2f} dB vs query {query_db:.2f} dB"
    )


def test_high_band_is_preserved_up_to_nyquist(tmp_path: Path) -> None:
    """Content below Nyquist must not be filtered away before it is fingerprinted.

    Nyquist at 11025 Hz is 5512 Hz. A 5000 Hz tone is legitimate content and
    has to survive; an anti-alias filter that starts rolling off at 4.4 kHz
    throws away the top ~20% of the usable spectrum.
    """
    config = DspConfig()
    source = _write_wav(tmp_path / "high.wav", _sine(5000.0, 1.0, 44100), 44100)

    level = _tone_amplitude_db(load(source, config), 5000.0, config.sample_rate)

    assert level > -3.0, f"5000 Hz attenuated to {level:.2f} dB — filter cuts in too early"


def test_amplitude_is_normalised(tmp_path: Path) -> None:
    """Recording loudness must not shift signals across the peak threshold."""
    config = DspConfig()
    quiet = _sine(440.0, 1.0, 44100) * 0.01
    source = _write_wav(tmp_path / "quiet.wav", quiet, 44100)

    signal = load(source, config)

    assert 0.9 <= float(np.max(np.abs(signal))) <= 1.0


def test_silence_survives_normalisation_without_dividing_by_zero(tmp_path: Path) -> None:
    config = DspConfig()
    source = _write_wav(tmp_path / "silence.wav", np.zeros(44100, dtype=np.float32), 44100)

    signal = load(source, config)

    assert np.all(np.isfinite(signal))
    assert float(np.max(np.abs(signal))) == 0.0
