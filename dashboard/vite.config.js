import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The wallet reuses the partner's credential package (collaboration/true) directly: same SD-JWT code,
// same selective disclosure, same holder-binding. Its dependencies resolve from collaboration/true/node_modules.
const here = path.dirname(fileURLToPath(import.meta.url))
const TRUE = path.resolve(here, '../collaboration/true')

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@true/credentials': path.join(TRUE, 'packages/credentials/src/index.ts'),
      '@true/syncshield-shared': path.join(TRUE, 'packages/shared/src/syncshield.ts'),
    },
  },
  server: { fs: { allow: [path.resolve(here, '..')] } },
})
