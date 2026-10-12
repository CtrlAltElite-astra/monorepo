/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MODE?: 'mock' | 'live'
  readonly VITE_REGION_URLS?: string
  readonly VITE_CHAOS_URL?: string
  readonly VITE_OFFICER_KEY?: string
  readonly VITE_SUPERVISOR_KEY?: string
  readonly VITE_ADMIN_KEY?: string
  /** "Alice=dev-alice,Bob=dev-bob": one API key per shopper, so a region counts each shopper's allowance separately */
  readonly VITE_SHOPPER_KEYS?: string
  /** "1" shows the adults-only item (it is refused until the identity lane's claim check is merged) */
  readonly VITE_RESTRICTED_PRODUCT?: string
}
