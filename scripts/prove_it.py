"""Prove it (owner: Rahil, U2): are all nodes converged right now?

    python scripts/prove_it.py                       mum :8001, pun :8002 (REGIONS=mum,pun,del adds del :8003)
    python scripts/prove_it.py --wait 15             poll until CONVERGED or give up after 15 s
    python scripts/prove_it.py --chaos 3 --wait 20   3 rounds of cut / write on both sides / heal, then check
    python scripts/prove_it.py --url mum=http://10.0.0.5:8001 --url pun=http://10.0.0.6:8002

Reads GET /v1/status from every configured node (1.5 s timeout: no answer = UNKNOWN, never green) and
applies region/engine/convergence.badge. Exit code 0 only when CONVERGED. --chaos needs ENABLE_ADMIN=1.
"""
import argparse
import os
import random
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from region.engine.convergence import badge  # noqa: E402

PORTS = {"mum": 8001, "pun": 8002, "del": 8003}
KEY = {"X-API-Key": os.environ.get("API_KEY", "dev-officer")}


def fetch(urls: dict) -> dict:
    out = {}
    for region, url in urls.items():
        try:
            r = httpx.get(url + "/v1/status", headers=KEY, timeout=1.5)
            out[region] = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            out[region] = None
    return out


def show(statuses: dict) -> tuple:
    result, why = badge(statuses)
    for region, s in statuses.items():
        if s is None:
            print(f"  {region:4} no answer")
        else:
            print(f"  {region:4} {s.get('level', '?'):11} heads={s.get('heads')} "
                  f"events={str(s.get('event_digest'))[:12]} state={str(s.get('state_hash'))[:12]} "
                  f"policy=v{s.get('policy_version')}")
    print(f"{result}: {why}")
    return result, why


def set_link(urls, a, b, mode):
    """Both ends, so neither direction crosses (plan E7)."""
    for x, y in ((a, b), (b, a)):
        r = httpx.post(f"{urls[x]}/admin/links/{y}", headers=KEY, json={"mode": mode}, timeout=3)
        r.raise_for_status()


def chaos(urls: dict, rounds: int, rng: random.Random) -> None:
    """Cut a random pair, write on both sides of the cut (some to the same field), heal."""
    first = next(iter(urls))
    r = httpx.post(urls[first] + "/v1/reports", headers=KEY, timeout=3,
                   json={"title": "prove-it", "category": "other", "severity": 1})
    r.raise_for_status()
    rid = r.json()["report"]["report_id"]
    time.sleep(1.5)  # let the new report replicate before the first cut
    for n in range(rounds):
        a, b = rng.sample(sorted(urls), 2)
        print(f"round {n + 1}: cut {a}-{b}, write on both sides, heal")
        set_link(urls, a, b, "down")
        try:
            for side in (a, b):
                for _ in range(rng.randint(1, 3)):
                    httpx.post(f"{urls[side]}/v1/reports/{rid}/notes", headers=KEY, timeout=3,
                               json={"text": f"prove-it {side} {rng.randint(0, 9999)}"})
                httpx.patch(f"{urls[side]}/v1/reports/{rid}", headers=KEY, timeout=3,
                            json={"field": "severity", "value": rng.randint(1, 5)})
        finally:
            set_link(urls, a, b, "up")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", action="append", default=[], help="region=url (repeatable)")
    ap.add_argument("--wait", type=float, default=0, help="seconds to keep polling until CONVERGED")
    ap.add_argument("--chaos", type=int, default=0, help="rounds of cut / write / heal before checking")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    if args.url:
        urls = dict(u.split("=", 1) for u in args.url)
    else:
        regions = os.environ.get("REGIONS", "mum,pun").split(",")
        urls = {r: f"http://127.0.0.1:{PORTS[r]}" for r in regions}
    if args.chaos:
        seed = args.seed if args.seed is not None else random.randrange(10**6)
        print(f"chaos seed {seed}")
        chaos(urls, args.chaos, random.Random(seed))
    end = time.time() + args.wait
    while True:
        statuses = fetch(urls)
        result, _ = badge(statuses)
        if result == "CONVERGED" or time.time() >= end:
            break
        time.sleep(0.5)
    return 0 if show(statuses)[0] == "CONVERGED" else 1


if __name__ == "__main__":
    sys.exit(main())
