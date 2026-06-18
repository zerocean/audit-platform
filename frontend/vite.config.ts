import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8767',
    },
    watch: {
      usePolling: true,
      interval: 1000,
    },
  },
})
