// Pure Float32 -> WAV encoder. No React, no browser-only APIs beyond
// ArrayBuffer/DataView/Blob, so it is trivially unit-testable.
//
// Deliberately hand-rolled instead of using MediaRecorder: MediaRecorder only
// emits webm/opus, which the server's `soundfile` cannot decode, and opus's
// lossy compression smears the spectral peaks the matcher depends on.

const RIFF_HEADER_SIZE = 44
const BYTES_PER_SAMPLE = 2 // 16-bit PCM
const PCM_FORMAT_CODE = 1
const CHANNELS = 1
const BITS_PER_SAMPLE = 16
const MAX_INT16 = 0x7fff
const MIN_INT16_MAGNITUDE = 0x8000

/**
 * Encodes raw mono Float32 samples as a 16-bit PCM WAV file.
 *
 * @param samples audio samples in the [-1, 1] range at `sampleRate`
 * @param sampleRate the sample rate the samples were captured at (no
 *   resampling happens here or anywhere on the client — see plan decision #1)
 */
export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const dataSize = samples.length * BYTES_PER_SAMPLE
  const buffer = new ArrayBuffer(RIFF_HEADER_SIZE + dataSize)
  const view = new DataView(buffer)

  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + dataSize, true)
  writeAscii(view, 8, 'WAVE')

  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true) // fmt chunk size for PCM
  view.setUint16(20, PCM_FORMAT_CODE, true)
  view.setUint16(22, CHANNELS, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * CHANNELS * BYTES_PER_SAMPLE, true) // byte rate
  view.setUint16(32, CHANNELS * BYTES_PER_SAMPLE, true) // block align
  view.setUint16(34, BITS_PER_SAMPLE, true)

  writeAscii(view, 36, 'data')
  view.setUint32(40, dataSize, true)

  let offset = RIFF_HEADER_SIZE
  for (let i = 0; i < samples.length; i++) {
    // Clamp before scaling — an out-of-range sample scaled directly would
    // wrap around int16 and inject a sharp, audible click into the signal.
    const clamped = Math.max(-1, Math.min(1, samples[i]))
    const scaled = Math.round(clamped * (clamped < 0 ? MIN_INT16_MAGNITUDE : MAX_INT16))
    view.setInt16(offset, scaled, true)
    offset += BYTES_PER_SAMPLE
  }

  return new Blob([buffer], { type: 'audio/wav' })
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i++) {
    view.setUint8(offset + i, text.charCodeAt(i))
  }
}
