"""Split-brain scenario (Simrit / Lane D).

Cuts link between mum and pun, performs concurrent writes, verifies isolation,
then heals and verifies convergence.
"""
import time
import httpx

MUM = "http://127.0.0.1:8001"
PUN = "http://127.0.0.1:8002"
KEY = {"X-API-Key": "dev-officer"}


def main():
    print("--- 1. Creating initial report on Mumbai ---")
    r = httpx.post(
        f"{MUM}/v1/reports",
        headers=KEY,
        json={"title": "Kurla Bridge Check", "category": "road_block", "severity": 2},
        timeout=5,
    )
    r.raise_for_status()
    rep = r.json()
    rep_id = rep["report"]["report_id"]
    print(f"Created: {rep_id} ({rep['event_id']})")
    time.sleep(2)

    print("--- 2. Cutting link Mumbai <-> Pune ---")
    httpx.post(f"{MUM}/admin/links/pun", json={"mode": "down"}, headers=KEY, timeout=3).raise_for_status()
    httpx.post(f"{PUN}/admin/links/mum", json={"mode": "down"}, headers=KEY, timeout=3).raise_for_status()
    print("Partition active. Both links set to DOWN.")

    print("--- 3. Concurrent edits on both sides ---")
    r_mum = httpx.patch(f"{MUM}/v1/reports/{rep_id}", json={"field": "severity", "value": 5}, headers=KEY, timeout=3)
    r_pun = httpx.patch(f"{PUN}/v1/reports/{rep_id}", json={"field": "severity", "value": 4}, headers=KEY, timeout=3)
    print(f"Mumbai edit status: {r_mum.status_code}")
    print(f"Pune edit status:   {r_pun.status_code}")

    time.sleep(1)
    st_mum = httpx.get(f"{MUM}/v1/status").json()
    st_pun = httpx.get(f"{PUN}/v1/status").json()
    print(f"Mumbai state hash during partition: {st_mum['state_hash']}")
    print(f"Pune state hash during partition:   {st_pun['state_hash']}")
    assert st_mum["state_hash"] != st_pun["state_hash"], "State hashes should differ during partition"

    print("--- 4. Healing partition ---")
    httpx.post(f"{MUM}/admin/links/pun", json={"mode": "up"}, headers=KEY, timeout=3).raise_for_status()
    httpx.post(f"{PUN}/admin/links/mum", json={"mode": "up"}, headers=KEY, timeout=3).raise_for_status()
    print("Links restored to UP. Awaiting fast heal and convergence...")

    for _ in range(15):
        time.sleep(0.5)
        st_mum = httpx.get(f"{MUM}/v1/status").json()
        st_pun = httpx.get(f"{PUN}/v1/status").json()
        if st_mum["state_hash"] == st_pun["state_hash"]:
            print(f"CONVERGED! Final state hash: {st_mum['state_hash']}")
            view = httpx.get(f"{MUM}/v1/reports/{rep_id}").json()
            print(f"Final resolved severity: {view['state']['severity']}")
            print(f"Recorded conflicts: {view['conflicts']}")
            return

    raise SystemExit("Convergence timed out!")


if __name__ == "__main__":
    main()
