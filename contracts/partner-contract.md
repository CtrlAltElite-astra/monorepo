# Partner contract (PROPOSED, for the Convergence round) — owner: Akshit

Status: **proposal, not yet agreed with the partner team.** Everything below is what SyncShield
will call and supply; change it only after the partner confirms. Our side is built behind one
interface, `region/trust/adapter.py::TrustAdapter`, so whichever Cybersecurity and Blockchain
problem statement we are paired with, the work is one adapter class.

Rule that never changes: **a partner outage must never block a local write.** It lowers the
trust `mode` label (`online` -> `cached` -> `stale`); it never turns a write into an error.
Unknown issuer or a bad signature fails closed (the action is refused, nothing is stored).

## 1. What we consume (base URL `PARTNER_URL`, header `X-Api-Key: PARTNER_KEY`)

```
GET  /trust/keys              -> {"keys": [{"kid", "alg": "Ed25519", "public_key_b64", "issuer"}]}     cache 10 min
POST /credentials/verify      {"presentation"}
                              -> {"valid", "subject", "role", "region_scope", "credential_id", "exp"}
GET  /credentials/status-list -> {"version", "issued_at", "revoked": [credential_id, ...]}            cache, TTL 10 min
POST /anchor                  {"region", "seq_from", "seq_to", "merkle_root", "sig"}
                              -> {"anchor_id", "tx_ref"}                                              optional
```

Field notes: `exp` is Unix time in seconds; `region_scope` is a region id (for example `mum`) or `all`;
the status list `version` is an integer or a string and is copied unchanged into `auth.status_ver`.

Timeouts: connect 0.5 s, read 1.5 s, at most one retry.
Partner `401`/`403` = the credential is invalid: we answer `403` and store no event.
A normal `200` response with `"valid": false` means exactly the same thing: refuse with `403`, store nothing.
Timeout or `5xx` = partner unavailable: we verify locally against cached issuer keys and the cached
status list, and label the decision `cached` (or `stale` when the status list is older than 10 min).
Unavailable is never treated as valid or as invalid by itself.

## 2. How a credential becomes a role

`valid: true` plus the returned `role` (`officer` or `supervisor`) and `region_scope` (this region's id,
or `all`) map onto the role table in `contracts.md` 3.1. A credential for another region, an
expired one (`exp`), or one whose `credential_id` is in the revoked list is refused. An action that
resolves a conflict needs a status list fresher than 10 minutes; if only a `stale` list is available
the resolve is refused with 403 `status_list_stale`, and ordinary writes continue.

Each accepted event records how it was authorised in a signed `auth` block (Phase 2):

```json
"auth": {"method": "api_key | partner_vc", "credential_id": "...", "mode": "online | cached | stale", "status_ver": "..."}
```

## 3. What we supply

- A stable REST API with OpenAPI at `/docs` (each region, ports 8001 / 8002 / 8003).
- The signed, replicated audit trail: `GET /v1/reports/{id}/history` (event, actor, `supersedes`, `hash`, `sig`, `auth`).
- Optionally, a Merkle root per event range, so the partner can anchor batches. **Only**
  `{region, seq_from, seq_to, merkle_root, signer, time}` (about 100 bytes) is ever sent: no report
  content, no names, no locations. All data in SyncShield is synthetic.
- A partition-tolerant place to record the partner's verification results (they become events).

## 4. How the three partner problem statements map onto the adapter

| partner PS | adapter method(s) | what changes in the workflow | partner unreachable |
|---|---|---|---|
| Privacy-Preserving Verifiable Identity Network | `verify_actor` | only credential holders create reports; only supervisor credentials resolve; every event records its credential | verify locally with cached keys, label `cached`/`stale`; unknown issuer rejected |
| Physical-to-Digital Anti-Counterfeit Trust Fabric | `check_evidence` (+ optional `anchor`) | a report carries a lot/scan id; a counterfeit verdict opens a review item | report saved with evidence `unverified`; verification retried from a local queue |
| Software Dependency Trust Graph and Compromise Detection | `peer_trust` | a new region (Delhi) joins only after its dependency manifest has a non-high risk verdict | join stays `trust unknown`; an admin override is allowed and logged |

Titles are as listed in the planning notes; confirm them against the organisers' document at pairing time.

## 5. Proof that the integration is load-bearing (tests, owned by Akshit)

- Forged or revoked credential -> `403` and **no event stored** (T13).
- Partner down -> writes continue, decisions labelled `cached`.
- Edited event or wrong key -> quarantined on every region (T10); an edited event also fails anchor verification.

## 6. Questions to settle with the partner

Endpoint paths and shapes above; how we authenticate to them; failure codes and latency; where their
service runs and whether our laptops can reach it from the venue network; what their own demo needs
from us; whether anchoring is wanted at all.

Until the partner is reachable we ship the adapter, a mock server (`scripts/mock_partner.py`, owner
Simrit) and this contract, and describe the state honestly as "integration ready, partner service
unavailable". A mock result is never presented as a real integration.
