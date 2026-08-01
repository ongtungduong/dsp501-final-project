import { useCallback, useEffect, useState } from 'react'
import { matchAudio, getSpectrogramUrl, ApiError } from './api/client'
import { useRecorder } from './audio/use-recorder'
import { RecordButton } from './components/record-button'
import { MatchResult, type MatchResultState } from './components/match-result'
import { SongList } from './components/song-list'

const RECORD_SECONDS = 8

function App() {
  const [matchState, setMatchState] = useState<MatchResultState>({ kind: 'idle' })
  const [lastRecordingBlob, setLastRecordingBlob] = useState<Blob | null>(null)
  const [showSpectrogram, setShowSpectrogram] = useState(false)
  const [spectrogramUrl, setSpectrogramUrl] = useState<string | null>(null)
  const [spectrogramError, setSpectrogramError] = useState<string | null>(null)
  const [spectrogramLoading, setSpectrogramLoading] = useState(false)

  const handleRecordingComplete = useCallback((blob: Blob) => {
    setLastRecordingBlob(blob)
    setMatchState({ kind: 'loading' })
    matchAudio(blob)
      .then((response) => {
        setMatchState(response.match ? { kind: 'matched', match: response.match } : { kind: 'not-found' })
      })
      .catch((err: unknown) => {
        setMatchState({ kind: 'error', message: describeApiError(err) })
      })
  }, [])

  const {
    state: recorderState,
    start,
    stop,
    error: recorderError,
  } = useRecorder({ seconds: RECORD_SECONDS, onComplete: handleRecordingComplete })

  // Fetch the spectrogram whenever the toggle is on and a fresh recording
  // exists. Re-runs automatically when a new recording replaces the blob.
  useEffect(() => {
    if (!showSpectrogram || !lastRecordingBlob) {
      return
    }
    let cancelled = false
    setSpectrogramLoading(true)
    setSpectrogramError(null)
    getSpectrogramUrl(lastRecordingBlob)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url)
          return
        }
        setSpectrogramUrl(url)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setSpectrogramError(describeApiError(err))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setSpectrogramLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [showSpectrogram, lastRecordingBlob])

  // Revoke the previous object URL whenever it's replaced or the component unmounts.
  useEffect(() => {
    return () => {
      if (spectrogramUrl) {
        URL.revokeObjectURL(spectrogramUrl)
      }
    }
  }, [spectrogramUrl])

  return (
    <main className="app">
      <header className="app__header">
        <h1>Nhận diện nhạc DSP501</h1>
        <p>Thu {RECORD_SECONDS} giây âm thanh và tìm bài khớp trong kho nhạc.</p>
      </header>

      <RecordButton state={recorderState} seconds={RECORD_SECONDS} onStart={start} onStop={stop} />

      {recorderError && (
        <p className="app__recorder-error" role="alert">
          {recorderError}
        </p>
      )}

      <MatchResult state={matchState} />

      <label className="app__spectrogram-toggle">
        <input
          type="checkbox"
          checked={showSpectrogram}
          onChange={(event) => setShowSpectrogram(event.target.checked)}
          disabled={!lastRecordingBlob}
        />
        Hiện ảnh phổ (spectrogram) của đoạn vừa thu
      </label>

      {showSpectrogram && (
        <div className="app__spectrogram">
          {spectrogramLoading && <p>Đang tải ảnh phổ...</p>}
          {spectrogramError && (
            <p role="alert" className="app__spectrogram-error">
              {spectrogramError}
            </p>
          )}
          {spectrogramUrl && !spectrogramLoading && (
            <img src={spectrogramUrl} alt="Ảnh phổ tần số của đoạn âm thanh vừa thu" />
          )}
        </div>
      )}

      <SongList />
    </main>
  )
}

function describeApiError(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message
  }
  return err instanceof Error ? err.message : 'Lỗi không xác định.'
}

export default App
