<<<<<<< HEAD
<!-- brand:start -->
# TRUE

**Trusted, Revocable, User-controlled Evidence** · *"Your ID answers TRUE, nothing more."*
<!-- brand:end -->

Privacy-preserving verifiable identity network, built for CodeAstra 2.0 (PS1, Cybersecurity & Blockchain).
A student proves one fact (for example "I am over 18") to a verifier without showing the rest of their ID.

**Status:** M1 of 7 done: issue → wallet → consent → selective disclosure → live verification. Revocation, verifier authentication, audit log and the chain registry come next (see `PLAN.md`).

## Run it

Needs Node 24 or newer.

```
npm install
npm run dev
```

Then open three browser windows:

| Window | Address |
|---|---|
| Issuer console | http://localhost:5174 |
| Wallet | http://localhost:5173 |
| Verifier | http://localhost:5175 |

Other commands: `npm test` (all tests), `npm run spike` (smallest SD-JWT check), `npm run chain:test` (local chain test), `npm run brand` (re-apply the product name to the Markdown headings).

## How it is built

- `packages/shared`: product name, addresses, claim names
- `packages/credentials`: the only place that touches cryptography (a wrapper over libraries, no custom crypto)
- `services/issuer-api`, `services/verifier-api`: two separate APIs with separate SQLite databases
- `apps/wallet`, `apps/issuer-web`, `apps/verifier-web`: the three screens
- `contracts`: local chain (Hardhat)
- `docs/EXPLAIN.md`: plain-language explanation of every part

The verifier never calls the issuer. It checks the credential's signatures against the issuer's public key from the registry.

## Known limitations (so far)

- All data is fake demo data.
- Unlinkability: selective disclosure hides claims, but the issuer's signature and the holder's public key are the same in every presentation, so two verifiers who compare notes could tell it is the same credential. A fix (issuing several one-time copies) is not built.
- See "What is temporary in M1" in `docs/EXPLAIN.md`.

## Attribution

Every third-party library used. All code in this repository was written during the event.

| Library | Used for | Licence |
|---|---|---|
| [@sd-jwt/sd-jwt-vc](https://github.com/openwallet-foundation-labs/identity-common-ts) (OpenWallet Foundation) | SD-JWT VC issue, present, verify | Apache-2.0 |
| [@owf/crypto](https://github.com/openwallet-foundation-labs/identity-common-ts) | ES256 sign/verify, SHA-256, salts | Apache-2.0 |
| [@owf/token-status-list](https://github.com/openwallet-foundation-labs/identity-common-ts) | Revocation status list (installed, used from M2) | Apache-2.0 |
| [jose](https://github.com/panva/jose) | Signed proof of key ownership at issuance | MIT |
| [Hono](https://github.com/honojs/hono) + @hono/node-server | HTTP APIs | MIT |
| [Zod](https://github.com/colinhacks/zod) | Request validation | MIT |
| [React](https://github.com/facebook/react) | User interfaces | MIT |
| [Vite](https://github.com/vitejs/vite) + @vitejs/plugin-react | Front-end dev server and build | MIT |
| [Tailwind CSS](https://github.com/tailwindlabs/tailwindcss) + @tailwindcss/vite | Styling | MIT |
| [qrcode](https://github.com/soldair/node-qrcode) | Drawing QR codes | MIT |
| [Hardhat](https://github.com/NomicFoundation/hardhat) + hardhat-toolbox-viem, [viem](https://github.com/wevm/viem) | Local chain, contract tests | MIT |
| [Vitest](https://github.com/vitest-dev/vitest), [TypeScript](https://github.com/microsoft/TypeScript) | Tests, type checking | MIT, Apache-2.0 |
| Node.js built-ins (`node:sqlite`, WebCrypto) | Database, key generation, randomness | — |

=======
# TRUE
>>>>>>> 6cb48062ca13629d5865b01f46ba2e2cfae7f7c5
