"""Scaling benchmark (owner: Rahil). Scaling plan, lane B task 1. HTTP only: it needs no region code.

    python scripts/bench_scale.py --vertical --horizontal --compare
    python scripts/bench_scale.py --vertical --writes 600 --concurrency 16
    python scripts/bench_scale.py --horizontal --url mum=http://127.0.0.1:8001 --url pun=http://127.0.0.1:8002

  --vertical    one region; for each power (low, medium, high) set through POST /admin/config, send N writes
  --horizontal  the same N writes split across 1, 2 and 3 regions, each at low power
  --compare     one region at high power against three regions at low power

A write is one add-to-cart (POST /v1/reports/{id}/notes) with a fresh op_id, sent by `concurrency` workers at
once. Carts are made before the clock starts, ten items per cart, so one hot cart does not dominate. The result
records accepted writes per second over the wall-clock time, and p50 / p95 of each write's latency.

If a region's /admin/config does not take "power" (lane D not merged yet), every mode that needs a power
setting is recorded as "unsupported" with no numbers: nothing is invented. A 429 or 503 is counted as refused,
never retried, and never counted as throughput.

Writes docs/scale/scale-result.json and copies it to frontend-new/public/scale-result.json.
"""
import argparse
import json
import math
import os
import platform
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "scale" / "scale-result.json"
WEB_COPY = ROOT / "frontend-new" / "public" / "scale-result.json"
DEFAULT_URLS = {"mum": "http://127.0.0.1:8001", "pun": "http://127.0.0.1:8002", "del": "http://127.0.0.1:8003"}
POWERS = ("low", "medium", "high")
ITEMS_PER_CART = 10


class Regions:
    def __init__(self, urls: dict, key: str, admin_key: str):
        self.urls, self.key, self.admin_key = urls, key, admin_key
        self.http = httpx.Client(timeout=10)
        self.original_power = {}

    def check(self):
        for region, url in self.urls.items():
            r = self.http.get(url + "/healthz")
            if r.status_code != 200:
                raise SystemExit(f"{region} at {url} is not answering /healthz ({r.status_code})")

    def set_power(self, region: str, power: str) -> bool:
        """True only when the region confirms the new power in its answer."""
        r = self.http.post(self.urls[region] + "/admin/config", json={"power": power},
                           headers={"X-API-Key": self.admin_key})
        if r.status_code != 200:
            return False
        body = r.json()
        return isinstance(body, dict) and body.get("power") == power

    def power_supported(self) -> bool:
        """Ask every region for its current power and set it back unchanged; remember it for the end."""
        for region, url in self.urls.items():
            r = self.http.post(url + "/admin/config", json={}, headers={"X-API-Key": self.admin_key})
            current = r.json().get("power") if r.status_code == 200 and isinstance(r.json(), dict) else None
            if current not in POWERS or not self.set_power(region, current):
                return False
            self.original_power[region] = current
        return True

    def restore_power(self):
        for region, power in self.original_power.items():
            self.set_power(region, power)

    def new_carts(self, region: str, count: int, run: str) -> list:
        ids = []
        for i in range(count):
            rid = "R-" + uuid.uuid4().hex[:8]
            r = self.http.post(self.urls[region] + "/v1/reports", headers={"X-API-Key": self.key}, json={
                "report_id": rid, "op_id": str(uuid.uuid4()), "title": f"bench {run} {i}", "description": "",
                "category": "cart", "severity": 1})
            if r.status_code not in (200, 201):
                raise SystemExit(f"could not create a cart on {region}: {r.status_code} {r.text[:200]}")
            ids.append(rid)
        return ids


def percentile(values: list, p: float):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(p / 100 * len(ordered)) - 1)], 1)


