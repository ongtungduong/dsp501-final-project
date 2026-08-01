import { useEffect, useState } from 'react'
import { getSongs, type Song } from '../api/client'
import { formatSeconds } from '../utils/format-time'

const PAGE_SIZE = 50

type LoadState = 'loading' | 'ready' | 'error'

export function SongList() {
  const [songs, setSongs] = useState<Song[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState('')
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [errorMessage, setErrorMessage] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoadState('loading')
    getSongs({ limit: PAGE_SIZE, q: query || undefined })
      .then((response) => {
        if (cancelled) return
        setSongs(response.items)
        setTotal(response.total)
        setLoadState('ready')
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setErrorMessage(err instanceof Error ? err.message : 'Không tải được danh sách kho nhạc.')
        setLoadState('error')
      })
    return () => {
      cancelled = true
    }
  }, [query])

  return (
    <section className="song-list">
      <h2>Kho nhạc</h2>
      <input
        type="search"
        className="song-list__search"
        placeholder="Tìm theo tên bài hoặc nghệ sĩ..."
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        aria-label="Tìm bài trong kho nhạc"
      />
      {loadState === 'loading' && <p>Đang tải danh sách...</p>}
      {loadState === 'error' && (
        <p role="alert" className="song-list__error">
          {errorMessage}
        </p>
      )}
      {loadState === 'ready' && (
        <>
          <p className="song-list__count">
            Hiển thị {songs.length} / {total} bài
          </p>
          {songs.length === 0 ? (
            <p>Không có bài nào khớp tìm kiếm.</p>
          ) : (
            <table className="song-list__table">
              <thead>
                <tr>
                  <th>Tên bài</th>
                  <th>Nghệ sĩ</th>
                  <th>Thời lượng</th>
                </tr>
              </thead>
              <tbody>
                {songs.map((song) => (
                  <tr key={song.id}>
                    <td>{song.title}</td>
                    <td>{song.artist ?? 'Không rõ'}</td>
                    {/* An unknown duration shows as a gap, not as 0:00 —
                        a plausible wrong number is worse than a visible blank. */}
                    <td>{song.duration === null ? '—' : formatSeconds(song.duration)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  )
}
