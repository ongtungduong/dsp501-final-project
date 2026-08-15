/// Peak-normalises a captured WAV clip before upload.
///
/// Mirrors `signal_to_wav_bytes` in desktop-app/app.py: find the loudest
/// absolute sample, divide the whole signal by it so the new peak is
/// `1.0`, then re-quantise back to int16 via the WAV writer.
///
/// Why we need this on mobile:
///   - The Android mic input gain depends on the device, the OS
///     media/voice volume, and how close the user is to the speaker.
///   - The fingerprint matcher relies on absolute peak positions in
///     the spectrogram, not on relative loudness, so a quiet recording
///     can drop below the matcher's noise floor even when the song is
///     clearly audible.
///   - The desktop client already normalises; without the same step
///     here the mobile client scores lower than the desktop client
///     for the same song played at the same volume.
library;

import 'dart:typed_data';

import 'package:wav/wav.dart';

class WavNormalizer {
  /// Decode a WAV byte buffer, divide every sample by the absolute peak
  /// so the new peak sits at ±1.0, and re-encode as 16-bit PCM WAV.
  ///
  /// Empty or silent input is returned unchanged — there is no peak to
  /// normalise against and any divisor would be zero or undefined.
  ///
  /// Throws [WavFormatException] when the buffer is not a recognisable
  /// WAV; the caller surfaces that as a Vietnamese error message.
  static Uint8List peakNormalize(Uint8List wavBytes) {
    final wav = Wav.read(wavBytes);

    // `Wav.read` exposes channels as Float64List normalised to [-1, 1].
    // We pull them out into one mono buffer (the recorder is already
    // mono, but mixing down defensively keeps the contract clean if a
    // future encoder flips to stereo).
    final mono = wav.toMono();
    if (mono.isEmpty) return wavBytes;

    double peak = 0.0;
    for (final sample in mono) {
      final abs = sample.abs();
      if (abs > peak) peak = abs;
    }
    if (peak <= 0.0) return wavBytes;

    // Divide in place. The ratio is bounded by [0, 1] so values stay in
    // range — no clipping needed.
    final scale = 1.0 / peak;
    for (int i = 0; i < mono.length; i++) {
      mono[i] = mono[i] * scale;
    }

    final normalised = Wav([mono], wav.samplesPerSecond, wav.format);
    return normalised.write();
  }

  /// Wrap [peakNormalize] for callers that already carry the bytes as a
  /// `List<int>`. The HTTP client takes `List<int>` so this is the
  /// shape that flows through `recorder_service` and `home_screen`.
  static List<int> peakNormalizeList(List<int> wavBytes) {
    return peakNormalize(Uint8List.fromList(wavBytes));
  }

  /// Diagnostic helper — returns the absolute peak of the input. Useful
  /// for the test suite to assert the normaliser actually did work
  /// (peak after ≈ 1.0).
  static double measurePeak(List<int> wavBytes) {
    final wav = Wav.read(Uint8List.fromList(wavBytes));
    final mono = wav.toMono();
    double peak = 0.0;
    for (final sample in mono) {
      final abs = sample.abs();
      if (abs > peak) peak = abs;
    }
    return peak;
  }

  /// `true` when the byte buffer looks like a RIFF/WAVE header. Cheap
  /// pre-check so we fail fast on a corrupt recording instead of
  /// throwing deep inside `Wav.read`.
  static bool looksLikeWav(List<int> bytes) =>
      bytes.length >= 12 &&
      bytes[0] == 0x52 && // R
      bytes[1] == 0x49 && // I
      bytes[2] == 0x46 && // F
      bytes[3] == 0x46 && // F
      bytes[8] == 0x57 && // W
      bytes[9] == 0x41 && // A
      bytes[10] == 0x56 && // V
      bytes[11] == 0x45; // E
}
