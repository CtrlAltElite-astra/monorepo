/// <reference types="vitest" />
import fs from 'node:fs'
import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Sign-in (VITE_AUTH_ENABLED=1): the wallet reuses the partner's credential package (collaboration/true): the same
// SD-JWT code, selective disclosure and holder binding. Its dependencies resolve from collaboration/true/node_modules,
// so that folder needs `npm install` first. A normal build points the name at a stub and needs nothing installed.
const here = process.cwd()
const TRUE = path.resolve(here, '../collaboration/true')
const STUB = path.join(here, 'src/identity/true-stub.ts')

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env } as Record<string, string | undefined>
  const authOn = env.VITE_AUTH_ENABLED === '1'
  if (authOn && !fs.existsSync(path.join(TRUE, 'node_modules'))) {
    throw new Error('VITE_AUTH_ENABLED=1 needs the TRUE packages: run `npm install` in collaboration/true first.')
  }
  const proxy = Object.fromEntries(['mum', 'pun', 'del', 'blr'].map((id, i) => [`/api/${id}`, {
    target: env[`${id.toUpperCase()}_BACKEND_URL`] || `http://127.0.0.1:${8001 + i}`,
    changeOrigin: true, rewrite: (p: string) => p.replace(`/api/${id}`, ''),
  }]))
  // sign-in mode: the partner issuer (claim codes) and the loopback revoke relay of scripts/true_stack.py
  proxy['/issuer'] = { target: env.ISSUER_BACKEND_URL || 'http://127.0.0.1:4001', changeOrigin: true, rewrite: (p: string) => p.replace('/issuer', '') }
  proxy['/relay'] = { target: env.RELAY_BACKEND_URL || 'http://127.0.0.1:4090', changeOrigin: true, rewrite: (p: string) => p.replace('/relay', '') }
  proxy['/chaos'] = { target: env.CHAOS_BACKEND_URL || 'http://127.0.0.1:9000', changeOrigin: true, rewrite: (p: string) => p.replace('/chaos', '') }
  const alias = authOn ? { '@true/credentials': path.join(TRUE, 'packages/credentials/src/index.ts') } : { '@true/credentials': STUB }
  return { plugins: [react()], resolve: { alias }, server: { host: '127.0.0.1', port: 5175, strictPort: true, proxy, fs: { allow: [path.resolve(here, '..')] } }, preview: { host: '127.0.0.1', port: 4175, strictPort: true, proxy }, test: { environment: 'node', include: ['tests/**/*.test.ts'] } }
})
