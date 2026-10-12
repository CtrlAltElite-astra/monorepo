"""Shared fixtures for the engine tests (owner: Rahil)."""
import json

import pytest

from region.engine import signing


@pytest.fixture
def provision(tmp_path, monkeypatch):
    """Give this process signing keys the way a region gets them: key files named by SIGNING_KEYS_DIR and
    a registry named by PUBLIC_KEYS_FILE. provision({region: private}, {region: public})."""
    def _provision(private: dict, public: dict):
        keys = tmp_path / "keys"
        keys.mkdir(exist_ok=True)
        for region, key in private.items():
            (keys / f"{region}.key").write_text(key, encoding="ascii")
        registry = tmp_path / "public_keys.json"
        registry.write_text(json.dumps(public), encoding="utf-8")
        monkeypatch.setenv("SIGNING_KEYS_DIR", str(keys))
        monkeypatch.setenv("PUBLIC_KEYS_FILE", str(registry))
        signing._priv_cache.clear()
        return keys, registry
    yield _provision
    signing._priv_cache.clear()
