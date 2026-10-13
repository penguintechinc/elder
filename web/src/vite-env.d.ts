/// <reference types="vite/client" />
// vitest.config.ts sets globals:true, so describe/it/expect are ambient at
// runtime; without this tsc errors on every test file that uses them.
/// <reference types="vitest/globals" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
