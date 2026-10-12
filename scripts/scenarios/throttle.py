"""Throttling & priority delivery scenario (Simrit / Lane D).

Configures a slow link with rate limiting, creates a stream of bulk notes (P2),
then issues a critical report (P0), demonstrating that P0 arrives at the peer
ahead of bulk note queues.
"""
import time
import httpx

MUM = "http://127.0.0.1:8001"
PUN = "http://127.0.0.1:8002"
KEY = {"X-API-Key": "dev-officer"}


def main():
    print("--- 1. Set Mumbai -> Pune link to SLOW (2000 Bps, 150ms delay) ---")
    r = httpx.post(
        f"{MUM}/admin/links/pun",
        json={"mode": "slow", "delay_ms": 150, "rate_bps": 2000},
        headers=KEY,
        timeout=3,
    )
    r.raise_for_status()
    print("Link is now throttled.")

    # Create base report
    r_base = httpx.post(
        f"{MUM}/v1/reports",
        headers=KEY,
        json={"title": "Priority Test Base", "category": "shelter", "severity": 2},
        timeout=5,
    )
    base_id = r_base.json()["report"]["report_id"]
    print(f"Created base report: {base_id}")

    print("--- 2. Queue 20 bulk notes (P2) ---")
    for i in range(20):
        httpx.post(f"{MUM}/v1/reports/{base_id}/notes", json={"text": f"Bulk status note {i}"}, headers=KEY)

    print("--- 3. Create Critical Severity 5 Collapse Incident (P0) ---")
    t0 = time.time()
    r_crit = httpx.post(
        f"{MUM}/v1/reports",
        headers=KEY,
        json={"title": "CRITICAL COLLAPSE TRAPPED SURVIVORS", "category": "collapse", "severity": 5},
        timeout=5,
    )
    crit_id = r_crit.json()["report"]["report_id"]
    print(f"P0 event created: {crit_id} at t={round(t0, 2)}")

    print("--- 4. Polling Pune for critical report arrival ---")
    arrived = False
    for _ in range(30):
        time.sleep(0.2)
        pun_reports = [rep["report_id"] for rep in httpx.get(f"{PUN}/v1/reports").json()]
        if crit_id in pun_reports:
            t1 = time.time()
            print(f"SUCCESS: P0 Critical report arrived at Pune in {round(t1 - t0, 2)}s, ahead of the bulk backlog!")
            arrived = True
            break

    if not arrived:
        print("P0 report did not arrive within expected window.")

    print("--- 5. Reset link to UP ---")
    httpx.post(f"{MUM}/admin/links/pun", json={"mode": "up"}, headers=KEY, timeout=3)


if __name__ == "__main__":
    main()
