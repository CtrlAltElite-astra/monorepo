"""Create the Ed25519 signing keys (owner: Rahil).   python scripts/gen_keys.py [mum pun del] [--force]

Writes keys/<region>.key (private, git-ignored: never commit or share it) and
records each public key in region/trust/public_keys.json. Safe to run again:
an existing key is kept unless --force is given, and the registry is always
rewritten to match the key files.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from region.engine import signing  # noqa: E402

REGIONS = ["mum", "pun", "del"]


def make_keys(regions, keys_dir=signing.KEYS_DIR, registry=signing.REGISTRY, force=False) -> dict:
    """Make sure every region has a key pair. Returns {region: 'created' | 'kept'}."""
    keys_dir, registry = Path(keys_dir), Path(registry)
    keys_dir.mkdir(parents=True, exist_ok=True)
    public = json.loads(registry.read_text(encoding="utf-8")) if registry.is_file() else {}
    done = {}
    for region in regions:
        key_file = keys_dir / f"{region}.key"
        if key_file.is_file() and not force:
            private, done[region] = key_file.read_text(encoding="ascii").strip(), "kept"
        else:
            private, done[region] = signing.generate()[0], "created"
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only where the OS supports it
            with open(fd, "w", encoding="ascii") as f:
                f.write(private + "\n")
        public[region] = signing.public_of(private)
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(public, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Create Ed25519 signing keys for the regions.")
    ap.add_argument("regions", nargs="*", default=REGIONS)
    ap.add_argument("--force", action="store_true", help="replace existing keys (peers must get the new registry)")
    ap.add_argument("--keys-dir", default=str(signing.KEYS_DIR))
    ap.add_argument("--registry", default=str(signing.REGISTRY))
    args = ap.parse_args()
    for region, what in make_keys(args.regions, args.keys_dir, args.registry, args.force).items():
        print(f"{region}: {what}  {Path(args.keys_dir) / (region + '.key')}")
    print(f"public keys: {args.registry}")
