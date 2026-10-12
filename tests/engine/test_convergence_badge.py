"""U2 global badge (owner: Rahil): CONVERGED only with equal digests, never greener than the facts."""
from region.engine.convergence import badge
from region.engine.model import Replica


def status(node: Replica, version=1) -> dict:
    return {"heads": node.heads(), "event_digest": node.event_digest(), "state_hash": node.state_hash(),
            "policy_version": version}


def two_nodes():
    mum, pun = Replica("mum"), Replica("pun")
    mum.write(1000, "CREATE", "R-1", value={"title": "Esha", "category": "cart", "severity": 1})
    mum.write(1001, "NOTE", "R-1", value={"text": "x"})
    return mum, pun


def test_equal_events_and_state_on_every_node_is_converged():
    mum, pun = two_nodes()
    pun.receive(list(mum.events.values()), 2000)
    assert badge({"mum": status(mum), "pun": status(pun)})[0] == "CONVERGED"


def test_one_silent_node_is_unknown_never_green():
    mum, pun = two_nodes()
    pun.receive(list(mum.events.values()), 2000)
    assert badge({"mum": status(mum), "pun": status(pun), "del": None}) == ("UNKNOWN", "no answer from del")
    assert badge({})[0] == "UNKNOWN"
    assert badge({"mum": status(mum), "pun": dict(status(pun), state_hash=None)})[0] == "UNKNOWN"


def test_different_policy_versions_are_incompatible_even_with_equal_digests():
    mum, pun = two_nodes()
    pun.receive(list(mum.events.values()), 2000)
    assert badge({"mum": status(mum), "pun": status(pun, version=2)})[0] == "INCOMPATIBLE"


def test_replication_in_progress_is_syncing_and_says_who_misses_what():
    mum, pun = two_nodes()
    pun.receive([mum.events["mum:1"]], 2000)
    got, why = badge({"mum": status(mum), "pun": status(pun)})
    assert got == "SYNCING" and why.endswith("behind: pun misses mum:2")


def test_equal_heads_with_an_event_beyond_a_gap_is_syncing_not_a_bug():
    mum, pun = two_nodes()
    mum.write(1002, "NOTE", "R-1", value={"text": "y"})
    pun.receive([mum.events["mum:1"], mum.events["mum:3"]], 2000)  # mum:2 still missing on pun
    other = Replica("del")
    other.receive([mum.events["mum:1"]], 2000)
    assert pun.heads() == other.heads() == {"mum": 1}
    assert badge({"pun": status(pun), "del": status(other)})[0] == "SYNCING"


def test_same_events_but_different_state_is_the_bug_alarm():
    mum, pun = two_nodes()
    pun.receive(list(mum.events.values()), 2000)
    broken = dict(status(pun), state_hash="0" * 64)  # what a non-deterministic derive() would report
    assert badge({"mum": status(mum), "pun": broken})[0] == "DIVERGED"
