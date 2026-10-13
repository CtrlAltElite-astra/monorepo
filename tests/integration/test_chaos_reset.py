"""Reset everything (scripts/chaos_agent.py): the default regions come back, Bengaluru stays dormant until it is added.

Pure logic, no processes: the agent's stop / delete / start steps are replaced by recorders, so this never touches a
running demo."""
import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException

AGENT = Path(__file__).resolve().parents[2] / "scripts" / "chaos_agent.py"


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("REGIONS", "mum,pun,del,blr")
    monkeypatch.setenv("ADMIN_KEY", "k")
    spec = importlib.util.spec_from_file_location("chaos_agent_under_test", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_world(agent, monkeypatch, running):
    """Replace the process and file steps; returns the recorder."""
    w = type("World", (), {})()
    w.running, w.stopped, w.wiped, w.started = set(running), [], [], []

    def stop(region):
        w.stopped.append(region)
        was = region in w.running
        w.running.discard(region)
        return was

    def delete_db(region):
        w.wiped.append(region)
        return True

    def start(region, x_admin_key=None):
        w.started.append(region)
        w.running.add(region)
        return {"region": region, "already_running": False, "pid": 1, "up": True}

    monkeypatch.setattr(agent, "_stop", stop)
    monkeypatch.setattr(agent, "_delete_db", delete_db)
    monkeypatch.setattr(agent, "start", start)
    return w


def test_reset_all_wipes_every_region_but_brings_back_only_the_default_ones(agent, monkeypatch):
    w = fake_world(agent, monkeypatch, running={"mum", "pun", "del", "blr"})     # Bengaluru had been added
    out = agent.reset_all(x_admin_key="k")

    assert sorted(w.stopped) == ["blr", "del", "mum", "pun"]                    # everything was stopped ...
    assert sorted(w.wiped) == ["blr", "del", "mum", "pun"]                      # ... and wiped together
    assert w.started == ["mum", "pun", "del"]                                   # only the defaults come back
    assert w.running == {"mum", "pun", "del"}                                   # Bengaluru is dormant
    assert out == {"reset": True, "wiped": ["mum", "pun", "del", "blr"], "started": ["mum", "pun", "del"], "dormant": ["blr"]}


def test_bengaluru_was_wiped_so_adding_it_later_starts_it_empty(agent, monkeypatch):
    w = fake_world(agent, monkeypatch, running={"mum", "pun", "del", "blr"})
    agent.reset_all(x_admin_key="k")
    assert "blr" in w.wiped and "blr" not in w.running


def test_reset_all_starts_the_default_regions_even_when_some_were_down(agent, monkeypatch):
    w = fake_world(agent, monkeypatch, running=set())                           # everything had crashed
    out = agent.reset_all(x_admin_key="k")
    assert w.started == ["mum", "pun", "del"] and out["dormant"] == ["blr"]


def test_reset_all_needs_the_admin_key_and_changes_nothing_without_it(agent, monkeypatch):
    w = fake_world(agent, monkeypatch, running={"mum", "pun", "del", "blr"})
    with pytest.raises(HTTPException) as e:
        agent.reset_all(x_admin_key="wrong")
    assert e.value.status_code == 401
    assert w.stopped == [] and w.wiped == [] and w.started == []
