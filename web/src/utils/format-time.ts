/** Formats a duration/offset in seconds as `m:ss`. Shared by match-result and song-list. */
export function formatSeconds(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(total / 60)
  const secs = total % 60
  return `${minutes}:${secs.toString().padStart(2, '0')}`
}
