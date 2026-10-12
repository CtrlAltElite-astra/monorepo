"""Re-export so both import paths work: region.engine.signing (plan) and region.trust.signing (ingest hook)."""
from region.engine.signing import (  # noqa: F401
    can_sign, ensure_keys, has_key, public_keys, sign, verify,
)
