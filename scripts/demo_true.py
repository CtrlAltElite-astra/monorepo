"""Live demo of auth mode: TRUE credentials -> regional sessions -> authorized SyncShield writes.

    python scripts/demo_true.py            # runs straight through (about a minute)
    python scripts/demo_true.py --pause    # waits for Enter before each step, for presenting

Starts its own issuer, one auth sidecar per region and two regions (mum, pun) on spare ports with throwaway
data, so it never touches a running demo. Every step checks the answer it shows; the exit code is the number
of failed checks. Real partner crypto, real processes: the same flows as tests/convergence.

Needs Node 24+, `npm install` in collaboration/true, and `pip install -r requirements.txt -r requirements-auth.txt`.
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from true_stack import TRUE_DIR, Stack  # noqa: E402
from region.engine import signing  # noqa: E402

REGIONS = ["mum", "pun"]
NAMES = {"mum": "Mumbai", "pun": "Pune"}
C = httpx.Client(timeout=10)
failed = []


def say(text=""):
    print(text, flush=True)


def check(label, ok, got=""):
    say(f"   {'✓' if ok else '✗'} {label}" + ("" if ok else f"   (got {got})"))
    if not ok:
        failed.append(label)
    return ok


def until(fn, timeout=12.0, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


class Demo:
    def __init__(self, a):
        self.data = Path(tempfile.mkdtemp(prefix="syncshield-true-demo-"))
        self.ports = {r: a.region_base_port + i for i, r in enumerate(REGIONS)}
        self.url = {r: f"http://127.0.0.1:{p}" for r, p in self.ports.items()}
        self.stack = Stack(self.data / "true", REGIONS, a.issuer_port, a.sidecar_base_port)
        self.procs = {}
        self.pause = a.pause

    # ---- processes ----------------------------------------------------------------------------
    def start_region(self, r):
        env = dict(os.environ)
        env.update(
            REGION=r, PORT=str(self.ports[r]), DB_PATH=str(self.data / f"{r}.db"), ENV_FILE="none",
            PEERS=",".join(f"{o}={self.url[o]}" for o in REGIONS if o != r), PEER_TOKEN="demo-peer",
            SYNC_INTERVAL_MS="300", API_KEYS=f"dev-officer:officer-{r}-1:officer", PYTHONPATH=str(ROOT),
            SIGNING_KEYS_DIR=str(self.data / "keys"), PUBLIC_KEYS_FILE=str(self.data / "public_keys.json"),
            **self.stack.region_env(r),
        )
        log = open(self.data / f"region-{r}.log", "ab")
        self.procs[r] = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "region.main:app", "--host", "127.0.0.1", "--port", str(self.ports[r]),
             "--workers", "1", "--log-level", "warning"], cwd=ROOT, env=env, stdout=log, stderr=log)
        if not until(lambda: self._ok(self.url[r] + "/healthz"), timeout=25):
            raise SystemExit(f"{r} did not start, see {self.data / f'region-{r}.log'}")

    def kill_region(self, r):
        self.procs[r].kill()
        self.procs[r].wait()

    @staticmethod
    def _ok(url):
        try:
            return C.get(url, timeout=1).status_code == 200
        except httpx.HTTPError:
            return False

    def up(self):
        os.environ["STATUS_REFRESH_MS"] = "1000"       # sidecars re-read the issuer's signed status every second
        os.environ["SIGNING_KEYS_DIR"] = str(self.data / "keys")
        os.environ["PUBLIC_KEYS_FILE"] = str(self.data / "public_keys.json")
        signing.ensure_keys(REGIONS)
        self.stack.up()
        for r in REGIONS:
            self.start_region(r)
        for r in REGIONS:                                 # login needs the first signed status snapshot
            until(lambda: C.get(self.url[r] + "/v1/auth/status").json().get("status"), timeout=20)

    def down(self, keep):
        for p in self.procs.values():
            if p.poll() is None:
                p.kill()
                p.wait()
        self.stack.down()
        if keep:
            say(f"\nData and logs kept in {self.data}")
        else:
            shutil.rmtree(self.data, ignore_errors=True)

    # ---- people -------------------------------------------------------------------------------
    def wallet(self, path, *args):
        r = subprocess.run(["node", str(TRUE_DIR / "scripts" / "wallet-cli.mjs"), *args, "--wallet", str(path)],
                           cwd=TRUE_DIR, capture_output=True, text=True, timeout=60)
        out = r.stdout.strip().splitlines()
        return json.loads(out[-1]) if out else {"ok": False, "error": r.stderr[-400:]}

    def member(self, name, role):
        status, m = self.stack.admin("POST", "/v1/syncshield/members", {"name": name, "role": role})
        assert status == 201, m
        code = self.stack.admin("POST", "/v1/syncshield/claim-codes", {"actorId": m["actor_id"], "ttlMinutes": 30})[1]["code"]
        path = self.data / f"wallet-{name.split()[0].lower()}.json"
        got = self.wallet(path, "claim", "--issuer", self.stack.issuer_url, "--code", code)
        assert got.get("ok"), got
        return {"name": name, "role": role, "actor_id": m["actor_id"], "code": code, "wallet": path,
                "credential_id": got["credentialId"]}

    def login(self, who, region, *extra):
        return self.wallet(who["wallet"], "login", "--region", self.url[region], *extra)

    def cart(self, token, region="mum", **kw):
        body = {"title": "cart", "category": "cart", "severity": 1, **kw}
        return C.post(self.url[region] + "/v1/reports", json=body, headers=bearer(token) if token else {})

    def has_event(self, region, event_id):
        origin, seq = event_id.split(":")
        return C.get(self.url[region] + "/v1/status").json()["heads"].get(origin, 0) >= int(seq)

    def step(self, title, story):
        say(f"\n━━ {title}")
        say(f"   {story}")
        if self.pause:
            input("   [Enter] ")


def code(r):
    """'401 credential_revoked'-style summary of a response, for checks and failure messages."""
    try:
        body = r.json()
    except ValueError:
        return str(r.status_code)
    d = (body.get("detail") or body.get("error") or {}) if isinstance(body, dict) else {}
    return f"{r.status_code} {d.get('code', '')}".strip() if isinstance(d, dict) else str(r.status_code)


def run(d):
    d.step("0. Start", "Issuer, an auth sidecar per region, and Mumbai + Pune with AUTH_ENABLED=true.")
    d.up()
    say(f"   issuer {d.stack.issuer_url}  ·  Mumbai {d.url['mum']}  ·  Pune {d.url['pun']}")

    d.step("1. Issue credentials", "An admin registers members; each claims a credential once with a one-time code.")
    alice, bob = d.member("Alice", "officer"), d.member("Bob", "officer")
    sunita, judge = d.member("Sunita (supervisor)", "supervisor"), d.member("Admin Judge", "admin")
    for m in (alice, bob, sunita, judge):
        say(f"   {m['name']:<20} role={m['role']:<10} claim code {m['code']}  ->  credential {m['credential_id'][:12]}…")
    again = d.wallet(d.data / "wallet-thief.json", "claim", "--issuer", d.stack.issuer_url, "--code", alice["code"])
    check("a claim code works only once (second use refused)", not again.get("ok"), again)

    d.step("2. Sign in", "Alice's wallet answers a one-time challenge from Mumbai with a selective-disclosure proof.")
    a = d.login(alice, "mum")
    check(f"Mumbai issued a regional session: role={a.get('role')}, auth_mode={a.get('auth_mode')}",
          a.get("ok") and a.get("role") == "officer", a)
    tok_a = a["token"]

    d.step("3. Only a verified session can write", "No credential, the old API key, then Alice's session.")
    r = d.cart(None)
    check("no credential  -> 401", r.status_code == 401, code(r))
    r = C.post(d.url["mum"] + "/v1/reports", json={"title": "x", "category": "cart", "severity": 1},
               headers={"X-API-Key": "dev-officer"})
    check("legacy X-API-Key -> 401 (ignored in auth mode)", r.status_code == 401, code(r))
    r = d.cart(tok_a, title="Alice cart")
    ok = check("Alice's session -> 201 cart created", r.status_code == 201, code(r))
    if ok:
        ev, cart_id = r.json()["event"], r.json()["report"]["report_id"]
        say(f"   signed event {ev['event_id']}: actor={ev['actor']} owner={ev['owner']} role={ev['role']} "
            f"credential={ev['credential_id'][:12]}… sig={ev['sig'][:16]}…")
        check("actor and owner come from the session, not the request", ev["actor"] == alice["actor_id"] == ev["owner"])

        d.step("4. Who can see and change what", "Roles and ownership come from the credential.")
        b = d.login(bob, "mum")["token"]
        s = d.login(sunita, "mum")["token"]
        j = d.login(judge, "mum")["token"]
        r = C.get(d.url["mum"] + f"/v1/reports/{cart_id}", headers=bearer(b))
        check("Bob (another officer) cannot see Alice's cart -> 404", r.status_code == 404, code(r))
        r = C.patch(d.url["mum"] + f"/v1/reports/{cart_id}", json={"field": "severity", "value": 5}, headers=bearer(b))
        check("Bob cannot edit it -> 404", r.status_code == 404, code(r))
        r = C.get(d.url["mum"] + f"/v1/reports/{cart_id}", headers=bearer(s))
        check("the supervisor can read every cart -> 200", r.status_code == 200, code(r))
        r = C.patch(d.url["mum"] + f"/v1/reports/{cart_id}", json={"field": "severity", "value": 4}, headers=bearer(s))
        check("...but not edit someone's cart -> 403 not_your_cart", code(r) == "403 not_your_cart", code(r))
        r = C.post(d.url["mum"] + f"/v1/reports/{cart_id}/resolve",
                   json={"field": "status", "value": "closed", "candidates": [], "reason": "try"}, headers=bearer(tok_a))
        check("an officer cannot resolve conflicts -> 403", r.status_code == 403, code(r))
        r = C.get(d.url["mum"] + "/admin/links", headers=bearer(j))
        check("the admin can administer links -> 200", r.status_code == 200, code(r))
        r = d.cart(j, title="admin cart")
        check("...but is not a shopper -> 403", r.status_code == 403, code(r))

    d.step("5. Forgery and replay", "Change the disclosed role to admin after signing; replay a used challenge.")
    bad = d.login(alice, "mum", "--tamper", "syncshield_role")
    check("presentation with role changed to admin -> 401", not bad.get("ok") and bad.get("status") == 401, bad)
    kept = d.login(alice, "mum", "--keep", "1")
    r = C.post(d.url["mum"] + "/v1/auth/session",
               json={"challenge_id": kept.get("challenge_id"), "presentation": kept.get("presentation")})
    check("replaying a consumed challenge -> 409 challenge_consumed", code(r) == "409 challenge_consumed", code(r))

    d.step("6. Region failover keeps the session", "Mumbai dies; Alice keeps working at Pune with the SAME token.")
    rid = "R-" + secrets.token_hex(4)
    r = d.cart(tok_a, title="survives", op_id="demo-op-1", report_id=rid)
    check("Alice writes a cart at Mumbai -> 201", r.status_code == 201, code(r))
    ev_id = r.json().get("event_id") if r.status_code == 201 else None
    check("copied to Pune before the crash", bool(ev_id and until(lambda: d.has_event("pun", ev_id), timeout=10)))
    d.kill_region("mum")
    say("   Mumbai killed (kill -9).")
    r = C.post(d.url["pun"] + f"/v1/reports/{rid}/notes", json={"text": "after crash", "op_id": "demo-op-2"},
               headers=bearer(tok_a))
    check("same token works at Pune, no new login -> 201", r.status_code == 201, code(r))
    r = d.cart(tok_a, "pun", title="survives", op_id="demo-op-1", report_id=rid)
    check("retrying the original write at Pune is one operation -> 200 replay",
          r.status_code == 200 and r.headers.get("idempotent-replay") == "true", code(r))
    d.start_region("mum")
    same = until(lambda: C.get(d.url["mum"] + "/v1/status").json()["event_digest"]
                 == C.get(d.url["pun"] + "/v1/status").json()["event_digest"], timeout=15)
    check("Mumbai restarted and both regions hold identical events", bool(same))

    d.step("7. Issuer outage", "The issuer goes down; regions keep working from the last signed status.")
    d.stack.stop("issuer")
    r = d.cart(tok_a, title="issuer down")
    check("existing session still writes -> 201", r.status_code == 201, code(r))
    me = C.get(d.url["mum"] + "/v1/auth/me", headers=bearer(tok_a)).json()
    say(f"   /v1/auth/me: auth_mode={me.get('auth_mode')}, status age {me.get('status_age_s')} s")
    fresh = d.login(bob, "pun")
    check("a new login at Pune still works", bool(fresh.get("ok")), fresh)
    d.stack.start_issuer()

    d.step("8. Revocation", "The admin revokes Bob; every region refuses his EXISTING sessions within seconds.")
    b_mum, b_pun = d.login(bob, "mum")["token"], d.login(bob, "pun")["token"]
    check("Bob can write at both regions before", d.cart(b_mum).status_code == 201 and d.cart(b_pun, "pun").status_code == 201)
    status, body = d.stack.admin("POST", f"/v1/syncshield/members/{bob['actor_id']}/revoke", {"reason": "left the team"})
    check("issuer revoked Bob's credential", status == 200, body)
    t0 = time.time()
    for r_ in REGIONS:
        tok = b_mum if r_ == "mum" else b_pun
        got = until(lambda: code(d.cart(tok, r_)) == "401 credential_revoked", timeout=15)
        check(f"{NAMES[r_]} refuses Bob's session -> 401 credential_revoked ({time.time() - t0:.1f} s after revoke)",
              bool(got), code(d.cart(tok, r_)))
    again = d.login(bob, "mum")
    check("Bob cannot sign in again -> 403 credential_revoked",
          not again.get("ok") and again.get("status") == 403, again)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pause", action="store_true", help="wait for Enter before each step")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway data and logs")
    ap.add_argument("--issuer-port", type=int, default=24001)
    ap.add_argument("--sidecar-base-port", type=int, default=24101)
    ap.add_argument("--region-base-port", type=int, default=28101)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if shutil.which("node") is None or not (TRUE_DIR / "node_modules").exists():
        raise SystemExit("Needs Node 24+ and `npm install` in collaboration/true")
    d = Demo(a)
    try:
        run(d)
    finally:
        d.down(a.keep or bool(failed))
    say(f"\n{'All checks passed.' if not failed else f'{len(failed)} check(s) FAILED: ' + '; '.join(failed)}")
    sys.exit(len(failed))


if __name__ == "__main__":
    main()
