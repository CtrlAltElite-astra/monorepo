"""Load Generator & Benchmark Script (Simrit / Lane D).

Generates synthetic writes across regions, measuring offered vs accepted rates,
commit latency percentiles (p50, p95), and sync lag.
Used for Trial & Reward curveball validation (PDF 2 Section 17).
"""
import argparse
import statistics
import time
import httpx

KEY = {"X-API-Key": "dev-officer"}


import argparse
import json
import random
import statistics
import time
import uuid

import httpx

KEY = {"X-API-Key": "dev-officer"}

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


def run_benchmark(target_url: str, total_writes: int, concur: int = 1, shop_mode: bool = True):
    print(f"Running benchmark: {total_writes} writes on {target_url} (shop_mode={shop_mode}, concurrency={concur})...")
    latencies = []
    accepted = 0
    rejected = 0

    client = httpx.Client(timeout=10.0)
    start_time = time.time()

    active_carts = []

    for i in range(total_writes):
        op_id = str(uuid.uuid4())
        shopper = random.choice(SHOPPERS)
        t0 = time.time()
        try:
            if shop_mode and active_carts and random.random() < 0.6:
                # Add item NOTE to existing cart
                cart_id = random.choice(active_carts)
                prod = random.choice(PRODUCTS)
                note_payload = {
                    "kind": "ADD",
                    "sku": prod["sku"],
                    "name": prod["name"],
                    "qty": 1,
                    "price": prod["price"],
                    "user": shopper,
                }
                r = client.post(
                    f"{target_url}/v1/reports/{cart_id}/notes",
                    json={"text": json.dumps(note_payload), "op_id": op_id},
                    headers=KEY,
                )
            else:
                # Create a new cart / incident report
                payload = {
                    "title": f"{shopper}'s Cart {i}",
                    "category": "shelter",
                    "severity": random.choice([1, 2, 3, 4, 5]),
                    "description": f"Cart for {shopper}",
                    "op_id": op_id,
                }
                r = client.post(f"{target_url}/v1/reports", json=payload, headers=KEY)
                if r.status_code == 201:
                    rid = r.json().get("report", {}).get("report_id")
                    if rid:
                        active_carts.append(rid)

            t_req = (time.time() - t0) * 1000
            if r.status_code in (200, 201):
                accepted += 1
                latencies.append(t_req)
            else:
                rejected += 1
        except Exception:
            rejected += 1

    total_time = time.time() - start_time
    throughput = total_writes / total_time if total_time > 0 else 0

    print("\n=== Benchmark Results ===")
    print(f"Total Offered:   {total_writes}")
    print(f"Accepted (2xx):  {accepted}")
    print(f"Rejected / Err:  {rejected}")
    print(f"Duration:        {round(total_time, 2)} s")
    print(f"Throughput:      {round(throughput, 1)} req/s")

    if latencies:
        p50 = statistics.median(latencies)
        latencies.sort()
        idx95 = int(len(latencies) * 0.95)
        p95 = latencies[min(idx95, len(latencies) - 1)]
        print(f"Latency p50:     {round(p50, 1)} ms")
        print(f"Latency p95:     {round(p95, 1)} ms")
        print(f"Latency Min/Max: {round(min(latencies), 1)} ms / {round(max(latencies), 1)} ms")


def main():
    parser = argparse.ArgumentParser(description="SyncShield loadgen benchmark")
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("-n", "--count", type=int, default=50)
    parser.add_argument("-c", "--concurrency", type=int, default=1)
    parser.add_argument("--shop", action="store_true", default=True, help="Use shop domain payload")
    args = parser.parse_args()
    run_benchmark(args.url, args.count, concur=args.concurrency, shop_mode=args.shop)


if __name__ == "__main__":
    main()

