import { useEffect, useState } from 'react'
import type { RecorderState } from '../audio/use-recorder'

interface RecordButtonProps {
  state: RecorderState
  seconds: number
  onStart: () => void
  onStop: () => void
}

const STATE_LABEL: Record<RecorderState, string> = {
  idle: 'Nhấn để ghi âm',
  requesting: 'Đang xin quyền micro...',
  recording: 'Đang ghi âm... (nhấn để dừng)',
  processing: 'Đang xử lý...',
}

// Ring circumference for r=54: 2 * PI * 54. Kept in sync with the
// stroke-dasharray set on .record-button__ring-progress in styles.css.
const RING_CIRCUMFERENCE = 339.3

export function RecordButton({ state, seconds, onStart, onStop }: RecordButtonProps) {
  // Bumped on every recording start so the countdown ring animation remounts
  // (via the `key` below) instead of relying on CSS restart quirks, which
  // don't reliably retrigger a keyframe animation on unchanged elements.
  const [session, setSession] = useState(0)

  useEffect(() => {
    if (state === 'recording') {
      setSession((value) => value + 1)
    }
  }, [state])

  const handleClick = (): void => {
    if (state === 'idle') {
      onStart()
    } else if (state === 'recording') {
      onStop()
    }
  }

  const disabled = state === 'requesting' || state === 'processing'

  return (
    <button
      type="button"
      className={`record-button record-button--${state}`}
      onClick={handleClick}
      disabled={disabled}
      aria-label={STATE_LABEL[state]}
    >
      <svg viewBox="0 0 120 120" className="record-button__ring" aria-hidden="true">
        <circle className="record-button__ring-track" cx="60" cy="60" r="54" />
        {state === 'recording' && (
          <circle
            key={session}
            className="record-button__ring-progress"
            cx="60"
            cy="60"
            r="54"
            style={{
              animationDuration: `${seconds}s`,
              strokeDasharray: RING_CIRCUMFERENCE,
            }}
          />
        )}
      </svg>
      <span className="record-button__icon" aria-hidden="true" />
      <span className="record-button__label">{STATE_LABEL[state]}</span>
    </button>
  )
}
