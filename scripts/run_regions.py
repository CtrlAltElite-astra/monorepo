"""Start all regions, one process each.   python scripts/run_regions.py [mum pun]

Ctrl+C stops them all. Data lives in data/<region>.db and survives restarts.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from run_one import PORTS, spawn  # noqa: E402

if __name__ == "__main__":
    regions = sys.argv[1:] or os.environ.get("REGIONS", "mum,pun").split(",")
    procs = {r: spawn(r, regions) for r in regions}
    print("Regions up: " + ", ".join(f"{r} http://127.0.0.1:{PORTS[r]}" for r in regions))
    print("Dashboard: cd dashboard && npm run dev   |   Ctrl+C to stop")
    reported = set()
    try:
        # One region dying must NOT stop the others (that is the point of the system).
        while any(p.poll() is None for p in procs.values()):
            for r, p in procs.items():
                if p.poll() is not None and r not in reported:
                    reported.add(r)
                    print(f"region {r} exited ({p.returncode}); others keep running. Restart: python scripts/run_one.py {r}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
