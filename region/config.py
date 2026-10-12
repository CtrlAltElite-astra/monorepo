"""Every setting comes from the environment (single owner: Simrit).

Real env vars win over a .env file. Point ENV_FILE at a file to load one.
"""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str) -> None:
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _parse_peers(raw: str) -> dict:
    peers = {}
    for item in filter(None, (s.strip() for s in raw.split(","))):
        name, url = item.split("=", 1)
        peers[name.strip()] = url.strip().rstrip("/")
    return peers


def _parse_keys(raw: str) -> dict:
    keys = {}
    for item in filter(None, (s.strip() for s in raw.split(","))):
        key, actor, role = item.split(":")
        keys[key] = (actor, role)
    return keys


# writes saved per batch (transaction) for each power preset
POWER_BATCH = {"low": 1, "medium": 8, "high": 32}


@dataclass(frozen=True)
class Settings:
    region: str
    port: int
    db_path: str
    peers: dict
    peer_token: str
    api_keys: dict
    sync_interval_ms: int
    page_size: int
    http_timeout_s: float
    enable_admin: bool
    push_enabled: bool = True
    link_rate_bps: int = 0
    write_batch_ms: int = 0
    # --- TRUE credential sessions (collaboration/true). Everything below is OFF unless AUTH_ENABLED=true. ---
    auth_enabled: bool = False
    auth_sidecar_url: str = ""         # this region's loopback auth sidecar, e.g. http://127.0.0.1:4101
    auth_sidecar_token: str = ""       # service credential for that sidecar
    auth_issuer_file: str = ""         # PINNED issuer public key file {issuerId, publicJwk}
    auth_session_key_dir: str = ""     # PINNED regional session public keys, one <region>.json each
    auth_status_file: str = ""         # sidecar-mirrored, issuer-signed status snapshot
    carry_enabled: bool = True
    write_queue_max: int = 100
    link_slow_bps: int = 10000
    # --- scaling, rate limiting (contract v1.2). Everything here is OFF / today's behaviour by default. ---
    rate_actor_burst: int = 0          # per-shopper token bucket, 0 = off (read by region/api/ratelimit.py)
    rate_actor_refill_per_s: float = 1.0
    region_write_limit_per_s: int = 0  # whole-region write limit, 0 = off (region/sync/scale.py)
    power: str = "medium"              # low | medium | high: writes saved per transaction (1 / 8 / 32)


def load() -> Settings:
    _load_dotenv(os.environ.get("ENV_FILE", ".env"))
    env = os.environ.get
    region = env("REGION", "mum")
    return Settings(
        region=region,
        port=int(env("PORT", "8001")),
        db_path=env("DB_PATH", f"data/{region}.db"),
        peers=_parse_peers(env("PEERS", "")),
        peer_token=env("PEER_TOKEN", "change-me"),
        api_keys=_parse_keys(env("API_KEYS", f"dev-officer:officer-{region}-1:officer")),
        sync_interval_ms=int(env("SYNC_INTERVAL_MS", "1000")),
        page_size=int(env("PAGE_SIZE", "200")),
        http_timeout_s=float(env("HTTP_TIMEOUT_S", "1.0")),
        enable_admin=env("ENABLE_ADMIN", "1") == "1",
        push_enabled=env("PUSH_ENABLED", "1") == "1",
        link_rate_bps=int(env("LINK_RATE_BPS", "0")),
        write_batch_ms=int(env("WRITE_BATCH_MS", "0")),
        auth_enabled=env("AUTH_ENABLED", "false").lower() in ("1", "true", "yes"),
        auth_sidecar_url=env("AUTH_SIDECAR_URL", "").rstrip("/"),
        auth_sidecar_token=env("AUTH_SIDECAR_TOKEN", ""),
        auth_issuer_file=env("TRUE_ISSUER_PUBLIC_JWK_FILE", ""),
        auth_session_key_dir=env("AUTH_SESSION_KEY_DIR", ""),
        auth_status_file=env("AUTH_STATUS_FILE", ""),
        carry_enabled=env("ENABLE_CARRY", "1") == "1",
        write_queue_max=int(env("WRITE_QUEUE_MAX", "100")),
        link_slow_bps=int(env("LINK_SLOW_BPS", "10000")),
        rate_actor_burst=int(env("RATE_ACTOR_BURST", "0")),
        rate_actor_refill_per_s=float(env("RATE_ACTOR_REFILL_PER_S", "1")),
        region_write_limit_per_s=int(env("REGION_WRITE_LIMIT_PER_S", "0")),
        power=env("POWER", "medium").strip().lower() if env("POWER", "medium").strip().lower() in POWER_BATCH else "medium",
    )


settings = load()


def add_peer(name: str, url: str) -> None:
    """Hot-reload PEERS for node discovery / join (PDF 2 Section 6.8, 6.10)."""
    global settings
    import dataclasses
    new_peers = dict(settings.peers)
    new_peers[name] = url.rstrip("/")
    settings = dataclasses.replace(settings, peers=new_peers)



# Settings that POST /admin/config may change at runtime, with their validators (contract v1.2, section 3.1).
RUNTIME_KEYS = ("rate_actor_burst", "rate_actor_refill_per_s", "region_write_limit_per_s", "power")


def _whole(v, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError(f"{name} must be a whole number, 0 or more")
    return v


def validate_runtime(changes: dict) -> dict:
    """Return the cleaned changes, or raise ValueError naming the first problem. Unknown keys are an error."""
    unknown = sorted(set(changes) - set(RUNTIME_KEYS))
    if unknown:
        raise ValueError(f"unknown setting(s): {', '.join(unknown)}")
    out = {}
    for k, v in changes.items():
        if k == "power":
            if v not in POWER_BATCH:
                raise ValueError("power must be one of: low, medium, high")
            out[k] = v
        elif k == "rate_actor_refill_per_s":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ValueError("rate_actor_refill_per_s must be a number, 0 or more")
            out[k] = float(v)
        else:
            out[k] = _whole(v, k)
    return out


def update_runtime(changes: dict) -> None:
    """Apply already validated runtime changes (the frozen Settings object is replaced, never mutated)."""
    global settings
    import dataclasses
    settings = dataclasses.replace(settings, **changes)


def runtime_values() -> dict:
    s = settings
    return {"rate_actor_burst": s.rate_actor_burst, "rate_actor_refill_per_s": s.rate_actor_refill_per_s,
            "region_write_limit_per_s": s.region_write_limit_per_s, "power": s.power}
