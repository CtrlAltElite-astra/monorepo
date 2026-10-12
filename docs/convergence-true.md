# SyncShield × TRUE: sign-in and authorization

Members sign in with a **TRUE credential** (a privacy-preserving digital ID built by the partner team) and get a
short regional session that **every** SyncShield region accepts. That session decides who may write, who may see a
cart and who may resolve a conflict. The distributed engine underneath (append-only signed event log, HLC,
`supersedes`, carry/outbox failover) is unchanged. Auth is **off by default**; nothing here runs unless
`AUTH_ENABLED=true`.

**Owner:** Lane C (Miti Shah). **Status:** works end to end on real processes; see [Test evidence](#10-test-evidence) and
[Honest limits](#13-honest-limits).

## The 30-second explanation

> Members sign in with a TRUE credential, a digital ID that proves "I am an eligible SyncShield officer" without
> revealing name or date of birth. A region checks that proof and hands back a 5-minute session that **every**
> region accepts, verified locally with pinned keys. So killing Mumbai does not log anyone out, and nothing calls
> the issuer on the write path. If someone is removed, a signed status list tells every region within seconds. If
> the issuer is unreachable, regions use the last status they have with strict time limits instead of letting
> everyone in. Roles are enforced on the server: officers touch only their own carts, and only supervisors
> resolve conflicts.

---

## 1. Attribution

| Part | Written by |
|---|---|
| Credential format, issue, holder proof, selective disclosure, verification (`collaboration/true/packages/credentials`) | **The partner team** (CodeAstra 2.0 PS1, "TRUE"). Built on `@sd-jwt/sd-jwt-vc`, `@owf/crypto`, `jose` (open source) |
| Issuer service, wallet UI, verifier service | The partner team |
| SyncShield profile, members/roles/revocation/status snapshot, regional auth sidecar, region-side checks, ownership, dashboard wallet | SyncShield team (Lane C) |
| Event log, signing, sync, write path v2, review/resolve | SyncShield: Lane A (Akshit), Lane B (Rahil), Lane D (Simrit) |

No cryptography was written for this integration. Every credential operation runs through the partner's code.
What was copied unchanged and what was modified is listed file by file in
[`collaboration/true/PROVENANCE.md`](../collaboration/true/PROVENANCE.md).

## 2. Partner functions we reuse (real, not mocked)

| Function (`packages/credentials`) | Used by | For |
|---|---|---|
| `generateHolderKey` | browser wallet, CLI wallet | Holder key pair; in the browser it is a **non-extractable** `CryptoKey` |
| `createHolderProof` / `verifyHolderProof` | wallet / issuer | Proof of key ownership when a credential is claimed |
| `issueCredential` | issuer | ES256-signed SD-JWT VC with `cnf.jwk` holder binding |
| `createPresentation` | wallet | Discloses only the four requested claims, bound to the region's nonce and audience |
| `verifyPresentation` | **auth sidecar** (one per region) | Issuer signature, disclosures match hashes, expiry, holder binding, nonce, audience, proof age |
| `readCredential` | wallet UI | Display only (the consent card). Verifies nothing and is never used to decide anything |

SyncShield adds two small things inside the partner package: a reserved-claims guard in `issueCredential`, and
`verifyPresentation` also returns the **verified** technical fields (`meta`: issuer, expiry, type, holder-bound).

## 3. Processes and ports

```
                         ┌──────────────────────────────┐
                         │ TRUE issuer       :4001      │  membership authority: members, roles,
                         │ (partner code +   admin      │  claim codes, revocation, signed status
                         │  SyncShield routes)          │  snapshot. NOT on the write path.
                         └───────▲──────────────┬───────┘
              claim (once)       │              │ status snapshot (issuer-signed, polled)
 ┌───────────────┐               │              ▼
 │ Browser wallet│        ┌──────┴───────┐   ┌─────────────────────────────────────────────┐
 │  :5173        │        │ auth sidecar │   │ one sidecar per region (loopback only)      │
 │ IndexedDB key │        │ mum  :4101   │   │ verifyPresentation → signs ES256 session    │
 └──────┬────────┘        │ pun  :4102   │   │ writes the verified status file the region  │
        │                 │ del  :4103   │   │ reads from disk                             │
        │ Bearer session  └──────▲───────┘   └─────────────────────────────────────────────┘
        ▼                        │ /internal/v1/{challenge,session} (token-protected, 127.0.0.1)
 ┌─────────────────────────────────────────────────────────────┐
 │ SyncShield regions (FastAPI)   mum :8001  pun :8002  del :8003 │
 │  per request: PyJWT check against PINNED session keys +      │
 │  issuer-signed status snapshot (rollback-protected), local   │
 │  only. Peers replicate events as before (peer token).        │
 └─────────────────────────────────────────────────────────────┘
        ▲ chaos agent :9000 (crash/start, X-Admin-Key)      dashboard :5173
```

The regions never call the issuer or a sidecar to authorize a write. A region without its sidecar can still
authorize every request that arrives with a valid session.

Isolated test ports: issuer 14001/14201/24001, sidecars 14101…/14301…, regions 18101…/18201…/28101…, dashboard 15173.

## 4. Session lifecycle

1. **Claim (once).** An admin registers a member at the issuer and gets a one-time claim code. The member's
   wallet redeems it with a holder proof. The issuer returns an SD-JWT VC containing exactly four claims:
   `actor_id`, `syncshield_member`, `syncshield_role`, `credential_id`. No name, date of birth, address, photo
   or student number.
2. **Challenge.** The wallet asks a region for a challenge. The **region** chooses the nonce, the audience
   `syncshield-region:<region>` and the claims to disclose. The challenge is single-use and expires in 120 s.
3. **Presentation.** The wallet shows a consent card listing those claims, then discloses exactly them.
4. **Verification.** The region's sidecar runs the partner's `verifyPresentation`, checks the profile on the
   **verified** values only (issuer, type, holder binding, expiry, exact claim set, role) and the current status
   (revoked / unknown are refused). The challenge is marked used before the token is returned, including on failure.
5. **Session.** An ES256 JWT (`typ: syncshield-session+jwt`, `aud: syncshield`, `iss: syncshield-region:<region>`),
   valid at most **300 s**, never longer than the credential or the status bound. The browser keeps it in memory only
   and renews 30 s before expiry with a fresh challenge.
6. **Every request.** The region verifies the signature against the pinned key of the issuing region, the header
   against an allowlist, every claim, and then re-evaluates the credential's status. All local.
7. **End.** Logout discards the token client-side. **Revocation** is enforced through the status snapshot, not
   through the token (see section 6).

## 5. Membership and role mapping

| Role (from the credential) | Sees | May write | May resolve | May administer links |
|---|---|---|---|---|
| `officer` | Own carts only. Another member's cart is a **404** (existence does not leak) | Own carts | No (403) | No (403) |
| `supervisor` | Every cart and the whole review queue | Own carts only (403 `not_your_cart` on others) | **Yes** | No (403) |
| `admin` | Admin surface | Nothing (not a shopper) | No (403) | **Yes** |

- The actor and role always come from the verified session, never from the request body or a header.
- The cart **owner** is fixed in the signed `CREATE` event; it cannot change later.
- In auth mode every event carries `role`, `auth_mode`, `credential_id` (and `owner` on create) **inside the signed
  hash**, so provenance is part of the tamper-evident history. In normal mode these are as before.
- An `op_id` is bound to the actor who first used it: someone else replaying it gets a 409, not the original result.

## 6. Freshness and outage policy

The credential status comes from an issuer-signed snapshot (monotonic version, one-letter statuses, no personal
data) that each sidecar polls and each region re-verifies from disk with the pinned issuer key. A region never
accepts an older version than it already holds.

| Snapshot age | Ordinary write | Sign-in, resolve, admin |
|---|---|---|
| ≤ 60 s (`online`) | allow | allow |
| ≤ 600 s (`cached`) | allow | allow |
| ≤ 900 s (`stale`) | allow, only for a known, previously active credential | **deny** (503) |
| > 900 s, or no snapshot | deny (503) | deny (503) |
| credential revoked or unknown | **always deny** (401) | **always deny** |

The same table is implemented twice (TypeScript `status.ts`, Python `true_status.py`) and both are tested against
one shared case file, `collaboration/true/tests/status-policy-cases.json`.

If a region cannot confirm status it answers **503 with `Retry-After`**, never "allowed": the SDK treats 503 as
"try another node". A 401 or 403 is a real answer and is not retried elsewhere.

## 7. API examples

Region endpoints (mounted only when `AUTH_ENABLED=true`). Real values are shortened.

```http
POST /v1/auth/challenge                    → 201
{ "challenge_id": "9e8459a1…", "nonce": "665aa023…", "audience": "syncshield-region:mum",
  "expires_at": 1791818299, "required_claims": ["actor_id","syncshield_member","syncshield_role","credential_id"] }

POST /v1/auth/session   {"challenge_id": "9e8459a1…", "presentation": "<SD-JWT presentation>"}   → 200
{ "token": "eyJhbGciOiJFUzI1NiIs…", "expires_at": 1791818479, "actor_id": "act_27f098ac056ccee6",
  "role": "officer", "credential_id": "cred_b9605bd…", "auth_mode": "online",
  "status_ver": 7, "status_age_s": 0, "region": "mum" }

GET  /v1/auth/me        Authorization: Bearer <token>        → 200   (everything re-derived, nothing echoed)
{ "actor_id": "act_27f098…", "role": "officer", "credential_id": "cred_b9605bd…", "auth_mode": "online",
  "status_ver": 7, "status_age_s": 3, "session_expires_at": 1791818479, "region": "mum" }

POST /v1/reports        Authorization: Bearer <token>        → 201
{ "report": { … }, "event": { "event_id": "mum:1", "actor": "act_27f098…", "owner": "act_27f098…",
  "role": "officer", "auth_mode": "online", "credential_id": "cred_b9605bd…", "sig": "LVHe/maXoZ…", … } }
```

Issuer admin (token in the `x-issuer-admin-token` header, never sent to a browser):

```http
POST /v1/syncshield/members                       {"name":"Alice","role":"officer"}       → 201
POST /v1/syncshield/claim-codes                   {"actorId":"act_…","ttlMinutes":30}     → 201 {"code":"BVLE52QV",…}
POST /v1/syncshield/members/{actorId}/revoke      {"reason":"left team"}                  → 200
{ "revokedCredentials": 1, "statusVersion": 8 }
GET  /v1/syncshield/status-snapshot               → the signed snapshot (public)
```

From the command line: `python scripts/true_stack.py members`, `python scripts/true_stack.py revoke Bob --reason "left team"`.

## 8. Error and status contract

| Situation | HTTP | Body |
|---|---|---|
| No token, bad signature, expired, wrong audience/type, key not pinned | 401 | `{"detail":{"code":"missing_session" \| "bad_signature" \| "session_expired" \| "wrong_audience" \| "unknown_key" …}}` |
| Credential revoked | 401 | `{"detail":{"code":"credential_revoked"}}` |
| Legacy `X-API-Key` while auth is on | 401 | ignored; only `Authorization: Bearer` is read |
| Role may not do this | 403 | `{"detail":{"code":"forbidden"}}` |
| Supervisor edits someone else's cart | 403 | `{"detail":{"code":"not_your_cart"}}` |
| Officer asks for another member's cart | 404 | `unknown report` |
| Status missing / too old for this action | 503 + `Retry-After: 1` | `{"detail":{"code":"no_status" \| "status_too_old" \| "status_stale_for_purpose"}}` |
| Challenge unknown / expired / already used | 404 / 410 / 409 | `unknown_challenge` / `challenge_expired` / `challenge_consumed` |
| Presentation altered (e.g. role edited) | 401 | the partner's verification code, nothing is issued |
| Stale edit / nothing to resolve / op_id reuse | 409 | `stale_edit` (+`live`) / `no_conflict` / `op_id_reuse` |

## 9. Trust assumptions

- **The issuer's public key is pinned** on each region (a file the operator controls). Everything about
  credentials and status is rooted there.
- **Regional session public keys are pinned** the same way. A region trusts a session only if it verifies against
  a pinned key and names that key's region. For several machines the operator must copy these public files across
  (no private key ever leaves its sidecar).
- **Each region trusts its own sidecar over loopback** with a service token. The browser never talks to the sidecar.
- **The admin token** stays with the operator (CLI / server). The browser has no admin credential; the chaos
  agent key is typed in at run time and kept in memory.
- **Two separate signature systems:** TRUE uses ES256 for credentials and sessions; SyncShield events keep their
  own Ed25519 origin signatures. Neither replaces the other.
- **This is not a blockchain and not mutual TLS.** Trust comes from pinned keys and signed, versioned status.

## 10. Test evidence

All numbers below are from real runs on this machine (Windows, Node 24, Python 3.14) against the branch with
`main` merged in. Convergence tests use real partner crypto, a real issuer, one real sidecar and one real FastAPI
process per region.

| Check | Result |
|---|---|
| Core SyncShield suite, **auth off** (`pytest --ignore=tests/convergence`) | **285 passed, 1 expected failure** (the same expected failure as before the merge) |
| TRUE convergence suite (`pytest tests/convergence`) | **50 passed**: 32 unit + 18 with real processes |
| Partner code, Node (`cd collaboration/true && npm test`) | **59 passed**, 1 todo |
| Dashboard unit tests (`cd dashboard && npm test`) | **23 passed** |
| Dashboard build, auth off, in a copy with **no** `collaboration/true/node_modules` | Builds (202 KB, no SD-JWT code in the bundle) |
| Dashboard build, auth on, TRUE installed | Builds (459 KB) |
| Dashboard build, auth on, TRUE **not** installed | Refuses with a clear message (by design) |
| `python scripts/demo_true.py` | "All checks passed", exit code 0 |
| Real browser (headless Edge), auth mode, two regions | Sign-in with consent card; **7 writes acknowledged, 7 present, 0 lost, 0 errors shown** after killing Mumbai; the second member saw no cart of the first; **0 requests carried an API key**, 137 carried a bearer token; nothing in localStorage or sessionStorage |

What the 18 real-process tests cover:

- one login, first authorized write, signed provenance in the event;
- legacy API key refused while auth is on;
- unauthorized writes change nothing;
- challenge replay refused, tampered disclosure refused;
- ownership (404 for another member's cart) and role gates (supervisor, admin, officer);
- idempotency bound to the actor;
- **a supervisor resolving a real conflict across two regions** (officer and admin get 403, the signed decision
  replicates and verifies, a stale retry is 409);
- region crash and restart with the same session; issuer outage; revocation at every region; sidecar loss;
- **three regions (mum, pun, del)**: one login accepted everywhere, session survives a crash, revocation reaches
  the survivors.

Not covered by an automated browser test: driving the **resolve form** in the dashboard in auth mode (the
endpoint path is proven with real processes; the form itself is unchanged from before).

## 11. Run it

```bash
cd collaboration/true && npm install            # once, Node 24+
pip install -r requirements-auth.txt            # once (PyJWT)
python scripts/true_stack.py up --provision     # issuer :4001, sidecars :4101-4103, prints one-time claim codes
AUTH_ENABLED=true python scripts/run_one.py mum # same for pun, del (reads the values from true_stack)
cd dashboard && VITE_AUTH_ENABLED=1 npm run dev # paste the claim codes into "Sign in with TRUE"
python scripts/true_stack.py revoke Bob         # watch Bob's next request get 401 credential_revoked
python scripts/true_stack.py down
```

Self-contained, deterministic demo (own issuer, sidecars and two regions on spare ports, throwaway data):

```bash
python scripts/demo_true.py            # about a minute; exit code = number of failed checks
python scripts/demo_true.py --pause    # step through with Enter when presenting
```

## 11b. Proving one attribute (contract v1.2, lane C)

A restricted product asks the shopper to prove ONE attribute ("over 18" or "member"). The credential carries two more
signed, separately disclosable yes/no claims for this, `age_over_18` and `member`; the issuer copies them from its
members table, like the role.

| Step | What happens |
|---|---|
| Challenge | `POST /v1/auth/challenge {"claim": "age_over_18"}` (body optional). The sidecar stores the one attribute asked for with the challenge; `required_claims` is the four sign-in claims plus that one. |
| Presentation | The wallet reveals exactly `required_claims`. An ordinary sign-in (no `claim`) reveals neither attribute. |
| Verification | The sidecar accepts the four sign-in claims plus, optionally, exactly the stored attribute. Anything else (an attribute nobody asked for, two attributes) is `401 wrong_claims`. Withholding the asked attribute still signs in, without the claim. The value must be a boolean. |
| Session | Two extra token claims: `dsc` (attribute names the shop received) and `clm` (those whose value was `true`). Nothing else from the credential is stored: no name, no date of birth. |
| `GET /v1/auth/me` | gains `claims` (proven) and `disclosed` (received). |
| Use | `region.trust.claims.has_claim(auth, "age_over_18")`; `require_claim(auth, claim)` raises `403 {"error":"claim_required","claim":...}` (rendered with exactly that top-level body). API-key mode never has a claim. |

Because the proof lives in the session token, it is verified locally at every region like the role is: the same session
and the same claim keep working at another region after a crash, with the issuer and the other sidecar down
(`test_claims_real.py`, demo step 6c).

`region/trust/claims.py` also answers the rate limiter and the engine:

- `subject_key(auth)`: the credential id when signed in (the limit follows the person, not the device), else the
  API-key actor.
- `is_revoked(credential_id)`: reads the status snapshot the region already holds (re-verified with the pinned issuer
  key on every call). Only an explicit revoked entry counts; no snapshot or an unknown id is "not revoked", so the
  admission rule never rejects history it has no status for. Window: the sidecar's refresh interval
  (`STATUS_REFRESH_MS`, 5 s by default) plus the fetch; about 1 s in the tests.
- The id events carry as `event["credential_id"]` is the same id the status list is keyed by (asserted in
  `test_is_revoked_turns_true_within_the_window_for_the_id_events_carry`), so lane B's `revoked_credential` rule can
  match it.

Demo members have attributes so every outcome can be shown: Alice and Dev prove both, Bob is not a member, Chitra
is under 18, Esha neither (`python scripts/true_stack.py members`).

For the console's "Revoke this shopper" button, `python scripts/true_stack.py relay` serves `POST /revoke` on
`127.0.0.1:4090`. The browser sends the console admin key (`X-Admin-Key`, the chaos agent's key); only the relay holds
the issuer admin token.

## 12. Changed files

| Area | Files |
|---|---|
| Partner code (adapted) | `collaboration/true/**`: see `PROVENANCE.md` for every file |
| Region config and gate | `region/config.py`, `region/auth.py`, `region/main.py` |
| TRUE checks (all new) | `region/trust/{true_status,true_session,true_adapter,true_hook,ownership}.py` |
| Routes | `region/api/routes_auth.py` (new), `routes_reports.py`, `routes_review.py`, `writes.py`, `region/sync/routes_admin.py` |
| Tooling | `scripts/true_stack.py`, `scripts/demo_true.py`, `scripts/run_one.py`, `requirements-auth.txt`, `.env.example` |
| Dashboard | `dashboard/src/auth/*` (wallet, context, panel, stub), `api.js`, `main.jsx`, `ShopConsole.jsx`, `sdk/syncshieldClient.js`, `vite.config.js` |
| Contract v1.2 (lane C) | `region/trust/claims.py` (new), `region/trust/{true_session,true_adapter,adapter,true_hook}.py`, `region/auth.py`, `region/api/routes_auth.py`, `collaboration/true/{packages/shared,services/issuer-api,services/syncshield-auth}`, `wallet-cli.mjs`, `scripts/{true_stack,demo_true}.py`, `frontend-new/src/identity/*` |
| Tests | `tests/convergence/*`, `collaboration/true/tests/syncshield.test.ts` |

Files owned by other lanes that were touched (need the owner's review): `region/auth.py`, `region/config.py`,
`region/main.py`, `region/api/{writes,routes_reports,routes_review}.py`, `region/sync/routes_admin.py`.
Each change is additive and gated by `AUTH_ENABLED`; with it off these files behave as on `main`.

## 13. Honest limits

- **Revocation is not instant.** It reaches a region at the next status refresh (default 5 s; about 0.3–1.2 s in the
  tests, which poll every 0.7 s). A region cut off from the issuer keeps using its last status for ordinary writes
  for up to 900 s. A member revoked during that window can still make ordinary writes there. Sign-in, resolve and
  admin need status no older than 600 s.
- **The demo's "issuer outage" step is short.** It shows regions carrying on from the last signed status. The long
  outage behaviour (stale, then deny) is covered by the shared policy table with an injected clock, not by waiting
  15 minutes.
- **Session keys and the issuer key are pinned from files.** One machine works out of the box; several machines
  need those public files copied.
- **One issuer.** It is not on the write path, but new logins and revocation propagation need it.
- **Browser tokens live in memory.** A reload means one click to sign in again (the credential and its
  non-extractable key stay in the wallet). A script injected into the page could still *use* the key while the page
  is open; it cannot export it.
- **Unlinkability is the partner's known limit:** the issuer signature and holder key are the same in every
  presentation, so two verifiers comparing notes could tell it is one credential.
- **Own-origin guard.** The carry path refuses an event that claims the receiving region's own origin. The peer
  push/ingest path does not have the same guard. Nothing in the tests exploits this; it belongs to the sync owners.
- **Peer replication is still authenticated by a shared peer token**, not by TRUE.
- **Demo identities are synthetic.** Claim codes work once.
- **An attribute is a fact the issuer asserted, not a check of the person.** "Over 18" is as good as the issuer's
  members table; the shop learns the boolean and nothing behind it. One attribute per sign-in: a purchase needing
  both signs in twice.
- **The proven claim lasts as long as the session** (5 minutes), and an attribute changed or revoked at the issuer
  reaches a region only through a revocation (status list) or the next sign-in.
- **`has_claim` is a session fact, not a per-request check against the issuer.** That is what lets it work with the
  issuer down.
- **Dashboard auth-mode build** needs `npm install` in `collaboration/true`. The normal build does not.

## 14. Adaptation changelog

Partner code (details and reasons in `collaboration/true/PROVENANCE.md`):

- `packages/credentials`: reserved-claims guard; `verifyPresentation` also returns verified `meta`.
- `packages/shared`: new `syncshield.ts` (profile, claims, roles, token and freshness constants).
- `services/issuer-api`: SyncShield members, claim codes, revocation and signed status snapshot as additive routes
  and tables; optional admin token; configuration from environment; key file mode 0600.
- `services/syncshield-auth`: new regional sidecar.
- Wallet port 5173 → 5183 (5173 is the SyncShield dashboard). Workspaces limited to what is vendored.

SyncShield side, after merging `main` (Lane A's write path v2):

- `Auth` keeps Lane A's field order `(actor, role, mode)` and appends `credential_id`.
- Provenance is added to the event before hashing and signing; an op_id is bound to the actor.
- Ownership filters on reports, history and the review queue; the review/resolve endpoints are Lane A's.
- Contract v1.2: `Auth` gains `claims` (proven attributes) after `credential_id`; `AuthDecision` and `Session`
  gain `claims` / `disclosed`, all defaulting to empty, so API-key mode is unchanged.
- `Authorization` added to the allowed CORS headers; Lane A's origin allowlist is unchanged
  (`CORS_ORIGINS` for other dashboard ports).
