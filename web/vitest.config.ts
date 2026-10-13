import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    // Unit tests live in src/; Playwright e2e specs (tests/e2e) run via `playwright test`.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['node_modules', 'dist', 'tests/e2e/**'],
    coverage: {
      provider: 'v8',
      // 90% org-wide gate (critical-rules.md Coverage). Not yet measured on
      // this branch — no CI step runs `vitest run --coverage` today (ci.yml
      // web-build only does tsc --noEmit + build) and `npm ci` wasn't run
      // here to measure cheaply. This has no CI teeth until a coverage step
      // is wired in; running `npm run test:coverage` locally will reveal the
      // true floor to ratchet from if 90 proves unreachable immediately.
      thresholds: {
        lines: 90,
        branches: 90,
        functions: 90,
        statements: 90,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
