"""Engine tests (owner: Rahil). Pure, no I/O."""
import itertools
import json
import random

from region.engine import canonical, hlc
from region.engine.derive import derive
from region.engine.digest import event_digest, state_hash


def mk(origin, seq, type_, field=None, value=None, supersedes=(), hlc_=None, report="R-mum-1"):
    ev = dict(v=1, event_id=f"{origin}:{seq}", origin=origin, seq=seq, report_id=report, type=type_,
              field=field, value=value, supersedes=list(supersedes), hlc=list(hlc_ or (1000 + seq, 0)),
              priority=1, actor="t", reason=None, wall="x", sig=None)
    ev["hash"] = canonical.event_hash(ev)
    return ev


CREATE = mk("mum", 1, "CREATE", value={"title": "Wall", "severity": 3, "status": "open"}, hlc_=(1000, 0))


# ---- canonical / hash ----------------------------------------------------
def test_canonical_is_key_order_independent():
    assert canonical.dumps({"b": 1, "a": 2}) == canonical.dumps({"a": 2, "b": 1}) == '{"a":2,"b":1}'


def test_hash_stable_and_ignores_hash_and_sig():
    ev = dict(CREATE)
    h = canonical.event_hash(ev)
    ev["hash"], ev["sig"] = "zzz", "sig"
    assert canonical.event_hash(ev) == h == CREATE["hash"]


def test_hash_changes_with_content():
    ev = dict(CREATE, value={"title": "Other"})
    assert canonical.event_hash(ev) != CREATE["hash"]


# The 4.1 example envelope from the build plan, and a non-ASCII note. If these hashes
# ever change, regions running different code quarantine each other's events (bad_hash).
PLAN_EVENT = dict(v=1, event_id="mum:17", origin="mum", seq=17, report_id="R-mum-3", type="SET",
                  field="severity", value=4, supersedes=["mum:3"], hlc=[1760260330123, 0], priority=1,
                  actor="officer-mum-1", reason=None, wall="2026-10-12T14:52:10+05:30", sig=None)
HINDI_NOTE = dict(PLAN_EVENT, event_id="pun:9", origin="pun", seq=9, type="NOTE", field=None,
                  value={"text": "दो लोग फंसे हैं"}, supersedes=[], actor="officer-pun-1")


def test_canonical_pins_separators_nesting_and_utf8():
    assert canonical.dumps({"b": [1, {"d": None, "c": "é"}], "a": True}) == '{"a":true,"b":[1,{"c":"é","d":null}]}'


def test_hash_is_stable_across_runs():
    # same values on Python 3.10 and 3.14 with different PYTHONHASHSEED
    assert canonical.event_hash(PLAN_EVENT) == "d421e4807cc7d2acb16ef6fee6a63b07c8a7b6deff581ca148a74a7413bb8b30"
    assert canonical.event_hash(HINDI_NOTE) == "25edc70839299788a026eb9fbaf8161f7c00a045797991e3bb21b8f6fae4c178"


def test_hash_matches_between_regions_after_transport():
    sent = dict(HINDI_NOTE)
    sent["hash"] = canonical.event_hash(sent)
    wire_forms = [
        canonical.dumps(sent),                                        # events.body in SQLite
        json.dumps(sent, ensure_ascii=False, separators=(",", ":")),  # FastAPI JSONResponse
        json.dumps(dict(reversed(list(sent.items()))), indent=2),     # escaped, pretty, other key order
    ]
    for wire in wire_forms:
        got = json.loads(wire.encode("utf-8"))
        assert got == sent and canonical.event_hash(got) == sent["hash"]
    assert canonical.event_hash(dict(sent, hlc=tuple(sent["hlc"]))) == sent["hash"]  # hlc.tick returns a tuple


# ---- HLC -----------------------------------------------------------------
def test_hlc_never_goes_backwards_when_wall_clock_jumps_back():
    s = hlc.tick((0, 0), 1_000_000)
    s2 = hlc.tick(s, 1_000_000 - 10_000)
    assert s2 > s
    assert hlc.tick(s2, 1_000_000 - 10_000) > s2


