"""Start and stop the TRUE side of SyncShield: the issuer and one auth sidecar per region.

    python scripts/true_stack.py up          [--regions mum,pun,del] [--provision]
    python scripts/true_stack.py provision                      # synthetic members + one-time claim codes
    python scripts/true_stack.py down
    python scripts/true_stack.py env mum                         # env block a region needs for AUTH_ENABLED

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
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRUE_DIR = ROOT / "collaboration" / "true"
DEFAULT_REGIONS = ["mum", "pun", "del"]

DEMO_MEMBERS = [   # synthetic; never real people
    ("Alice", "officer"), ("Bob", "officer"), ("Chitra", "officer"), ("Dev", "officer"), ("Esha", "officer"),
    ("Sunita (supervisor)", "supervisor"), ("Admin Judge", "admin"),
]


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
        for name in ["issuer"] + [f"auth-{r}" for r in self.regions]:
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

    def provision_demo(self, ttl_minutes=720):
        """Idempotent: members are created once (data/true/members.json); fresh one-time codes every call."""
        mf = self.data / "members.json"
        members = json.loads(mf.read_text()) if mf.exists() else {}
        for name, role in DEMO_MEMBERS:
            if name not in members:
                status, body = self.admin("POST", "/v1/syncshield/members", {"name": name, "role": role})
                assert status == 201, body
                members[name] = body
        mf.write_text(json.dumps(members, indent=2))
        codes = {}
        for name, _ in DEMO_MEMBERS:
            status, body = self.admin("POST", "/v1/syncshield/claim-codes",
                                      {"actorId": members[name]["actor_id"], "ttlMinutes": ttl_minutes})
            if status == 201:
                codes[name] = body["code"]
        return members, codes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["up", "down", "provision", "env", "reset"])
    ap.add_argument("region", nargs="?")
    ap.add_argument("--regions", default=",".join(DEFAULT_REGIONS))
    ap.add_argument("--provision", action="store_true")
    ap.add_argument("--data-dir")
    ap.add_argument("--issuer-port", type=int, default=4001)
    ap.add_argument("--sidecar-base-port", type=int, default=4101)
    a = ap.parse_args(argv)
    st = Stack(a.data_dir, a.regions.split(","), a.issuer_port, a.sidecar_base_port)
    if a.cmd == "up":
        st.up()
        print(f"issuer {st.issuer_url}  sidecars " + ", ".join(f"{r}:{st.sidecar_port(r)}" for r in st.regions))
        if a.provision:
            _, codes = st.provision_demo()
            print("\nClaim codes (paste this block into the wallet's onboarding box; each works once):")
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
    elif a.cmd == "down":
        st.down()
        print("stopped")
    elif a.cmd == "reset":
        st.down()
        shutil.rmtree(st.data, ignore_errors=True)
        print("removed", st.data)


if __name__ == "__main__":
    main()
