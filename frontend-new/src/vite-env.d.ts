/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MODE?: 'mock' | 'live'
  readonly VITE_REGION_URLS?: string
  readonly VITE_CHAOS_URL?: string
  readonly VITE_OFFICER_KEY?: string
  readonly VITE_SUPERVISOR_KEY?: string
  readonly VITE_ADMIN_KEY?: string
}
