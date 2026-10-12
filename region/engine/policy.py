"""Conflict policy v1 (owner: Rahil). Pure: no I/O, no randomness, no wall clock.

When the live writes of one field disagree, resolve() picks the value every node
shows until someone decides, and says whether a supervisor must look at it. The
table with the reasons is in docs/conflict-policy.md. Any rule change bumps
POLICY_VERSION and its unit test in the same PR.
"""
from dataclasses import dataclass

from region.engine import canonical

POLICY_VERSION = 1

STATUS_RANK = {"open": 0, "assigned": 1, "resolved": 2, "closed": 3, "cancelled": 3}  # lower = more active
CLOSE_STATUSES = ("closed", "cancelled")
CRITICAL_SEVERITY = 4
METRES_PER_DEGREE = 111_195  # along a meridian, mean Earth radius


def is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_point(v) -> bool:
    return (isinstance(v, dict) and set(v) == {"lat_e6", "lon_e6"}
            and all(isinstance(x, int) and not isinstance(x, bool) for x in v.values()))


def _cos_e6(lat_e6: int) -> int:
    """cos(latitude) * 1e6 from whole-number arithmetic only, so every node gets the same digits."""
    x = min(abs(lat_e6), 90_000_000) * 3141592653589793 // (180 * 10**15)  # micro-radians
    x2 = x * x
    return max(0, 10**6 - x2 // (2 * 10**6) + x2**2 // (24 * 10**18) - x2**3 // (720 * 10**30)
               + x2**4 // (40320 * 10**42))


def metres_apart_at_least(a: dict, b: dict, limit_m: int) -> bool:
    """True when two {lat_e6, lon_e6} points are limit_m or more apart (flat map, fine for a few km)."""
    dlat = a["lat_e6"] - b["lat_e6"]
    dlon = abs(a["lon_e6"] - b["lon_e6"]) % 360_000_000
    dlon = min(dlon, 360_000_000 - dlon) * _cos_e6((a["lat_e6"] + b["lat_e6"]) // 2) // 10**6
    return (dlat * dlat + dlon * dlon) * METRES_PER_DEGREE**2 >= (limit_m * 10**6) ** 2


def _highest(values: list):
    numbers = [v for v in values if is_number(v)]
    return max(numbers) if numbers else values[-1]


@dataclass(frozen=True)
class Policy:
    """The rule set. Every node runs POLICY_V1; the fields record the team decisions D1-D3."""
    version: int = POLICY_VERSION
    status_rule: str = "most_active"  # D1: "most_active" keeps the cart live; the other option is "furthest"
    severity_review: bool = False     # D2: the highest severity wins without a review
    location_review_m: int = 200      # D3: review when the candidates are this far apart

    def resolve(self, field: str, cands: list) -> tuple:
        """cands: [(event, value)] for the live writes of one field, oldest first, with 2+ distinct values.

        Returns (value shown until resolved, rule id, needs_review, flags).
        """
        values = [v for _, v in cands]
        flags = []
        if field == "severity":
            value, rule, review = _highest(values), "SEVERITY_HIGHEST", self.severity_review
        elif field == "people_estimate":
            value, rule, review = _highest(values), "PEOPLE_HIGHEST", True
        elif field == "status":
            value, rule, review = self._status(values), "STATUS_" + self.status_rule.upper(), True
        elif field == "assigned_team":
            value, rule, review = values[0], "TEAM_EARLIEST", True
            if len({v for v in values if isinstance(v, str)}) > 1:
                flags.append("DOUBLE_DISPATCH")
        elif field == "location":
            value, rule = values[-1], "LOCATION_LATEST"
            flags += self._location_flags(values)
            review = bool(flags)
        else:
            value, rule, review = values[-1], "LATEST", False
        decided = {canonical.dumps(v) for e, v in cands if e["type"] == "RESOLVE"}
        if len(decided) > 1:  # two supervisors decided differently: neither may silently win
            flags.append("RESOLVE_VS_RESOLVE")
            review = True
        return value, rule, review, flags

    def _status(self, values: list):
        known = [v for v in values if isinstance(v, str) and v in STATUS_RANK]
        if not known:
            return values[-1]
        best = (min if self.status_rule == "most_active" else max)(STATUS_RANK[v] for v in known)
        return [v for v in known if STATUS_RANK[v] == best][-1]  # a tie (closed vs cancelled): the latest

    def _location_flags(self, values: list) -> list:
        points = [v for v in values if is_point(v)]
        flags = ["LOCATION_MISSING"] if len(points) < len(values) else []  # no pin: distance cannot be measured
        if any(metres_apart_at_least(a, b, self.location_review_m)
               for i, a in enumerate(points) for b in points[i + 1:]):
            flags.append("FAR_APART")
        return flags


POLICY_V1 = Policy()
