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


def run_benchmark(target_url: str, total_writes: int, concur: int = 1):
    print(f"Running benchmark: {total_writes} writes on {target_url}...")
    latencies = []
    accepted = 0
    rejected = 0

    client = httpx.Client(timeout=10.0)
    start_time = time.time()

    for i in range(total_writes):
        payload = {
            "title": f"Bench Incident {i}",
            "category": "flood" if i % 2 == 0 else "medical",
            "severity": (i % 5) + 1,
            "description": f"Generated load test entry {i}",
        }
        t0 = time.time()
        try:
            r = client.post(f"{target_url}/v1/reports", json=payload, headers=KEY)
            t_req = (time.time() - t0) * 1000  # ms
            if r.status_code == 201:
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
    print(f"Accepted (201):  {accepted}")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("-n", "--count", type=int, default=50)
    args = parser.parse_args()
    run_benchmark(args.url, args.count)


if __name__ == "__main__":
    main()
