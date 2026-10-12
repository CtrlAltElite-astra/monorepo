"""Load generator (Simrit / Lane D): shop-style writes, measured honestly.

Reports offered, accepted and refused writes, throughput and commit latency (p50, p95). Everything it prints
comes from the responses it actually got.

Examples
    python scripts/loadgen.py --url http://127.0.0.1:8001 -n 300 -c 16          # one region, 16 at a time
    python scripts/loadgen.py --actor Bob -n 40                                  # spam as Bob (key dev-bob)
    python scripts/loadgen.py --spread mum,pun,del -n 300 -c 12                  # split across regions

Each shopper has a home region (shoppers are dealt to the listed regions in turn), and a cart's notes always go
to the region that holds the cart, so no write ever depends on replication having finished.
"""
import argparse
import json
import random
import statistics
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_one import PORTS  # noqa: E402

SHOPPERS = ["Alice", "Bob", "Chitra", "Dev", "Esha"]

PRODUCTS = [
    {"sku": "SANDAL-01", "name": "Kolhapuri sandals", "price": 1299},
    {"sku": "LAMP-01", "name": "Desk lamp", "price": 1099},
    {"sku": "BOTTLE-01", "name": "Steel water bottle", "price": 499},
    {"sku": "BAG-01", "name": "Backpack", "price": 1599},
    {"sku": "KURTA-01", "name": "Cotton kurta", "price": 899},
    {"sku": "GIFT-500", "name": "Gift card", "price": 500},
    {"sku": "FCOFF-500", "name": "Filter coffee 500 g", "price": 449},
    {"sku": "CHAI-01", "name": "Masala chai box", "price": 349},
    {"sku": "EARBUD-01", "name": "Wireless earbuds", "price": 1999},
    {"sku": "MAT-01", "name": "Yoga mat", "price": 799},
]


def shopper_key(name: str) -> str:
    """The API key of a shopper. scripts/run_one.py gives every shopper one (dev-alice, dev-bob, ...)."""
    return f"dev-{name.lower()}"


def percentile(sorted_values: list, q: float) -> float:
    return sorted_values[min(int(len(sorted_values) * q), len(sorted_values) - 1)]


class Run:
    def __init__(self, urls: dict, shoppers: list, key_for, shop: bool):
        self.urls = urls                    # region -> base url
        self.shoppers = shoppers
        self.key_for = key_for
        self.shop = shop
        regions = list(urls)
        self.home = {s: regions[i % len(regions)] for i, s in enumerate(shoppers)}
        self.carts = {s: [] for s in shoppers}   # shopper -> cart ids at the shopper's home region
        self.lock = threading.Lock()
        self.latencies = []
        self.codes = Counter()
        self.per_region = Counter()
        self.limited = None                       # first refusal body, for the report
        self.local = threading.local()            # one HTTP client per worker thread (building one costs ~300 ms)

    def one(self, i: int) -> None:
        shopper = self.shoppers[i % len(self.shoppers)]
        region = self.home[shopper]
        base = self.urls[region]
        headers = {"X-API-Key": self.key_for(shopper)}
        op_id = str(uuid.uuid4())
        with self.lock:
            cart = random.choice(self.carts[shopper]) if self.carts[shopper] and random.random() < 0.6 else None
        t0 = time.time()
        try:
            c = getattr(self.local, "client", None)
            if c is None:
                c = self.local.client = httpx.Client(timeout=10.0)
            if self.shop and cart:
                prod = random.choice(PRODUCTS)
                note = {"kind": "ADD", "sku": prod["sku"], "name": prod["name"], "qty": 1,
                        "price": prod["price"], "user": shopper}
                r = c.post(f"{base}/v1/reports/{cart}/notes", json={"text": json.dumps(note), "op_id": op_id},
                           headers=headers)
            else:
                payload = {"title": shopper, "category": "cart", "severity": random.choice([1, 2, 3]),
                           "description": f"Cart {i}", "op_id": op_id}
                r = c.post(f"{base}/v1/reports", json=payload, headers=headers)
                if r.status_code == 201:
                    rid = (r.json().get("report") or {}).get("report_id")
                    if rid:
                        with self.lock:
                            self.carts[shopper].append(rid)
            ms = (time.time() - t0) * 1000
            with self.lock:
                self.codes[r.status_code] += 1
                self.per_region[region] += 1
                if r.status_code in (200, 201):
                    self.latencies.append(ms)
                elif self.limited is None and r.status_code in (429, 503):
                    self.limited = {"status": r.status_code, "body": r.text[:120],
                                    "retry_after": r.headers.get("retry-after")}
        except httpx.HTTPError as exc:
            with self.lock:
                self.codes[type(exc).__name__] += 1


