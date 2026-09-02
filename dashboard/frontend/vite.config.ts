import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 4173,
    proxy: {
      '/ws': { target: 'ws://localhost:8765', ws: true },
      '/api': { target: 'http://localhost:8765' },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
