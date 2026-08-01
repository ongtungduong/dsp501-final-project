import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // The recorder worklet compiles to a tiny JS file that would otherwise
    // be inlined as a base64 data: URL (default 4kB threshold). Safari has
    // historically been unreliable loading AudioWorklet modules from data:
    // URLs, so force it — and every other asset — to emit as a real file.
    assetsInlineLimit: 0,
  },
  server: {
    proxy: {
      // Backend runs separately in dev (FastAPI on :8000). Proxying avoids
      // CORS entirely instead of relying on server-side CORS middleware.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
