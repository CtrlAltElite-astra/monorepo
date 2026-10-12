"""T18, engine half (owner: Rahil): which carried or received events may enter the log.

The rule is region/engine/admit.py; /v1/carry and peer ingest call it. A forged
signature, an edited body or an unknown origin is rejected and changes nothing;
the same event twice is a duplicate, never a second copy.
"""
import pytest

from region.engine import admit, canonical, signing
from region.engine.model import Replica

MUM, MUM_PUB = signing.generate()
PUN, PUN_PUB = signing.generate()
PUBLIC = {"mum": MUM_PUB, "pun": PUN_PUB}


def signed_cart():
    """A cart and one item written by mum, as mum's browser holds them in its outbox."""
    mum = Replica("mum", MUM)
    cart = mum.write(1000, "CREATE", "R-0000a11c", value={"title": "Alice", "category": "cart", "severity": 1})
    item = mum.write(1001, "NOTE", "R-0000a11c", value={"text": '{"kind":"ADD","sku":"GIFT-500"}'}, op_id="op-1")
    return cart, item


@pytest.fixture(autouse=True)
def node(provision):
    """This process is node pun knowing both public keys."""
    provision({"pun": PUN}, PUBLIC)


def test_a_genuine_signed_event_is_admitted():
    cart, item = signed_cart()
    assert admit.reject_reason(cart, PUBLIC) is None and admit.reject_reason(item, PUBLIC) is None
    assert admit.reject_reason(item) is None  # the same answer from this node's own registry


def test_an_edited_body_is_bad_hash():
    _, item = signed_cart()
    assert admit.reject_reason(dict(item, value={"text": '{"kind":"ADD","sku":"EARBUD-01"}'}), PUBLIC) == "bad_hash"


def test_an_edited_and_re_hashed_body_is_bad_sig():
    _, item = signed_cart()
    edited = dict(item, value={"text": '{"kind":"ADD","sku":"EARBUD-01"}'})
    edited["hash"] = canonical.event_hash(edited)  # the hash fits again, the origin's signature does not
    assert admit.reject_reason(edited, PUBLIC) == "bad_sig"


def test_an_event_forged_for_another_node_is_bad_sig():
    _, item = signed_cart()
    forged = dict(item, sig=signing.sign_with(PUN, item["hash"]))  # pun signs an event that claims to be mum's
    assert admit.reject_reason(forged, PUBLIC) == "bad_sig"
    assert admit.reject_reason(dict(item, sig=None), PUBLIC) == "bad_sig"  # carry never accepts unsigned
    assert admit.reject_reason(dict(item, sig="not base64!"), PUBLIC) == "bad_sig"


def test_an_unknown_origin_is_rejected_before_anything_else():
    _, item = signed_cart()
    stranger = dict(item, origin="xyz", event_id="xyz:2")
    assert admit.reject_reason(stranger, PUBLIC) == "unknown_origin"
    assert admit.reject_reason(stranger) == "unknown_origin"


@pytest.mark.parametrize("breakage", [
    lambda e: {k: v for k, v in e.items() if k != "hash"},
    lambda e: {k: v for k, v in e.items() if k != "supersedes"},
    lambda e: dict(e, event_id="mum:99"),            # id does not match origin and seq
    lambda e: dict(e, seq="2"),
    lambda e: dict(e, hlc=[1, 2, 3]),
    lambda e: dict(e, hash=None),
    lambda e: "mum:2",
    lambda e: None,
])
def test_a_malformed_envelope_is_shape(breakage):
    _, item = signed_cart()
    assert admit.reject_reason(breakage(item), PUBLIC) == "shape"


def test_unsigned_ok_lets_old_peers_through_on_shape_and_hash_only():
    _, item = signed_cart()
    unsigned = dict(item, sig=None)
    assert admit.reject_reason(unsigned, PUBLIC, unsigned_ok=True) is None
    stranger = dict(unsigned, origin="xyz", event_id="xyz:2")
    stranger["hash"] = canonical.event_hash(stranger)
    assert admit.reject_reason(stranger, PUBLIC, unsigned_ok=True) is None  # no key needed when nothing is signed
    assert admit.reject_reason(dict(unsigned, actor="x"), PUBLIC, unsigned_ok=True) == "bad_hash"
    forged = dict(item, sig=signing.sign_with(PUN, item["hash"]))
    assert admit.reject_reason(forged, PUBLIC, unsigned_ok=True) == "bad_sig"  # a signature present is checked


def test_rejected_events_change_nothing_and_a_duplicate_is_one_event():
    cart, item = signed_cart()
    pun = Replica("pun", PUN)
    assert pun.receive([cart, item], 2000, PUBLIC) == [cart["event_id"], item["event_id"]]
    before = (pun.event_digest(), pun.state_hash())
    edited = dict(item, value={"text": "x"})
    rehashed = dict(edited, hash=canonical.event_hash(edited))
    bad = [edited, rehashed, dict(item, sig=signing.sign_with(PUN, item["hash"])),
           dict(item, origin="xyz", event_id="xyz:2"), {"event_id": "mum:3"}]
    assert pun.receive(bad, 2001, PUBLIC) == []
    assert pun.receive([item, cart, item], 2002, PUBLIC) == []  # duplicates: nothing new
    assert (pun.event_digest(), pun.state_hash()) == before
    assert len(pun.view("R-0000a11c")["state"]["notes"]) == 1


def test_every_reason_is_one_the_contract_names():
    assert admit.REASONS == ("shape", "unknown_origin", "bad_hash", "bad_sig")  # contract 5.3, /v1/carry results