def run_benchmark(urls: dict, total: int, concurrency: int, shoppers: list, key_for, shop: bool = True) -> dict:
    where = ", ".join(f"{r} {u}" for r, u in urls.items())
    print(f"Running {total} writes, {concurrency} at a time, as {', '.join(shoppers)} on {where} ...")
    run = Run(urls, shoppers, key_for, shop)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(run.one, range(total)))
    secs = time.time() - t0

    accepted = run.codes[200] + run.codes[201]
    refused = run.codes[429] + run.codes[503]
    other = sum(n for c, n in run.codes.items() if c not in (200, 201, 429, 503))
    lat = sorted(run.latencies)
    result = {"offered": total, "accepted": accepted, "refused": refused, "errors": other,
              "seconds": round(secs, 2), "writes_per_s": round(accepted / secs, 1) if secs > 0 else 0,
              "p50_ms": round(statistics.median(lat), 1) if lat else None,
              "p95_ms": round(percentile(lat, 0.95), 1) if lat else None,
              "by_status": {str(k): v for k, v in sorted(run.codes.items(), key=lambda kv: str(kv[0]))},
              "by_region": dict(run.per_region), "first_refusal": run.limited}
    print("\n=== Results ===")
    print(f"Offered:     {result['offered']}")
    print(f"Accepted:    {accepted}")
    print(f"Refused:     {refused}   (429 rate limit / 503 overloaded)")
    print(f"Errors:      {other}")
    print(f"Duration:    {result['seconds']} s")
    print(f"Throughput:  {result['writes_per_s']} accepted writes/s")
    if lat:
        print(f"Latency:     p50 {result['p50_ms']} ms, p95 {result['p95_ms']} ms (accepted writes only)")
    print(f"By status:   {result['by_status']}")
    if len(urls) > 1:
        print(f"By region:   {result['by_region']}")
    if run.limited:
        print(f"First refusal: {run.limited}")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="SyncShield load generator")
    ap.add_argument("--url", default="http://127.0.0.1:8001", help="one region (ignored with --spread)")
    ap.add_argument("--spread", help="comma list of regions to split the shoppers across, e.g. mum,pun,del")
    ap.add_argument("--actor", help="send everything as this one shopper (e.g. Bob): spam as Bob")
    ap.add_argument("--key", help="API key to use for every request (default: dev-officer, or dev-<actor> with --actor)")
    ap.add_argument("-n", "--count", type=int, default=50)
    ap.add_argument("-c", "--concurrency", type=int, default=1)
    ap.add_argument("--json", action="store_true", help="print the result as JSON only")
    args = ap.parse_args()

    if args.spread:
        regions = [r.strip() for r in args.spread.split(",") if r.strip()]
        bad = [r for r in regions if r not in PORTS]
        if bad:
            sys.exit(f"unknown region(s): {', '.join(bad)} (known: {', '.join(PORTS)})")
        urls = {r: f"http://127.0.0.1:{PORTS[r]}" for r in regions}
    else:
        urls = {"url": args.url.rstrip("/")}
    shoppers = [args.actor] if args.actor else SHOPPERS
    if args.key:
        key_for = lambda _s: args.key                    # noqa: E731
    elif args.actor:
        key_for = shopper_key                            # as Bob: key dev-bob, so a per-shopper limit sees one actor
    else:
        key_for = lambda _s: "dev-officer"               # noqa: E731  (works on every region started by hand)
    result = run_benchmark(urls, args.count, args.concurrency, shoppers, key_for)
    if args.json:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
