/// <reference types="vitest" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxy = Object.fromEntries(['mum', 'pun', 'del', 'blr'].map((id, i) => [`/api/${id}`, {
    target: env[`${id.toUpperCase()}_BACKEND_URL`] || `http://127.0.0.1:${8001 + i}`,
    changeOrigin: true, rewrite: (p: string) => p.replace(`/api/${id}`, ''),
  }]))
  proxy['/chaos'] = { target: env.CHAOS_BACKEND_URL || 'http://127.0.0.1:9000', changeOrigin: true, rewrite: (p: string) => p.replace('/chaos', '') }
  return { plugins: [react()], server: { host: '127.0.0.1', port: 5175, strictPort: true, proxy }, preview: { host: '127.0.0.1', port: 4175, strictPort: true, proxy }, test: { environment: 'node', include: ['tests/**/*.test.ts'] } }
})
