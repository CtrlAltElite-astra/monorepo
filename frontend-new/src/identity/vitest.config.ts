// The identity folder's own tests (the console's vitest config only collects tests/**). From frontend-new:
//   npx vitest run --config src/identity/vitest.config.ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: { environment: 'node', include: ['src/identity/**/*.test.{ts,tsx}'] },
})