def send_writes(regions: Regions, targets: list, writes: int, concurrency: int, run: str) -> dict:
    """targets: the regions to spread the writes over, round robin. Returns the measured figures."""
    plan = []  # (url, cart id) per write, decided before the clock starts
    carts = {r: regions.new_carts(r, math.ceil(writes / len(targets) / ITEMS_PER_CART), run) for r in targets}
    per_region = {r: 0 for r in targets}
    for i in range(writes):
        region = targets[i % len(targets)]
        plan.append((regions.urls[region], carts[region][per_region[region] // ITEMS_PER_CART]))
        per_region[region] += 1

    local = threading.local()
    text = json.dumps({"kind": "ADD", "sku": "BENCH-1", "name": "Benchmark item", "qty": 1, "price": 1,
                       "user": "bench"}, separators=(",", ":"))

    def one(job):
        url, rid = job
        client = getattr(local, "client", None) or httpx.Client(timeout=10)
        local.client = client
        start = time.perf_counter()
        try:
            r = client.post(f"{url}/v1/reports/{rid}/notes", headers={"X-API-Key": regions.key},
                            json={"text": text, "op_id": str(uuid.uuid4())})
            status = r.status_code
        except httpx.HTTPError:
            status = None
        return status, (time.perf_counter() - start) * 1000

    began = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(one, plan))
    elapsed = time.perf_counter() - began
    ok = [ms for status, ms in results if status in (200, 201)]
    refused = sum(1 for status, _ in results if status in (429, 503))
    return {
        "status": "measured",
        "accepted": len(ok),
        "refused": refused,
        "errors": len(results) - len(ok) - refused,
        "seconds": round(elapsed, 3),
        "writes_per_s": round(len(ok) / elapsed, 1) if elapsed else None,
        "p50_ms": percentile(ok, 50),
        "p95_ms": percentile(ok, 95),
        "mean_ms": round(statistics.fmean(ok), 1) if ok else None,
    }


def unsupported(reason: str) -> dict:
    return {"status": "unsupported", "reason": reason, "writes_per_s": None, "p50_ms": None, "p95_ms": None}


def machine() -> str:
    return (f"{platform.system()} {platform.release()}, {platform.processor() or platform.machine()}, "
            f"{os.cpu_count()} logical CPUs, Python {platform.python_version()}; all regions on this one machine")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vertical", action="store_true")
    ap.add_argument("--horizontal", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--writes", type=int, default=300)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--url", action="append", default=[], help="region=url, in order (default mum, pun, del)")
    ap.add_argument("--key", default=os.environ.get("BENCH_API_KEY", "dev-officer"))
    ap.add_argument("--admin-key", default=os.environ.get("BENCH_ADMIN_KEY", "dev-admin"))
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--no-copy", action="store_true", help="do not copy the result into frontend-new/public")
    args = ap.parse_args()
    if not (args.vertical or args.horizontal or args.compare):
        ap.error("choose at least one of --vertical, --horizontal, --compare")

    urls = dict(u.split("=", 1) for u in args.url) if args.url else dict(DEFAULT_URLS)
    regions = Regions(urls, args.key, args.admin_key)
    regions.check()
    names = list(urls)
    power = regions.power_supported()
    note = None if power else "POST /admin/config does not accept \"power\" on these regions (lane D not merged)"
    print(f"regions: {', '.join(names)} | power setting: {'supported' if power else 'unsupported'}")

    result = {"measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
              "machine": machine(), "writes": args.writes, "concurrency": args.concurrency,
              "regions": urls, "power_supported": power}
    try:
        if args.vertical:
            rows = []
            for p in POWERS:
                if power and regions.set_power(names[0], p):
                    row = send_writes(regions, names[:1], args.writes, args.concurrency, f"vertical-{p}")
                else:
                    row = unsupported(note or f"{names[0]} did not confirm power {p}")
                rows.append({"power": p, "region": names[0], **row})
                print(f"vertical  {p:6} {row['status']:11} {row['writes_per_s']} writes/s  p95 {row['p95_ms']} ms")
            result["vertical"] = rows

        if args.horizontal:
            rows = []
            for k in range(1, min(3, len(names)) + 1):
                used = names[:k]
                at = "low" if power and all(regions.set_power(r, "low") for r in used) else "default"
                row = send_writes(regions, used, args.writes, args.concurrency, f"horizontal-{k}")
                rows.append({"regions": k, "used": used, "power": at, **row})
                print(f"horizontal {k} region(s) at {at:7} {row['writes_per_s']} writes/s  p95 {row['p95_ms']} ms")
            result["horizontal"] = rows

        if args.compare:
            if not power:
                result["compare"] = {"one_high": unsupported(note), "three_low": unsupported(note)}
            elif len(names) < 3:
                result["compare"] = {"one_high": unsupported("needs three regions"),
                                     "three_low": unsupported("needs three regions")}
            else:
                one = (send_writes(regions, names[:1], args.writes, args.concurrency, "compare-one-high")
                       if regions.set_power(names[0], "high") else unsupported("power high not confirmed"))
                three = (send_writes(regions, names[:3], args.writes, args.concurrency, "compare-three-low")
                         if all(regions.set_power(r, "low") for r in names[:3]) else unsupported("power low not confirmed"))
                result["compare"] = {"one_high": {"regions": 1, "power": "high", **one},
                                     "three_low": {"regions": 3, "power": "low", **three}}
            for side, row in result["compare"].items():
                print(f"compare   {side:9} {row['status']:11} {row['writes_per_s']} writes/s")
    finally:
        regions.restore_power()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    if not args.no_copy and WEB_COPY.parent.is_dir():
        WEB_COPY.write_text(args.out.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"copied to {WEB_COPY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
