import { useCallback, useEffect, useRef, useState } from 'react'
import { encodeWav } from './encode-wav'
// `?worker&url` routes the file through Vite's actual worker/TS compile
// pipeline (unlike plain `?url`, which just copies the raw source as a
// static asset — verified against a real build: that emitted literal
// TypeScript, which is not valid JS and throws when the browser parses it)
// and hands back the compiled chunk's URL, which is what
// AudioContext.audioWorklet.addModule needs.
import recorderWorkletUrl from './recorder-worklet.ts?worker&url'

export type RecorderState = 'idle' | 'requesting' | 'recording' | 'processing'

interface UseRecorderOptions {
  /** Auto-stop after this many seconds. Defaults to 8. */
  seconds?: number
  /** Called with the encoded WAV blob once a recording session finishes. */
  onComplete: (blob: Blob) => void
}

interface UseRecorderResult {
  state: RecorderState
  start: () => void
  stop: () => void
  error: string | null
}

const DEFAULT_SECONDS = 8

export function useRecorder({ seconds = DEFAULT_SECONDS, onComplete }: UseRecorderOptions): UseRecorderResult {
  const [state, setStateRaw] = useState<RecorderState>('idle')
  const [error, setError] = useState<string | null>(null)

  // React may invoke functional setState updaters more than once (e.g.
  // StrictMode double-invoke), so `start`/`stop` must not rely on a
  // functional updater to guard side effects. Mirror the state into a ref
  // that is always synchronously current instead.
  const stateRef = useRef<RecorderState>('idle')
  const setState = useCallback((next: RecorderState) => {
    stateRef.current = next
    setStateRaw(next)
  }, [])

  const audioContextRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const workletRef = useRef<AudioWorkletNode | null>(null)
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const chunksRef = useRef<Float32Array[]>([])
  const autoStopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Incremented by teardown so a start() suspended at an await can tell that
  // its session was abandoned and release the microphone itself.
  const generationRef = useRef(0)
  // onComplete is likely a fresh closure on every render — stash it in a ref
  // so the recording callbacks below don't need it in their dependency array.
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  // Releases the microphone and the AudioContext. Must run on every exit
  // path (stop, auto-stop, setup failure, unmount) — a missed path leaves
  // the browser's mic indicator lit indefinitely.
  const teardown = useCallback(() => {
    if (autoStopTimerRef.current !== null) {
      clearTimeout(autoStopTimerRef.current)
      autoStopTimerRef.current = null
    }
    if (workletRef.current) {
      workletRef.current.port.onmessage = null
      workletRef.current.port.close()
      workletRef.current.disconnect()
      workletRef.current = null
    }
    sourceRef.current?.disconnect()
    sourceRef.current = null
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      void audioContextRef.current.close()
    }
    audioContextRef.current = null
    // Invalidate any start() still suspended at an await.
    generationRef.current += 1
  }, [])

  const finish = useCallback(() => {
    setState('processing')
    const sampleRateAtCapture = audioContextRef.current?.sampleRate ?? 0
    const chunks = chunksRef.current
    chunksRef.current = []
    teardown()

    const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0)
    if (sampleRateAtCapture > 0 && totalLength > 0) {
      const merged = new Float32Array(totalLength)
      let offset = 0
      for (const chunk of chunks) {
        merged.set(chunk, offset)
        offset += chunk.length
      }
      onCompleteRef.current(encodeWav(merged, sampleRateAtCapture))
    } else {
      // Reachable when the audio context never started producing callbacks.
      // Saying so is essential: the alternative is the user watching the
      // countdown run to zero and then nothing happening at all — no result,
      // no error, no reason. Silence is the one outcome this interface must
      // never produce.
      setError(
        'Không thu được âm thanh nào. Kiểm tra micro trong System Settings › ' +
          'Privacy & Security › Microphone, rồi thử lại.',
      )
    }
    setState('idle')
  }, [teardown, setState])

  const stop = useCallback(() => {
    if (stateRef.current !== 'recording') {
      return
    }
    finish()
  }, [finish])

  const start = useCallback(() => {
    if (stateRef.current !== 'idle') {
      return
    }
    setError(null)
    chunksRef.current = []
    setState('requesting')

    // Bumped on teardown so an in-flight start() knows it was cancelled. The
    // async work below suspends at two awaits; without this, unmounting during
    // either one leaves the microphone open, because teardown runs while every
    // ref is still null and therefore has nothing to release.
    const generation = ++generationRef.current
    const cancelled = () => generationRef.current !== generation

    void (async () => {
      let stream: MediaStream
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          // Browsers enable echo cancellation, noise suppression and automatic
          // gain control by default for microphone tracks. All three are signal
          // processing applied before the samples reach us, and all three fight
          // this specific task: AGC applies a time-varying gain that the
          // server's static normalisation cannot undo, noise suppression
          // attenuates exactly the steady tonal partials that peak picking
          // selects, and echo cancellation actively removes audio correlated
          // with what the machine is playing — which is the demo, music from a
          // nearby speaker. Design decision #1 says the browser sends raw
          // samples; this is what makes that true in practice.
          audio: {
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false,
          },
        })
      } catch (err) {
        setState('idle')
        setError(describeGetUserMediaError(err))
        return
      }

      if (cancelled()) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream

      try {
        const context = new AudioContext()
        audioContextRef.current = context
        // Creating the context after an await breaks the synchronous
        // user-gesture chain, so it can start suspended — and a suspended
        // context never calls process(), which would leave the recording empty
        // with no error anywhere. Resuming explicitly costs nothing when the
        // context is already running.
        if (context.state === 'suspended') {
          await context.resume()
        }
        await context.audioWorklet.addModule(recorderWorkletUrl)

        if (cancelled()) {
          teardown()
          return
        }

        const source = context.createMediaStreamSource(stream)
        sourceRef.current = source

        const worklet = new AudioWorkletNode(context, 'recorder-processor', {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          channelCount: 1,
          channelCountMode: 'explicit',
        })
        workletRef.current = worklet
        worklet.port.onmessage = (event: MessageEvent<Float32Array>) => {
          chunksRef.current.push(event.data)
        }

        source.connect(worklet)
        // The rendering graph only pulls nodes reachable from the
        // destination. The processor never writes to its output (silence by
        // default), so this doesn't create audible feedback — it just keeps
        // the worklet actively processing for the whole session.
        worklet.connect(context.destination)

        setState('recording')
        autoStopTimerRef.current = setTimeout(finish, seconds * 1000)
      } catch (err) {
        teardown()
        setState('idle')
        setError(
          err instanceof Error
            ? `Không thể khởi tạo bộ thu âm: ${err.message}`
            : 'Không thể khởi tạo bộ thu âm.',
        )
      }
    })()
  }, [seconds, finish, teardown, setState])

  // Cleanup on unmount even if a recording is in progress.
  useEffect(() => teardown, [teardown])

  return { state, start, stop, error }
}

function describeGetUserMediaError(err: unknown): string {
  if (err instanceof DOMException) {
    if (err.name === 'NotAllowedError') {
      return 'Bạn đã từ chối quyền truy cập micro. Hãy cấp quyền micro cho trang này trong phần cài đặt trình duyệt rồi thử lại.'
    }
    if (err.name === 'NotFoundError') {
      return 'Không tìm thấy micro trên thiết bị này. Hãy kết nối micro rồi thử lại.'
    }
  }
  return 'Không thể truy cập micro. Kiểm tra lại thiết bị và quyền truy cập rồi thử lại.'
}
