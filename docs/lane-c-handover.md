# Lane C handover: SyncShield × TRUE sign-in

Owner: Miti Shah (Lane C) · Last updated: 2026-10-12

Read sections 1 to 3 before touching anything. Section 3 says what is done and what is next, in order.

---

## 1. Where things stand

| What | Where | State |
|---|---|---|
| Lane C v1.1 work: shop failover console, failover SDK, chaos agent, Prove-it, Tamper, Delhi node, architecture docs | `main` on GitHub | Merged. Item-by-item status: `docs/lane-c-progress.md` |
| TRUE integration, backend: partner credential code plus the checks in each region | GitHub branch `lane-c` (`07cfbf0`, `67e522b`) | Pushed and tested. **Off by default** |
| Everything for the TRUE sign-in: `main` merged in, browser wallet, tooling, tests, `docs/convergence-true.md`, this handover | local branch `lane/collaboration-true-auth` (worktree below) | Done and tested 2026-10-12. **Local only, not pushed**; waiting for Miti's go-ahead |
| Browser wallet, original branch | local branch `wip/lane-c-browser-wallet` | Already merged into the lane branch; can be deleted |
| Working folder for this branch | `C:\Users\Administrator\Desktop\syncshield-collaboration-true-auth` (git worktree) | Clean |
| Main folder | `C:\Users\Administrator\Desktop\LifeBoat` (branch `main`) | In sync with GitHub (reset 2026-10-12; old local main kept as `backup/main-before-reset`) |

**`main` had moved since `lane-c` branched.** Lane A's main build is merged there: write path v2, `GET /v1/review`, `POST /v1/reports/{id}/resolve`, roles and `auth_mode` in the event envelope, and a trust adapter interface. Simrit's lane is merged too. The GitHub branch `lane-c` is still the OLD version, 14 commits behind `main`; the up-to-date merge exists only in the local worktree. **Push and PR only when Miti says so.**

**What the TRUE integration does.** Members sign in with a TRUE credential, the privacy-preserving digital ID from the partner team (SD-JWT VC). The wallet shows only four facts: an opaque member id, "is a member", a role, and a credential id. It does not show name, date of birth, address, photo or student number. The issuer signs the credential, and it is bound to a key that never leaves the browser.

A region checks the proof with the partner's own verification code and issues a 5-minute session token. Every region accepts that token and checks it locally against pinned keys and a signed revocation list. Nothing calls the issuer on the write path, so killing a region does not log anyone out. Roles are enforced on the server:

- An officer only sees and edits their own carts.
- Only a supervisor resolves conflicts.
- An admin only administers.

---

## 2. Rules for every commit and push

- **Identity.** Author and committer must be `Miti Shah <mitishah2109@gmail.com>`; this is the email on the GitHub account. Set it per command and leave global config alone:
  ```bash
  export GIT_AUTHOR_NAME="Miti Shah" GIT_AUTHOR_EMAIL="mitishah2109@gmail.com" \
         GIT_COMMITTER_NAME="Miti Shah" GIT_COMMITTER_EMAIL="mitishah2109@gmail.com"
  git commit -m "..."
  git log -1 --format=fuller     # both Author and Commit must say Miti Shah <mitishah2109@gmail.com>
  ```
- **Only Miti's name.** No other author credits of any kind in commits or PR descriptions.
- **Pushing.** Push only this branch, to `lane-c`: `git push origin lane/collaboration-true-auth:lane-c`. Never force-push. Never push straight to `main`.
- **Never commit:**
  - `TRUE-main/`, the partner team's full repo copy in the LifeBoat folder. It holds their private planning notes.
  - `data/true/`, which holds generated keys and the issuer database.
  - `keys/`
  - any `.env` file
- **Auth stays off by default.** With no auth variables set, SyncShield must behave exactly as it does on `main`, and the core test suite must stay green.
- **Tests stay on their own ports** so a running demo is never touched: regions 18101/18102, issuer 14001, sidecars 14101 and up, Vite 15173. The demo uses issuer 4001, sidecars 4101/4102/4103, dashboard 5173 and chaos agent 9000.

---

## 3. Start here: what is done and what is next

### Done (all local commits on `lane/collaboration-true-auth`; nothing below is pushed yet)

| Step | What | Where |
|---|---|---|
| Merge `main` into the lane branch | Lane A's write path v2 is the base, TRUE parts sit on top (decisions are in the list under "Decisions" below) | commit `0833ce3` |
| Browser sign-in screen | Merged from the wip branch; builds with **nothing** installed under `collaboration/true` (stub), real packages only when `VITE_AUTH_ENABLED=1` | `dashboard/` |
| Partner README fixed, provenance written | Merge markers removed; every changed/new/omitted file listed | `collaboration/true/{README,PROVENANCE}.md` |
| Tooling | `true_stack.py members` / `revoke`, `AUTH_ENABLED=true python scripts/run_one.py <region>`, root `.env.example`, `scripts/demo_true.py` | `scripts/`, `.env.example` |
| Three-region auth run (Delhi) | One login accepted at mum/pun/del, session survives a crash, revocation reaches survivors | `tests/convergence/test_three_regions.py` |
| Supervisor resolve, real processes | Concurrent edits under a partition, officer and admin refused, supervisor resolves, decision replicates and verifies | `tests/convergence/test_real_flow.py` |
| `docs/convergence-true.md` | The required write-up (attribution, reused functions, API examples, contracts, trust, lifecycle, freshness, diagram, files, evidence, limits, changelog) | `docs/` |

