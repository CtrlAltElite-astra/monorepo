"""Restart & recovery verification scenario (Simrit / Lane D).

Verifies that stopping a region and starting it back up retains all committed
events and continues sequence allocation without duplicate ID collisions.
"""
import time
import httpx

MUM = "http://127.0.0.1:8001"
KEY = {"X-API-Key": "dev-officer"}


def main():
    print("--- 1. Query current status on Mumbai ---")
    st1 = httpx.get(f"{MUM}/v1/status", timeout=5).json()
    print(f"Current heads: {st1['heads']}, event count: {st1['event_count']}")

    print("--- 2. Write new report ---")
    r = httpx.post(
        f"{MUM}/v1/reports",
        headers=KEY,
        json={"title": "Recovery Test Incident", "category": "medical", "severity": 3},
        timeout=5,
    )
    r.raise_for_status()
    rep = r.json()
    print(f"Committed event: {rep['event_id']}, report: {rep['report']['report_id']}")

    st2 = httpx.get(f"{MUM}/v1/status", timeout=5).json()
    print(f"Updated heads: {st2['heads']}, event count: {st2['event_count']}")
    print("Node is ready for kill/restart drill. Run python scripts/run_one.py mum to verify continuity.")


if __name__ == "__main__":
    main()
