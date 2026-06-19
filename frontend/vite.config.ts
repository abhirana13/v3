import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In docker-compose the backend is reachable at http://backend:8000 over the
// compose network; on the host it's http://localhost:8001. Override with
// VITE_API_TARGET. The app calls /api/* and the proxy strips the /api prefix.
const target = process.env.VITE_API_TARGET || 'http://backend:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
