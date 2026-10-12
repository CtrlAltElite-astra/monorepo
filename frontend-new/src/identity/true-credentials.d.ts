// Types for the partner's credential package (collaboration/true/packages/credentials/src/index.ts), used only by
// wallet.ts. The module itself is resolved by a Vite alias that the console adds when it mounts the identity panel,
// exactly as dashboard/vite.config.js does; until then nothing in the build imports wallet.ts.
declare module '@true/credentials' {
  type Jwk = Record<string, unknown>
  export function generateHolderKey(): Promise<{ privateKey: CryptoKey; publicJwk: Jwk }>
  export function createHolderProof(input: {
    holderPrivateKey: CryptoKey; holderPublicJwk: Jwk; nonce: string; audience: string
  }): Promise<string>
  export function createPresentation(input: {
    credential: string; holderPrivateKey: CryptoKey; claims: string[]; nonce: string; audience: string
  }): Promise<string>
  export function readCredential(credential: string): Promise<Record<string, unknown>>
}
