import { describe, expect, it } from 'vitest'
import { encodeWav } from './encode-wav'

async function readHeader(blob: Blob): Promise<DataView> {
  const buffer = await blob.arrayBuffer()
  return new DataView(buffer)
}

function readAscii(view: DataView, offset: number, length: number): string {
  let out = ''
  for (let i = 0; i < length; i++) {
    out += String.fromCharCode(view.getUint8(offset + i))
  }
  return out
}

describe('encodeWav', () => {
  it('writes a valid 44-byte RIFF/WAVE header for 16-bit mono PCM', async () => {
    const samples = new Float32Array([0, 0.5, -0.5])
    const blob = encodeWav(samples, 11025)
    expect(blob.type).toBe('audio/wav')

    const view = await readHeader(blob)
    expect(readAscii(view, 0, 4)).toBe('RIFF')
    expect(readAscii(view, 8, 4)).toBe('WAVE')
    expect(readAscii(view, 12, 4)).toBe('fmt ')
    expect(view.getUint16(20, true)).toBe(1) // PCM format code
    expect(view.getUint16(22, true)).toBe(1) // mono
    expect(view.getUint32(24, true)).toBe(11025) // sample rate
    expect(view.getUint16(34, true)).toBe(16) // bits per sample
    expect(readAscii(view, 36, 4)).toBe('data')

    const dataSize = samples.length * 2
    expect(view.getUint32(40, true)).toBe(dataSize)
    expect(view.getUint32(4, true)).toBe(36 + dataSize)
    expect(blob.size).toBe(44 + dataSize)
  })

  it('scales in-range samples to 16-bit PCM without distortion', async () => {
    const samples = new Float32Array([1, -1, 0])
    const blob = encodeWav(samples, 11025)
    const view = await readHeader(blob)
    expect(view.getInt16(44, true)).toBe(32767) // +1.0 -> max positive int16
    expect(view.getInt16(46, true)).toBe(-32768) // -1.0 -> max negative int16
    expect(view.getInt16(48, true)).toBe(0)
  })

  it('clamps out-of-range samples instead of wrapping around', async () => {
    // A stray sample above 1.0 or below -1.0 (e.g. from analog headroom)
    // must clamp to the same values as the true full-scale ones — if it
    // wrapped instead, it would inject an audible click into the WAV.
    const samples = new Float32Array([1.8, -2.3])
    const blob = encodeWav(samples, 11025)
    const view = await readHeader(blob)
    expect(view.getInt16(44, true)).toBe(32767)
    expect(view.getInt16(46, true)).toBe(-32768)
  })

  it('produces an empty data chunk for an empty sample array', async () => {
    const blob = encodeWav(new Float32Array(0), 11025)
    expect(blob.size).toBe(44)
    const view = await readHeader(blob)
    expect(view.getUint32(40, true)).toBe(0)
  })
})
