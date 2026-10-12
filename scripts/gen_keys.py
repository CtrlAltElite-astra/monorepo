"""Create Ed25519 signing keys for the regions.   python scripts/gen_keys.py [mum pun del]

Private keys go to keys/<region>.key (git-ignored). The public registry is written to
region/trust/public_keys.json. Existing keys are kept, so this is safe to re-run.
run_one.py / run_regions.py call the same function, so you rarely need to run this by hand.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from region.engine import signing  # noqa: E402

if __name__ == "__main__":
    regions = sys.argv[1:] or ["mum", "pun", "del"]
    reg = signing.ensure_keys(regions)
    for r, k in sorted(reg.items()):
        print(f"{r}: {k}")
    print("keys in", signing.keys_dir(), "| registry", signing.public_keys_file())