**Decisions made while merging** (so nobody redoes them differently):

- `region/api/writes.py` is Lane A's file with TRUE re-added: `provenance` goes into the event before hashing and signing, a CREATE fixes `owner`, an op_id is bound to the actor. `local_write_ex` keeps its old shape.
- `Auth` is `(actor, role, mode, credential_id)`. Lane A's order is kept and `credential_id` is appended with default `None`.
- Routes use Lane A's `failure`/`respond`/receipts, plus ownership filters and provenance.
- `Authorization` is allowed in CORS; Lane A's origin allowlist is unchanged (use `CORS_ORIGINS` for other dashboard ports).
- Our resolve test expects Lane A's contract: `stale_edit` first, then `no_conflict`.

### Next, in order

1. **Wait for Miti's go-ahead, then push** only this branch: `git push origin lane/collaboration-true-auth:lane-c`. Not pushed yet; GitHub `lane-c` is still the older version.
2. **Show Akshit the diff** of the files he owns (`region/auth.py`, `config.py`, `main.py`, `region/api/{writes,routes_reports,routes_review}.py`) and agree the trust-adapter seam: `region/trust/true_adapter.py` implements his `adapter.py` interface; his open item "partner HTTP adapter with cache and fallback (T13)" is separate.
3. **Tell Simrit** about the own-origin observation: carry rejects an event claiming the receiving region's own origin; the peer push/ingest path has no such guard (nothing exploits it in tests).
4. **Fetch `main` again before any PR** and re-run section 5. Only merge to `main` when Miti says so.
5. **Browser-level resolve check (C8).** The review queue and resolve form exist and the backend path is proven with real processes, but the resolve *form* has not been driven in a browser in auth mode. Do it once (sign in as Sunita, create two concurrent edits, resolve).
6. **C9 / C10 / C12** from `docs/lane-c-progress.md`: RECOVERING state needs Simrit's levels; metrics panel needs a `/v1/metrics`; a person must record the backup screen recording.

## 4. How to run

### Normal mode (auth off): unchanged
Use the README on `main`. Nothing from TRUE is needed.

### Auth mode (demo)
```bash
cd collaboration/true && npm install            # once; needs Node 24+
pip install -r requirements-auth.txt            # once (PyJWT)
python scripts/true_stack.py up --provision     # issuer :4001, sidecars :4101/:4102/:4103; prints one-time claim codes
python scripts/true_stack.py env mum            # env block for Mumbai (same for pun, del); start each region with it
AUTH_ENABLED=true python scripts/run_one.py mum    # one terminal per region; fills in the values for you
cd dashboard && VITE_AUTH_ENABLED=1 npm run dev     # needs npm install in collaboration/true
python scripts/true_stack.py revoke Bob             # demo: Bob's next request is 401 credential_revoked
python scripts/true_stack.py down                   # stop the issuer and sidecars
```
Paste the printed claim codes into "Sign in with TRUE", click **Claim credentials**, then **Sign in**.

---

## 5. Checks (all must pass)

```bash
pytest                                     # core suite, auth off: must stay green
pytest tests/convergence                   # 32 unit + 18 real-process (skips with a reason if Node 24 or npm install is missing)
cd collaboration/true && npm test          # vitest: partner crypto + SyncShield profile
cd dashboard && npm test && npm run build  # the build also passes on a clone with nothing installed under collaboration/true
```

Browser check (auth mode, headless Edge): claim, consent, sign in, shop, a second member cannot see the cart, kill Mumbai, keep shopping.

