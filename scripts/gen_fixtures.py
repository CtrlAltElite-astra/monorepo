"""Regenerate contracts/fixtures/*.json from the real engine (owner: Akshit)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from region.engine import canonical
from region.engine.derive import derive

def mk(origin, seq, type_, field=None, value=None, sup=(), hlc=(0, 0), report="R-mum-1", actor=None, wall="2026-10-12T14:52:10+05:30"):
    ev = dict(v=1, event_id=f"{origin}:{seq}", origin=origin, seq=seq, report_id=report, type=type_, field=field,
              value=value, supersedes=list(sup), hlc=list(hlc), priority=1,
              actor=actor or f"officer-{origin}-1", reason=None, wall=wall, sig=None)
    ev["hash"] = canonical.event_hash(ev)
    return ev

create = mk("mum", 1, "CREATE", value={"title": "Wall collapse, Kurla", "category": "collapse", "severity": 3,
            "status": "open", "assigned_team": None, "location": None, "description": "", "people_estimate": None},
            hlc=(1760260330000, 0))
m_sev = mk("mum", 2, "SET", "severity", 5, ["mum:1"], (1760260400000, 0))
p_sev = mk("pun", 1, "SET", "severity", 4, ["mum:1"], (1760260401000, 0))
p_note = mk("pun", 2, "NOTE", value={"text": "two people still trapped"}, hlc=(1760260402000, 0))
events = [create, m_sev, p_sev, p_note]
out = ROOT / "contracts" / "fixtures"
out.mkdir(parents=True, exist_ok=True)
(out / "split_brain_events.json").write_text(json.dumps(events, indent=2), encoding="utf-8")

v = derive(events)
report = {"report_id": "R-mum-1", "state": v["state"], "live": v["live"], "conflicts": v["conflicts"],
          "incomplete": v["incomplete"], "consistency": {"replication": "LOCAL_ONLY"}}
(out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
status = {"region": "mum", "schema_version": 1, "policy_version": 0, "heads": {"mum": 2, "pun": 0},
          "event_count": 2, "event_digest": "sha256-hex", "state_hash": "sha256-hex",
          "peers": {"pun": {"link": "down", "reachable": False, "last_ok_s": 47.2, "their_heads": {"pun": 2},
                            "last_error": "link_down", "fails": 5}},
          "level": "ISOLATED", "quarantined": 0, "incomplete_reports": 0,
          # nullable in the live API until Lane B (risk) / Lane D (queues) fill them; sample values for the UI
          "risk": {"unreplicated": 3, "critical_unreplicated": 1, "oldest_age_ms": 47200},
          "queues": {"P0": 0, "P1": 2, "P2": 1}}
(out / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
print("fixtures written")