def test_hlc_stays_monotonic_through_clock_jump_receive_and_catch_up():
    t = 1_000_000
    s = [hlc.tick((0, 0), t)]
    s.append(hlc.tick(s[-1], t - 10_000))                    # wall clock jumps back 10 s
    s.append(hlc.tick(s[-1], t - 9_999))
    s.append(hlc.receive(s[-1], (t - 5_000, 4), t - 9_998))  # peer stamp that is also behind us
    s.append(hlc.receive(s[-1], (t, 9), t - 9_997))          # peer stamp level with us
    s.append(hlc.tick(s[-1], t + 1))                         # wall clock passes us again
    assert all(a < b for a, b in zip(s, s[1:]))
    assert s[-2] == (t, 10) and s[-1] == (t + 1, 0)          # back on physical time, counter reset


def test_hlc_receive_is_after_both():
    local, remote = (100, 5), (500, 2)
    r = hlc.receive(local, remote, 50)
    assert r > local and r > remote


def test_hlc_suspect_clock():
    assert hlc.is_suspect((200_000, 0), 0) and not hlc.is_suspect((30_000, 0), 0)


# ---- derive --------------------------------------------------------------
def test_sequential_edit_is_not_a_conflict():
    a = mk("mum", 2, "SET", "severity", 4, supersedes=["mum:1"])
    b = mk("mum", 3, "SET", "severity", 5, supersedes=["mum:2"])
    v = derive([CREATE, a, b])
    assert v["state"]["severity"] == 5 and v["conflicts"] == [] and v["live"]["severity"] == ["mum:3"]


def test_concurrent_edit_is_a_conflict_and_deterministic():
    m = mk("mum", 2, "SET", "severity", 5, supersedes=["mum:1"], hlc_=(2000, 0))
    p = mk("pun", 1, "SET", "severity", 4, supersedes=["mum:1"], hlc_=(2001, 0))
    v = derive([CREATE, m, p])
    assert [c["field"] for c in v["conflicts"]] == ["severity"]
    assert sorted(v["conflicts"][0]["values"]) == [4, 5]
    assert v["state"]["severity"] == 5  # policy v1: the highest wins, although 4 was written later


def test_disjoint_fields_do_not_conflict():
    a = mk("mum", 2, "SET", "status", "closed", supersedes=["mum:1"])
    b = mk("pun", 1, "SET", "severity", 5, supersedes=["mum:1"])
    v = derive([CREATE, a, b])
    assert v["conflicts"] == [] and v["state"]["status"] == "closed" and v["state"]["severity"] == 5


def test_supersedes_only_replaces_the_written_field():
    s = mk("mum", 2, "SET", "severity", 5, supersedes=["mum:1"])
    v = derive([CREATE, s])
    assert v["live"]["severity"] == ["mum:2"] and v["live"]["title"] == ["mum:1"]
    assert v["state"]["title"] == "Wall" and v["state"]["status"] == "open"


def test_write_that_saw_both_values_clears_the_conflict():
    m = mk("mum", 2, "SET", "severity", 5, supersedes=["mum:1"], hlc_=(2000, 0))
    p = mk("pun", 1, "SET", "severity", 4, supersedes=["mum:1"], hlc_=(2001, 0))
    fix = mk("mum", 3, "SET", "severity", 5, supersedes=["mum:2", "pun:1"], hlc_=(3000, 0))
    v = derive([CREATE, m, p, fix])
    assert v["conflicts"] == [] and v["live"]["severity"] == ["mum:3"] and v["state"]["severity"] == 5


def test_write_that_saw_only_one_value_keeps_the_conflict():
    m = mk("mum", 2, "SET", "severity", 5, supersedes=["mum:1"], hlc_=(2000, 0))
    p = mk("pun", 1, "SET", "severity", 4, supersedes=["mum:1"], hlc_=(2001, 0))
    late = mk("mum", 3, "SET", "severity", 2, supersedes=["mum:2"], hlc_=(3000, 0))  # still cut off from pun
    v = derive([CREATE, m, p, late])
    assert v["live"]["severity"] == ["pun:1", "mum:3"]
    assert v["conflicts"][0]["values"] == [4, 2] and v["state"]["severity"] == 4


