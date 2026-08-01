# Web app (DSP501 Shazam Clone)

React + TypeScript + Vite. Records raw microphone audio and sends it to the
FastAPI backend for matching — the client does zero DSP (see plan decision
#1 in `plans/260731-1847-shazam-clone-dsp/plan.md`).

## Develop

```bash
pnpm install
pnpm dev
```

The dev server proxies `/api` to `http://localhost:8000` (see
`vite.config.ts`), so the FastAPI backend must be running separately.

## Build / test

```bash
pnpm build   # tsc -b && vite build
pnpm test    # vitest
```

## Layout

- `src/audio/` — mic capture: `encode-wav.ts` (pure WAV encoder),
  `recorder-worklet.ts` (AudioWorkletProcessor), `use-recorder.ts` (React
  hook wrapping the recording lifecycle)
- `src/api/client.ts` — typed fetch wrappers for the backend, single source
  of truth for the API shape on this side
- `src/components/` — `record-button`, `match-result`, `song-list`
- `src/styles.css` — plain CSS, no UI library
