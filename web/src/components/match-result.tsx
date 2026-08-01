import type { MatchInfo } from '../api/client'
import { formatSeconds } from '../utils/format-time'

export type MatchResultState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'matched'; match: MatchInfo }
  | { kind: 'not-found' }
  | { kind: 'error'; message: string }

const STRENGTH_LABEL: Record<MatchInfo['strength'], string> = {
  strong: 'Khớp mạnh',
  moderate: 'Khớp trung bình',
  weak: 'Khớp yếu',
}

export function MatchResult({ state }: { state: MatchResultState }) {
  switch (state.kind) {
    case 'idle':
      return (
        <div className="match-result match-result--idle">
          <p>Nhấn nút micro để bắt đầu nhận diện.</p>
        </div>
      )

    case 'loading':
      return (
        <div className="match-result match-result--loading" role="status">
          <p>Đang phân tích đoạn ghi âm...</p>
        </div>
      )

    case 'matched': {
      const { match } = state
      return (
        <div
          className={`match-result match-result--matched match-result--${match.strength}`}
          role="status"
        >
          <p className="match-result__title">{match.title}</p>
          <p className="match-result__artist">{match.artist ?? 'Không rõ nghệ sĩ'}</p>
          <p className="match-result__strength">
            {STRENGTH_LABEL[match.strength]} ·{' '}
            {Math.round(match.alignedFraction * 100)}% đoạn ghi âm khớp vị trí
          </p>
          <p className="match-result__offset">
            Đoạn này ở khoảng giây {formatSeconds(match.offsetSeconds)} của bài
          </p>
        </div>
      )
    }

    case 'not-found':
      return (
        <div className="match-result match-result--not-found" role="status">
          <p className="match-result__title">Không tìm thấy bài này trong kho nhạc.</p>
          <p>
            Thử đặt máy gần loa hơn, giảm tiếng ồn xung quanh, hoặc kiểm tra bài có
            trong danh sách kho bên dưới không.
          </p>
        </div>
      )

    case 'error':
      return (
        <div className="match-result match-result--error" role="alert">
          <p className="match-result__title">Có lỗi xảy ra</p>
          <p>{state.message}</p>
        </div>
      )
  }
}
