"""envelope.new_event (owner: Rahil): the event a local write stores, built one way for every caller."""
import pytest

from region.engine import admit, canonical, envelope, signing
from region.engine.derive import derive
from region.engine.model import Replica

MUM, MUM_PUB = signing.generate()
CART = {"title": "Dev", "description": "", "category": "cart", "severity": 1, "status": "open"}


@pytest.fixture(autouse=True)
def node(monkeypatch):
    """This process is node mum. Module state is restored afterwards."""
    for name in ("_private", "_public", "_loaded"):
        monkeypatch.setattr(signing, name, getattr(signing, name))
    signing.configure(MUM, {"mum": MUM_PUB})


def cart_with_one_item():
    mum = Replica("mum", MUM)
    mum.write(1000, "CREATE", "R-0000de01", value=CART, op_id="dev-1")
    mum.write(1001, "NOTE", "R-0000de01", value={"text": '{"kind":"ADD","sku":"MAT-01"}'}, op_id="dev-2")
    return mum


def test_a_write_built_here_is_exactly_what_the_in_memory_node_writes():
    mum = cart_with_one_item()
    before = mum.view("R-0000de01")
    ev = envelope.new_event("mum", 3, (1002, 0), "SET", "R-0000de01", "status", "closed", before,
                            op_id="dev-3", wall="2026-10-12T15:00:00+00:00")
    twin = mum.write(1002, "SET", "R-0000de01", "status", "closed", op_id="dev-3")
    assert ev == dict(twin, wall=ev["wall"], hash=ev["hash"], sig=ev["sig"]) and ev["seq"] == twin["seq"]
    assert admit.reject_reason(ev, {"mum": MUM_PUB}) is None  # sealed and signed by this node's key


def test_set_and_resolve_replace_every_live_value_and_create_and_note_replace_nothing():
    mum = cart_with_one_item()
    before = mum.view("R-0000de01")
    for kind in ("SET", "RESOLVE"):
        ev = envelope.new_event("mum", 3, (1002, 0), kind, "R-0000de01", "severity", 4, before)
        assert ev["supersedes"] == before["live"]["severity"] == ["mum:1"]
    assert envelope.new_event("mum", 3, (1002, 0), "NOTE", "R-0000de01", value={"text": "x"},
                              view_before=before)["supersedes"] == []
    assert envelope.new_event("mum", 1, (1, 0), "CREATE", "R-00000002", value=CART)["supersedes"] == []


def test_observed_is_recorded_only_where_the_policy_needs_it():
    before = cart_with_one_item().view("R-0000de01")
    close = envelope.new_event("mum", 3, (1002, 0), "SET", "R-0000de01", "status", "closed", before)
    assert close["observed"] == ["mum:2", "mum:1"]  # the item and the delivery speed it saw
    assert envelope.new_event("mum", 3, (1002, 0), "NOTE", "R-0000de01", value={"text": "x"},
                              view_before=before)["observed"] == ["mum:1"]  # the status it saw
    assert "observed" not in envelope.new_event("mum", 3, (1002, 0), "SET", "R-0000de01", "title", "Dev K", before)


@pytest.mark.parametrize("kind, field, value, bulk, lane", [
    ("SET", "status", "closed", False, 0),     # order placed
    ("SET", "severity", 4, False, 0),          # raised to Same-day
    ("SET", "title", "Dev K", False, 1),
    ("NOTE", None, {"text": "x"}, False, 2),   # cart activity
    ("SET", "title", "Dev K", True, 2),        # bulk seed
])
def test_the_replication_lane_is_stored_on_the_event(kind, field, value, bulk, lane):
    before = cart_with_one_item().view("R-0000de01")
    ev = envelope.new_event("mum", 3, (1002, 0), kind, "R-0000de01", field, value, before, bulk=bulk)
    assert ev["priority"] == lane


def test_op_id_actor_and_role_are_sealed_into_the_hash():
    ev = envelope.new_event("mum", 1, (5, 0), "CREATE", "R-00000003", value=CART, op_id="7f2a",
                            actor="officer-mum-1", role="officer", auth_mode="online")
    assert (ev["op_id"], ev["actor"], ev["role"], ev["auth_mode"]) == ("7f2a", "officer-mum-1", "officer", "online")
    assert canonical.event_hash(ev) == ev["hash"]
    assert admit.reject_reason(dict(ev, role="supervisor"), {"mum": MUM_PUB}) == "bad_hash"  # nobody can upgrade it


def test_a_node_without_a_key_writes_unsigned_events():
    signing.configure(None, {"mum": MUM_PUB})
    ev = envelope.new_event("mum", 1, (5, 0), "CREATE", "R-00000004", value=CART)
    assert ev["sig"] is None and canonical.event_hash(ev) == ev["hash"]
    assert envelope.new_event("mum", 1, (5, 0), "CREATE", "R-00000004", value=CART, signer=None)["sig"] is None


def test_derive_accepts_what_new_event_builds():
    ev = envelope.new_event("mum", 1, (5, 0), "CREATE", "R-00000005", value=CART)
    assert derive([ev])["state"]["title"] == "Dev" and derive([ev])["incomplete"] is False
