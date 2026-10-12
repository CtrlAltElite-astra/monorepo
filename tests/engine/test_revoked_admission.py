"""An event from a revoked credential is refused on arrival (owner: Rahil). Scaling plan, lane B task 3.

A region that had not yet heard of a revocation may still have signed a write from that credential.
Every node that knows the credential is revoked refuses the event with reason revoked_credential,
after the signature check. The lookup is passed in; by default nothing is revoked.
"""
from region.engine import admit, canonical, signing
from region.engine.model import Replica

MUM, MUM_PUB = signing.generate()
PUN, PUN_PUB = signing.generate()
PUBLIC = {"mum": MUM_PUB, "pun": PUN_PUB}
CART = "R-0000c0de"
REVOKED, LIVE = "cred-revoked-1", "cred-live-1"
revoked = {REVOKED}.__contains__


def mumbai_with_a_cart():
    """Mumbai writes a cart and replicates it to Pune; Pune then knows the cart."""
    mum, pun = Replica("mum", MUM), Replica("pun", PUN)
    created = mum.write(1000, "CREATE", CART, value={"title": "Dev", "category": "cart", "severity": 1})
    pun.receive([created], 1001, PUBLIC)
    return mum, pun


def item_from(mum, credential_id, now=2000):
    """Mumbai, not yet aware of any revocation, accepts and signs an add-to-cart from this credential."""
    return mum.write(now, "NOTE", CART, value={"text": '{"kind":"ADD","sku":"MAT-01"}'}, credential_id=credential_id)


def test_a_validly_signed_event_from_a_revoked_credential_is_refused_and_state_does_not_move():
    mum, pun = mumbai_with_a_cart()
    ev = item_from(mum, REVOKED)
    assert admit.reject_reason(ev, PUBLIC) is None                       # hash and signature are fine
    assert admit.reject_reason(ev, PUBLIC, is_revoked=revoked) == "revoked_credential"
    before = (pun.event_digest(), pun.state_hash())
    assert pun.receive([ev], 2001, PUBLIC, is_revoked=revoked) == []
    assert (pun.event_digest(), pun.state_hash()) == before
    assert pun.view(CART)["state"]["notes"] == []


def test_the_same_event_from_a_live_credential_is_accepted():
    mum, pun = mumbai_with_a_cart()
    ev = item_from(mum, LIVE)
    assert admit.reject_reason(ev, PUBLIC, is_revoked=revoked) is None
    assert pun.receive([ev], 2001, PUBLIC, is_revoked=revoked) == [ev["event_id"]]
    assert len(pun.view(CART)["state"]["notes"]) == 1


def test_an_event_without_a_credential_is_unaffected():
    mum, pun = mumbai_with_a_cart()
    ev = item_from(mum, None)
    assert "credential_id" not in ev
    assert pun.receive([ev], 2001, PUBLIC, is_revoked=lambda cid: True) == [ev["event_id"]]  # nothing to look up


def test_with_the_default_lookup_nothing_changes():
    mum, pun = mumbai_with_a_cart()
    ev = item_from(mum, REVOKED)
    assert admit.never_revoked(REVOKED) is False
    assert admit.reject_reason(ev, PUBLIC) is None  # no lookup given: the credential is not checked
    assert pun.receive([ev], 2001, PUBLIC) == [ev["event_id"]]


def test_the_older_reasons_still_come_first():
    """Revocation is asked only of an event that passed every other check."""
    mum, _ = mumbai_with_a_cart()
    ev = item_from(mum, REVOKED)
    asked = []

    def lookup(cid):
        asked.append(cid)
        return True

    edited = dict(ev, value={"text": "tampered"})
    rehashed = dict(edited, hash=canonical.event_hash(edited))
    stranger = dict(ev, origin="xyz", event_id="xyz:2")
    assert admit.reject_reason(edited, PUBLIC, is_revoked=lookup) == "bad_hash"
    assert admit.reject_reason(rehashed, PUBLIC, is_revoked=lookup) == "bad_sig"
    assert admit.reject_reason(stranger, PUBLIC, is_revoked=lookup) == "unknown_origin"
    assert admit.reject_reason({"event_id": "mum:9"}, PUBLIC, is_revoked=lookup) == "shape"
    assert asked == []  # never consulted for an event that failed earlier
    assert admit.reject_reason(ev, PUBLIC, is_revoked=lookup) == "revoked_credential" and asked == [REVOKED]


def test_the_credential_is_bound_by_the_signature():
    """A carrier cannot strip or swap the credential to slip past the rule: the hash covers it."""
    mum, _ = mumbai_with_a_cart()
    ev = item_from(mum, REVOKED)
    stripped = {k: v for k, v in ev.items() if k != "credential_id"}
    swapped = dict(ev, credential_id=LIVE)
    assert admit.reject_reason(stripped, PUBLIC, is_revoked=revoked) == "bad_hash"
    assert admit.reject_reason(swapped, PUBLIC, is_revoked=revoked) == "bad_hash"


def test_an_unsigned_peer_event_from_a_revoked_credential_is_refused_too():
    mum, _ = mumbai_with_a_cart()
    ev = dict(item_from(mum, REVOKED), sig=None)
    assert admit.reject_reason(ev, PUBLIC, unsigned_ok=True) is None
    assert admit.reject_reason(ev, PUBLIC, unsigned_ok=True, is_revoked=revoked) == "revoked_credential"