**Results (2026-10-12, branch `lane/collaboration-true-auth`, `main` merged in):**
- Core suite, auth off: `pytest --ignore=tests/convergence` -> **285 passed, 1 expected failure**.
- TRUE: `pytest tests/convergence` -> **50 passed** (32 unit + 18 real-process, including the three-region and supervisor-resolve tests).
- Node: `cd collaboration/true && npm test` -> **59 passed, 1 todo**.
- Dashboard: `npm test` -> **23 passed**. Build: auth off passes in a copy with no `collaboration/true/node_modules`; auth on passes with TRUE installed; auth on without it refuses with a clear message.
- `python scripts/demo_true.py` -> "All checks passed", exit 0.
- Real browser (headless Edge), merged code: **7 acknowledged, 7 present, 0 lost, 0 errors** after killing Mumbai; second member saw no cart; 0 requests with an API key, 137 with a bearer token; nothing in localStorage/sessionStorage.
- The browser run needs `CORS_ORIGINS=<dashboard origin>` when the dashboard is not on :5173 (Lane A's allowlist).
- Screenshots: `docs/screenshots/auth_signed_in.png`, `auth_after_kill.png`.

---

## 6. Key facts (for the doc and for judges' questions)

**Session.** ES256 JWT with `typ` `syncshield-session+jwt`, `aud` `syncshield`, valid for 300 s. The sidecar returns `token` and `expires_at` (epoch seconds). The browser renews 30 s before expiry using a fresh challenge.

**Challenge.** One-shot, with an atomic pending → verifying claim. A failed attempt also uses the challenge up. The server chooses the audience `syncshield-region:<region>`; the client never does.

**Revocation.** The issuer publishes a signed status snapshot at `GET /v1/syncshield/status-snapshot`. Its version only goes up (rollback-protected), statuses are one letter, and it carries no personal data. Admins revoke with `POST /v1/syncshield/members/{actor_id}/revoke`.

**Freshness policy.** Shared case table: `tests/status-policy-cases.json`. Mirrored in `status.ts` and `region/trust/true_status.py`.

| Snapshot age | Ordinary write | Sign-in, resolve, admin |
|---|---|---|
| 60 s or less (online) | allow | allow |
| 600 s or less (cached) | allow | allow |
| 900 s or less (stale) | allow | **deny** |
| over 900 s, or no snapshot | deny | deny |
| member revoked or unknown | **always deny** | **always deny** |

**Error contract.**

| Case | Response |
|---|---|
| Missing, bad or expired token | 401 |
| Revoked credential | 401, code `credential_revoked` |
| Wrong role | 403 |
| Another member's cart | 404, so the cart's existence does not leak |
| Status missing or too old | 503 with `Retry-After` |

**Roles.**

| Role | Allowed |
|---|---|
| officer | Their own carts only |
| supervisor | Reads all carts and resolves conflicts; edits only their own |
| admin | Admin endpoints only |

The cart owner is fixed by the signed CREATE event.

**Two kinds of signature.**

| Signature | Covers | Issued by |
|---|---|---|
| Ed25519 | Events | SyncShield regions |
| ES256 | Credentials and sessions | TRUE |

This is not a blockchain and not mTLS; do not claim either.

---

## 7. Demo script (auth mode, about 3 minutes)

1. Signed out: no carts are shown and no API key appears in any request.
2. Paste the claim codes. Seven credentials are issued (5 officers, 1 supervisor, 1 admin), each with a real holder proof.
3. Sign in as Alice. The consent card lists exactly what is shared and what is **not** shared (name, date of birth, address, photo, student number).
4. Click **Sign everyone in**, then shop as Alice. Writes are acknowledged.
5. Switch to Bob. Bob cannot see Alice's cart.
6. Crash Mumbai and keep shopping with the **same** sessions. Pune accepts them, checked locally, with no call to the issuer. No acknowledged write is lost.
7. Revoke a member. After the next status refresh, every region refuses them with 401 `credential_revoked`, even though their token has not expired yet.
8. Stop the issuer. Regions keep working from the cached snapshot within the time limits above, then refuse with a 503 instead of letting everyone through.

## 8. The 30-second explanation for judges

> "Members sign in with a TRUE credential, a privacy-preserving digital ID built by our partner team. The wallet proves 'I am an eligible SyncShield officer' without revealing name or date of birth. Any region checks it and gives a short session that **every** region accepts, checked locally with pinned keys. So killing Mumbai does not log anyone out, and nothing calls the issuer on the write path. Revocation reaches every region through a signed status list. If the issuer goes down, regions use the cached list with strict time limits instead of letting everyone in. Roles are enforced on the server: officers only touch their own carts, and only supervisors resolve conflicts."

---

## 9. Known issues and honest limits

- **Own-origin guard.** Carry rejects an event that claims the receiving region's own origin. The peer push/ingest path does not. Nothing exploits this in the tests. Tell Simrit (sync) and Akshit (ingest).
- **Page reload.** Browser tokens live in memory only, so reloading the page means signing in again. It is one click, because the credential and its key stay in the wallet.
- **Revocation speed.** Revocation is as fast as the status refresh interval, not instant.
- **Demo data.** Demo members are synthetic. Claim codes work once.
- **Auth-mode dashboard build** needs `npm install` in `collaboration/true`; the normal build does not.

**Adaptation changelog (partner code we changed):**
- `packages/credentials`: a reserved-claims guard, and verified metadata (`meta`) on a successful verification.
- `packages/shared/src/syncshield.ts`: the SyncShield profile (claims, roles, audience format).
- `services/issuer-api`:
  - SyncShield members and claim codes
  - revocation
  - the signed status snapshot
- `services/syncshield-auth` (new): a per-region sidecar. It runs the challenge, verifies the presentation with the partner's `verifyPresentation`, and signs the session.
- New files: `tests/syncshield.test.ts`, `tests/status-policy-cases.json`, `scripts/wallet-cli.mjs`.
- Not copied: the partner's internal planning notes.

## 10. Files added or changed by the TRUE work

See the table in `docs/convergence-true.md` (section 12) and `collaboration/true/PROVENANCE.md` for the partner files.

Source documents, which are not in the repo; keep them at hand:
- the TRUE integration brief (the required sections of `docs/convergence-true.md` come from it)
- the SyncShield Main Build Plan v1.1 (the Lane C items in "Next" come from it)
