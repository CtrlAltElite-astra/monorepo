"""Shared fixtures for the Lane A API tests (owner: Akshit).

Each test gets ONE in-process region ("mum") on a fresh temp SQLite file. The pull loop is not
running (no lifespan) unless a test opts in with `with TestClient(app)`, so tests are deterministic.
"""
import os

os.environ.setdefault("ENV_FILE", "none")  # never read a developer's .env

import dataclasses  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from region import config, store  # noqa: E402
from region.engine import canonical  # noqa: E402
from region.main import app  # noqa: E402

KEY = {"X-API-Key": "dev-officer"}


def make_settings(tmp_path, **overrides):
    base = dict(
        region="mum", db_path=str(tmp_path / "mum.db"), peers={"pun": "http://127.0.0.1:9"},
        peer_token="t", api_keys={"dev-officer": ("officer-mum-1", "officer")}, sync_interval_ms=100,
    )
    base.update(overrides)
    return dataclasses.replace(config.settings, **base)


@pytest.fixture(autouse=True)
def no_dev_keys(tmp_path, monkeypatch):
    """Tests never see the developer's real keys/ or key registry (run scripts create them).
    A test that needs keys overrides these two variables (see tests/api/test_carry_and_idempotency.py)."""
    monkeypatch.setenv("SIGNING_KEYS_DIR", str(tmp_path / "no-keys"))
    monkeypatch.setenv("PUBLIC_KEYS_FILE", str(tmp_path / "no-registry.json"))


@pytest.fixture
def region(tmp_path, monkeypatch):
    """A freshly initialised region. Yields the DB path."""
    settings = make_settings(tmp_path)
    monkeypatch.setattr(config, "settings", settings)
    store.init()
    yield settings.db_path
    store.close()


@pytest.fixture
def client(region):
    return TestClient(app)


@pytest.fixture
def headers():
    return dict(KEY)


@pytest.fixture
def create(client):
    """create(**overrides) -> response of POST /v1/reports."""
    def _create(**overrides):
        body = {"title": "Wall collapse, Kurla", "category": "collapse", "severity": 3}
        body.update(overrides)
        return client.post("/v1/reports", json=body, headers=KEY)
    return _create


def create_value(title="Flooded road", severity=3):
    """A complete CREATE value (every report field), as the real write path builds it."""
    return {"title": title, "category": "flood", "severity": severity, "status": "open",
            "assigned_team": None, "location": None, "description": "", "people_estimate": None}


@pytest.fixture
def mk_event():
    """mk_event(origin, seq, type, report_id, ...) -> a well-formed event as a peer would send it."""
    def _mk(origin, seq, type_, report_id, field=None, value=None, supersedes=(), hlc=None):
        ev = dict(v=1, event_id=f"{origin}:{seq}", origin=origin, seq=seq, report_id=report_id,
                  type=type_, field=field, value=value, supersedes=list(supersedes),
                  hlc=list(hlc or (4_000_000_000_000 + seq, 0)), priority=1,
                  actor=f"officer-{origin}-1", reason=None, wall="2026-10-12T15:00:00+05:30", sig=None)
        ev["hash"] = canonical.event_hash(ev)
        return ev
    return _mk
