import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The browser only ever talks to the Vite dev server (localhost:5173).
// Anything under /api is forwarded to Spring Boot, with the /api prefix removed,
// so the backend sees plain paths like /products and never needs CORS config.
// Image uploads are the exception: they go straight from the browser to MinIO.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.BACKEND_URL || 'http://localhost:8080',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
