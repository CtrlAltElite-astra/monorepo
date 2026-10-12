import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Auth mode (VITE_AUTH_ENABLED=1): the wallet reuses the partner's credential package (collaboration/true)
// directly: same SD-JWT code, same selective disclosure, same holder-binding. Its dependencies resolve from
// collaboration/true/node_modules, so that folder needs `npm install` first.
//
// Normal build (auth off, the default): the two @true/* names point at a tiny stub, so the dashboard builds and
// runs with NOTHING installed under collaboration/true.
const here = path.dirname(fileURLToPath(import.meta.url))
const TRUE = path.resolve(here, '../collaboration/true')
const STUB = path.join(here, 'src/auth/true-stub.js')

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, here, 'VITE_'), ...process.env }
  const authOn = env.VITE_AUTH_ENABLED === '1'
  if (authOn && !fs.existsSync(path.join(TRUE, 'node_modules'))) {
    throw new Error('VITE_AUTH_ENABLED=1 needs the TRUE packages: run `npm install` in collaboration/true first.')
  }
  return {
    plugins: [react()],
    resolve: {
      alias: authOn
        ? {
            '@true/credentials': path.join(TRUE, 'packages/credentials/src/index.ts'),
            '@true/syncshield-shared': path.join(TRUE, 'packages/shared/src/syncshield.ts'),
          }
        : { '@true/credentials': STUB, '@true/syncshield-shared': STUB },
    },
    server: { fs: { allow: [path.resolve(here, '..')] } },
  }
})
