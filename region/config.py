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
    carry_enabled: bool = True
    write_queue_max: int = 100
    link_slow_bps: int = 10000


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
        carry_enabled=env("ENABLE_CARRY", "1") == "1",
        write_queue_max=int(env("WRITE_QUEUE_MAX", "100")),
        link_slow_bps=int(env("LINK_SLOW_BPS", "10000")),
    )


settings = load()


def add_peer(name: str, url: str) -> None:
    """Hot-reload PEERS for node discovery / join (PDF 2 Section 6.8, 6.10)."""
    global settings
    import dataclasses
    new_peers = dict(settings.peers)
    new_peers[name] = url.rstrip("/")
    settings = dataclasses.replace(settings, peers=new_peers)

