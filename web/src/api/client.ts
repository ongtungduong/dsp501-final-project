// Typed client for the FastAPI backend. Types here are the single source of
// truth for the API shape on the frontend — mirror server-side schema
// changes here and nowhere else.

export type MatchStrength = 'strong' | 'moderate' | 'weak'

export interface MatchInfo {
  songId: number
  title: string
  artist: string | null
  score: number
  /**
   * NOT a confidence percentage. ~0.18 for a flawless match, ~0.02 for a
   * real room recording. Never render this as "18% confident" — use
   * `strength` for the human-facing verdict.
   */
  alignedFraction: number
  strength: MatchStrength
  offsetSeconds: number
}

export interface MatchResponse {
  match: MatchInfo | null
  queryHashes: number
  elapsedMs: number
}

export interface Song {
  id: number
  title: string
  artist: string | null
  duration: number | null
}

export interface SongsResponse {
  items: Song[]
  total: number
  limit: number
  offset: number
}

export interface HealthResponse {
  status: string
  songs: number
  fingerprints: number
  database: string
}

export interface GetSongsParams {
  limit?: number
  offset?: number
  q?: string
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function requestJson<T>(input: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(input, init)
  } catch {
    // fetch() rejects on network failure (server down, DNS, offline) — this
    // is the expected "backend genuinely unreachable" case, not a bug.
    throw new ApiError('Không kết nối được máy chủ. Kiểm tra backend đã chạy chưa.', 0)
  }
  if (!response.ok) {
    throw new ApiError(await extractDetail(response), response.status)
  }
  return (await response.json()) as T
}

async function extractDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (
      body !== null &&
      typeof body === 'object' &&
      'detail' in body &&
      typeof (body as { detail: unknown }).detail === 'string'
    ) {
      return (body as { detail: string }).detail
    }
  } catch {
    // Response body wasn't JSON — fall through to the status-specific message.
  }
  return fallbackDetailForStatus(response.status)
}

// The server always sends `{"detail": "..."}` in Vietnamese for these cases,
// so this only fires if that body is somehow missing or malformed.
function fallbackDetailForStatus(status: number): string {
  if (status === 413) {
    return 'Đoạn ghi âm quá lớn để tải lên.'
  }
  if (status === 422) {
    return 'Âm thanh bị lỗi hoặc quá ngắn (dưới 1 giây).'
  }
  return `Máy chủ trả lỗi ${status}`
}

export function matchAudio(blob: Blob): Promise<MatchResponse> {
  const formData = new FormData()
  formData.append('file', blob, 'query.wav')
  return requestJson<MatchResponse>('/api/match', { method: 'POST', body: formData })
}

export function getSongs(params: GetSongsParams = {}): Promise<SongsResponse> {
  const search = new URLSearchParams()
  if (params.limit !== undefined) search.set('limit', String(params.limit))
  if (params.offset !== undefined) search.set('offset', String(params.offset))
  if (params.q) search.set('q', params.q)
  const query = search.toString()
  return requestJson<SongsResponse>(`/api/songs${query ? `?${query}` : ''}`)
}

export function getHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>('/api/health')
}

/**
 * Uploads the given audio blob and resolves to an object URL for the
 * returned PNG spectrogram. Caller owns the URL and must revoke it with
 * `URL.revokeObjectURL` once it's no longer displayed, to avoid leaking
 * memory across repeated recordings.
 */
export async function getSpectrogramUrl(blob: Blob): Promise<string> {
  const formData = new FormData()
  formData.append('file', blob, 'query.wav')

  let response: Response
  try {
    response = await fetch('/api/spectrogram', { method: 'POST', body: formData })
  } catch {
    throw new ApiError('Không kết nối được máy chủ khi tải ảnh phổ.', 0)
  }
  if (!response.ok) {
    throw new ApiError(await extractDetail(response), response.status)
  }
  const imageBlob = await response.blob()
  return URL.createObjectURL(imageBlob)
}