def test_equal_hlc_breaks_ties_by_origin_then_seq():
    m = mk("mum", 2, "SET", "title", "from mum", supersedes=["mum:1"], hlc_=(2000, 0))
    p = mk("pun", 1, "SET", "title", "from pun", supersedes=["mum:1"], hlc_=(2000, 0))
    for evs in ([CREATE, m, p], [p, m, CREATE]):
        v = derive(evs)
        assert v["live"]["title"] == ["mum:2", "pun:1"] and v["state"]["title"] == "from pun"  # title: latest wins


def test_equal_concurrent_values_are_not_a_conflict():
    a = mk("mum", 2, "SET", "severity", 4, supersedes=["mum:1"])
    b = mk("pun", 1, "SET", "severity", 4, supersedes=["mum:1"])
    assert derive([CREATE, a, b])["conflicts"] == []


def test_notes_union_and_incomplete_flag():
    n = mk("pun", 1, "NOTE", value={"text": "two trapped"})
    assert derive([n])["incomplete"] is True
    v = derive([CREATE, n])
    assert v["incomplete"] is False and v["state"]["notes"] == [{"id": "pun:1", "text": "two trapped"}]


def test_permutations_and_duplicates_give_identical_view():
    m = mk("mum", 2, "SET", "severity", 5, supersedes=["mum:1"], hlc_=(2000, 0))
    p = mk("pun", 1, "SET", "severity", 4, supersedes=["mum:1"], hlc_=(2001, 0))
    n = mk("pun", 2, "NOTE", value={"text": "x"}, hlc_=(2002, 0))
    s = mk("mum", 3, "SET", "status", "assigned", supersedes=["mum:1"], hlc_=(2003, 0))
    base = [CREATE, m, p, n, s]
    want = canonical.dumps(derive(base))
    for perm in itertools.islice(itertools.permutations(base), 120):
        assert canonical.dumps(derive(list(perm))) == want
    rnd = random.Random(7)
    for _ in range(20):
        dup = base + rnd.choices(base, k=6)
        rnd.shuffle(dup)
        assert canonical.dumps(derive(dup)) == want


# ---- digests -------------------------------------------------------------
def test_digests_are_order_independent():
    pairs = [("mum:1", "a"), ("pun:1", "b"), ("mum:2", "c")]
    assert event_digest(pairs) == event_digest(reversed(pairs))
    v1 = {"R-1": derive([CREATE]), "R-2": derive([mk("mum", 9, "CREATE", value={"title": "b"})])}
    assert state_hash(v1) == state_hash(dict(reversed(list(v1.items()))))


def test_two_regions_converge_after_partition():
    """M1b in pure form: both sides write while cut off, then each pulls what it lacks."""
    m = mk("mum", 2, "SET", "severity", 5, supersedes=["mum:1"], hlc_=(2000, 0))
    p = mk("pun", 1, "SET", "severity", 4, supersedes=["mum:1"], hlc_=(2001, 0))
    n = mk("pun", 2, "NOTE", value={"text": "two trapped"}, hlc_=(2002, 0))
    mum, pun = [CREATE, m], [CREATE, p, n]
    assert derive(mum)["state"]["severity"] == 5 and derive(pun)["state"]["severity"] == 4  # diverged
    mum, pun = mum + pun, pun + [m]  # heal; the re-sent CREATE is a harmless duplicate
    vm, vp = derive(mum), derive(pun)
    assert canonical.dumps(vm) == canonical.dumps(vp) and len(vm["conflicts"]) == 1
    assert state_hash({"R-mum-1": vm}) == state_hash({"R-mum-1": vp})
    assert event_digest({(e["event_id"], e["hash"]) for e in mum}) == \
        event_digest({(e["event_id"], e["hash"]) for e in pun})
