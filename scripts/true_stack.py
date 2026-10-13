"""Start and stop the TRUE side of SyncShield: the issuer and one auth sidecar per region.

    python scripts/true_stack.py up          [--regions mum,pun,del] [--provision]
    python scripts/true_stack.py provision                      # synthetic members + one-time claim codes
    python scripts/true_stack.py down
    python scripts/true_stack.py env mum                         # env block a region needs for AUTH_ENABLED
    python scripts/true_stack.py members                         # demo members (name, role, actor id)
    python scripts/true_stack.py revoke Bob --reason "left team" # revoke a member; regions refuse them in seconds
    python scripts/true_stack.py relay [--port 4090]             # loopback revoke endpoint for the console's demo button

Nothing here is needed unless AUTH_ENABLED=true. Defaults keep the existing ports: issuer 4001, sidecars
4101 / 4102 / 4103 (mum / pun / del). Tests pass their own base ports and data dir to stay isolated from a
running demo. Secrets and private keys live under data/true/ (git-ignored) and are generated once, never
on every run.

Needs Node 24+ (built-in type stripping and node:sqlite) and `npm install` in collaboration/true.
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRUE_DIR = ROOT / "collaboration" / "true"
DEFAULT_REGIONS = ["mum", "pun", "del", "blr"]   # blr has a sidecar from the start, so it can join later

DEMO_MEMBERS = [   # synthetic; never real people. Attributes (contract v1.2): who can prove "over 18" / "member"
    ("Alice", "officer", {"age_over_18": True, "member": True}),
    ("Bob", "officer", {"age_over_18": True, "member": False}),
    ("Chitra", "officer", {"age_over_18": False, "member": True}),
    ("Dev", "officer", {"age_over_18": True, "member": True}),
    ("Esha", "officer", {"age_over_18": False, "member": False}),
    ("Sunita (supervisor)", "supervisor", {}), ("Admin Judge", "admin", {}),
]
RELAY_PORT = 4090


class Stack:
    def __init__(self, data_dir=None, regions=None, issuer_port=4001, sidecar_base_port=4101, node="node"):
        self.data = Path(data_dir or os.environ.get("TRUE_STACK_DIR") or (ROOT / "data" / "true")).resolve()
        self.regions = list(regions or DEFAULT_REGIONS)
        self.issuer_port = int(issuer_port)
        self.sidecar_base = int(sidecar_base_port)
        self.node = node
        self.run = self.data / "run"
        self.issuer_url = f"http://127.0.0.1:{self.issuer_port}"
        self.pinned_issuer = self.data / "pinned" / "issuer-public.json"
        self.session_keys = self.data / "pinned" / "session-keys"
        self.secrets_file = self.data / "secrets.json"
        self._procs = {}
        self._members_lock = threading.Lock()   # the relay serves several browser requests at once

    # ---- derived values every region needs ------------------------------------------------
    def sidecar_port(self, region):
        return self.sidecar_base + self.regions.index(region)

    def sidecar_url(self, region):
        return f"http://127.0.0.1:{self.sidecar_port(region)}"

    def secrets(self):
        self.data.mkdir(parents=True, exist_ok=True)
        if self.secrets_file.exists():
            return json.loads(self.secrets_file.read_text())
        s = {"issuer_admin_token": secrets.token_urlsafe(24), "sidecar_token": secrets.token_urlsafe(24)}
        self.secrets_file.write_text(json.dumps(s, indent=2))
        try:
            os.chmod(self.secrets_file, 0o600)
        except OSError:
            pass
        return s

    def region_env(self, region):
        """Environment for a FastAPI region run with AUTH_ENABLED=true."""
        s = self.secrets()
        return {
            "AUTH_ENABLED": "true",
            "AUTH_SIDECAR_URL": self.sidecar_url(region),
            "AUTH_SIDECAR_TOKEN": s["sidecar_token"],
            "TRUE_ISSUER_PUBLIC_JWK_FILE": str(self.pinned_issuer),
            "AUTH_SESSION_KEY_DIR": str(self.session_keys),
            "AUTH_STATUS_FILE": str(self.data / "status" / f"{region}.jws"),
        }

    # ---- processes --------------------------------------------------------------------------
    def _spawn(self, name, entry, env):
        self.run.mkdir(parents=True, exist_ok=True)
        log = open(self.run / f"{name}.log", "ab")
        p = subprocess.Popen([self.node, entry], cwd=TRUE_DIR, env={**os.environ, **env}, stdout=log, stderr=log)
        (self.run / f"{name}.pid").write_text(str(p.pid))
        self._procs[name] = p
        return p

    @staticmethod
    def _wait(url, timeout=25.0, ok=(200,)):
        end = time.time() + timeout
        while time.time() < end:
            try:
                with urllib.request.urlopen(url, timeout=1) as r:
                    if r.status in ok:
                        return True
            except urllib.error.HTTPError as e:
                if e.code in ok:
                    return True
            except OSError:
                pass
            time.sleep(0.2)
        raise TimeoutError(url)

    def start_issuer(self):
        s = self.secrets()
        (self.data / "issuer").mkdir(parents=True, exist_ok=True)
        self.pinned_issuer.parent.mkdir(parents=True, exist_ok=True)
        self._spawn("issuer", "services/issuer-api/src/server.ts", {
            "SYNCSHIELD": "1", "ISSUER_PORT": str(self.issuer_port), "ISSUER_ADMIN_TOKEN": s["issuer_admin_token"],
            "TRUE_DATA_DIR": str(self.data / "issuer"), "TRUE_REGISTRY_FILE": str(self.data / "issuer" / "registry.json"),
            "ISSUER_PUBLIC_JWK_FILE": str(self.pinned_issuer),
        })
        self._wait(f"{self.issuer_url}/v1/syncshield/profile")

    def start_sidecar(self, region):
        s = self.secrets()
        self.session_keys.mkdir(parents=True, exist_ok=True)
        self._spawn(f"auth-{region}", "services/syncshield-auth/src/server.ts", {
            "AUTH_REGION": region, "AUTH_PORT": str(self.sidecar_port(region)), "AUTH_SIDECAR_TOKEN": s["sidecar_token"],
            "AUTH_DATA_DIR": str(self.data / f"auth-{region}"), "ISSUER_URL": self.issuer_url,
            "ISSUER_PUBLIC_JWK_FILE": str(self.pinned_issuer), "SESSION_KEY_DIR": str(self.session_keys),
            "STATUS_FILE": str(self.data / "status" / f"{region}.jws"),
            "STATUS_REFRESH_MS": os.environ.get("STATUS_REFRESH_MS", "5000"),
        })
        self._wait(self.sidecar_url(region) + "/internal/v1/health", ok=(200, 401))

    def up(self):
        self.start_issuer()
        for r in self.regions:
            self.start_sidecar(r)
        return self

    def start_relay(self, port=RELAY_PORT):
        """The demo relay as a background process (the console's onboarding and revoke button use it)."""
        self.run.mkdir(parents=True, exist_ok=True)
        log = open(self.run / "relay.log", "ab")
        cmd = [sys.executable, str(Path(__file__).resolve()), "relay", "--port", str(port), "--data-dir", str(self.data),
               "--regions", ",".join(self.regions), "--issuer-port", str(self.issuer_port),
               "--sidecar-base-port", str(self.sidecar_base)]
        kw = {"creationflags": 0x00000008} if os.name == "nt" else {"start_new_session": True}   # detached from this shell
        p = subprocess.Popen(cmd, stdout=log, stderr=log, **kw)
        (self.run / "relay.pid").write_text(str(p.pid))
        self._procs["relay"] = p
        self._wait_port(port)
        return p

    @staticmethod
    def _wait_port(port, timeout=10.0):
        import socket
        end = time.time() + timeout
        while time.time() < end:
            with socket.socket() as sk:
                sk.settimeout(0.3)
                if sk.connect_ex(("127.0.0.1", port)) == 0:
                    return True
            time.sleep(0.2)
        raise TimeoutError(f"port {port}")

    def stop(self, name):
        pidfile = self.run / f"{name}.pid"
        p = self._procs.get(name)
        if p is not None:
            p.kill()
            p.wait()
        elif pidfile.exists():
            try:
                pid = int(pidfile.read_text())
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                else:
                    os.kill(pid, 9)
            except (OSError, ValueError):
                pass
        pidfile.unlink(missing_ok=True)

    def down(self):
        for name in ["relay", "issuer"] + [f"auth-{r}" for r in self.regions]:
            self.stop(name)

    # ---- admin helpers (issuer admin API; the token never goes to a browser) -------------------
    def admin(self, method, path, body=None):
        req = urllib.request.Request(
            self.issuer_url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json", "x-issuer-admin-token": self.secrets()["issuer_admin_token"]},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def ensure_members(self):
        """Idempotent: the demo members are created once (data/true/members.json). No claim codes are made here."""
        with self._members_lock:
            mf = self.data / "members.json"
            members = json.loads(mf.read_text()) if mf.exists() else {}
            changed = False
            for name, role, attributes in DEMO_MEMBERS:
                if name not in members:
                    status, body = self.admin("POST", "/v1/syncshield/members",
                                              {"name": name, "role": role, **({"attributes": attributes} if attributes else {})})
                    assert status == 201, body
                    members[name] = body
                    changed = True
            if changed:   # written once, atomically: a concurrent reader never sees half a file
                tmp = mf.with_suffix(".tmp")
                tmp.write_text(json.dumps(members, indent=2))
                os.replace(tmp, mf)
            return members

    def claim_code_for(self, who, ttl_minutes=60):
        """A fresh one-time claim code for one demo member (name, first name or actor id), or (status, error body)."""
        self.ensure_members()
        actor = self.find_member(who)
        if actor is None:
            return 404, {"error": {"code": "unknown_member", "message": f"no single member matches {who!r}"}}
        status, body = self.admin("POST", "/v1/syncshield/claim-codes", {"actorId": actor, "ttlMinutes": ttl_minutes})
        return (201, {"code": body["code"]}) if status == 201 else (status, body)

    def provision_demo(self, ttl_minutes=720):
        """Idempotent: members are created once (data/true/members.json); fresh one-time codes every call."""
        members = self.ensure_members()
        codes = {}
        for name, _, _ in DEMO_MEMBERS:
            status, body = self.admin("POST", "/v1/syncshield/claim-codes",
                                      {"actorId": members[name]["actor_id"], "ttlMinutes": ttl_minutes})
            if status == 201:
                codes[name] = body["code"]
        return members, codes


    def members(self):
        with self._members_lock:
            mf = self.data / "members.json"
            return json.loads(mf.read_text()) if mf.exists() else {}

    def find_member(self, who):
        """A demo member by name, first name or actor id; an unlisted `act_...` id is passed through as is."""
        w = str(who).strip().lower()
        hits = [m for n, m in self.members().items() if w in (n.lower(), n.split(" ")[0].lower(), m["actor_id"].lower())]
        if len(hits) == 1:
            return hits[0]["actor_id"]
        return who if not hits and str(who).startswith("act_") else None

    def revoke(self, who, reason):
        actor = self.find_member(who)
        if actor is None:
            return 404, {"error": {"code": "unknown_member", "message": f"no single member matches {who!r}"}}
        return self.admin("POST", f"/v1/syncshield/members/{actor}/revoke", {"reason": reason})


def serve_relay(stack, port=RELAY_PORT):
    """Demo-only revoke endpoint on 127.0.0.1, so the console's "Revoke this shopper" button never holds the issuer
    admin token. Same guard as the chaos agent: header X-Admin-Key = ADMIN_KEY (default "dev-admin"), and CORS only
    for the console origins in CORS_ORIGINS (default the frontend-new dev server).

        POST /revoke      {"member": "<name | actor id>", "reason": "..."} -> the issuer's answer
        POST /claim-code  {"member": "<name | actor id>"} -> {"code": "..."}: a fresh one-time claim code, so the console
                          can onboard a demo member by itself (nobody pastes codes). The code still goes through the real
                          flow: holder proof first, then the issuer signs the credential.
    """
    import hmac
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    admin_key = os.environ.get("ADMIN_KEY", "dev-admin")
    origins = {o.strip() for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5175,http://127.0.0.1:5175").split(",") if o.strip()}

    class Relay(BaseHTTPRequestHandler):
        def _send(self, code, body=None):
            self.send_response(code)
            origin = self.headers.get("Origin")
            if origin in origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Key")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body is not None:
                self.wfile.write(json.dumps(body).encode())

        def do_OPTIONS(self):  # noqa: N802 - http.server naming
            self._send(204)

        def do_POST(self):  # noqa: N802
            if self.path not in ("/revoke", "/claim-code"):
                return self._send(404, {"error": {"code": "not_found", "message": "POST /revoke or /claim-code only"}})
            if not hmac.compare_digest(self.headers.get("X-Admin-Key", ""), admin_key):
                return self._send(401, {"error": {"code": "unauthorized", "message": "missing or wrong X-Admin-Key"}})
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}")
                member, reason = str(body["member"]), str(body.get("reason") or "revoked in the demo")[:200]
            except (ValueError, KeyError, TypeError):
                return self._send(400, {"error": {"code": "bad_request", "message": "member is required"}})
            try:
                if self.path == "/claim-code":
                    status, out = stack.claim_code_for(member)
                else:
                    status, out = stack.revoke(member, reason)
            except Exception as exc:  # noqa: BLE001 - say what failed instead of dropping the connection
                status, out = 500, {"error": {"code": "relay_error", "message": f"{type(exc).__name__}: {exc}"[:200]}}
            self._send(status, out)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Relay)
    print(f"demo relay (revoke, claim-code) on http://127.0.0.1:{port}  (X-Admin-Key required; Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["up", "down", "provision", "env", "reset", "members", "revoke", "relay"])
    ap.add_argument("region", nargs="?", help="region for `env`; member name or actor id for `revoke`")
    ap.add_argument("--reason", default="revoked by admin")
    ap.add_argument("--regions", default=",".join(DEFAULT_REGIONS))
    ap.add_argument("--provision", action="store_true")
    ap.add_argument("--data-dir")
    ap.add_argument("--issuer-port", type=int, default=4001)
    ap.add_argument("--sidecar-base-port", type=int, default=4101)
    ap.add_argument("--port", type=int, default=RELAY_PORT, help="port for `relay`")
    a = ap.parse_args(argv)
    st = Stack(a.data_dir, a.regions.split(","), a.issuer_port, a.sidecar_base_port)
    if a.cmd == "up":
        st.up()
        st.ensure_members()
        st.start_relay(a.port)
        print(f"issuer {st.issuer_url}  sidecars " + ", ".join(f"{r}:{st.sidecar_port(r)}" for r in st.regions)
              + f"  demo relay :{a.port}")
        print("\nNothing to paste: open the console and press Sign in. Each member's credential is issued the first time.")
        if a.provision:   # only for the command line (wallet-cli, scripts): the console does not need these
            _, codes = st.provision_demo()
            print("\nClaim codes (one use each):")
            for n, c in codes.items():
                print(f"{n.split(' ')[0]}: {c}")
        print("\nRunning in the background. Stop with: python scripts/true_stack.py down")
    elif a.cmd == "provision":
        _, codes = st.provision_demo()
        for n, c in codes.items():
            print(f"{n.split(' ')[0]}: {c}")
    elif a.cmd == "env":
        for k, v in st.region_env(a.region or st.regions[0]).items():
            print(f"{k}={v}")
    elif a.cmd == "members":
        for name, m in st.members().items():
            attrs = ", ".join(k for k, v in (m.get("attributes") or {}).items() if v) or "-"
            print(f"{name:22} {m.get('role', '?'):11} {m['actor_id']}  proves: {attrs}")
    elif a.cmd == "relay":
        serve_relay(st, a.port)
    elif a.cmd == "revoke":
        if not a.region:
            ap.error("revoke needs a member name or actor id, e.g.  revoke Bob")
        if st.find_member(a.region) is None:
            sys.exit(f"no single member matches {a.region!r}; run `members` to list them")
        status, body = st.revoke(a.region, a.reason)
        if status != 200:
            sys.exit(f"revoke failed: HTTP {status} {body}")
        print(f"revoked {a.region}: {body['revokedCredentials']} credential(s), status version {body['statusVersion']}")
        print("Regions refuse their existing sessions after the next status refresh (about 5 s by default).")
    elif a.cmd == "down":
        st.down()
        print("stopped")
    elif a.cmd == "reset":
        st.down()
        shutil.rmtree(st.data, ignore_errors=True)
        print("removed", st.data)


if __name__ == "__main__":
    main()
