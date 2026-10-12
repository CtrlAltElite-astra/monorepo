"""Who may do what (owner: Akshit). Pure data, no I/O: PDF 2 section 5 role table.

officer     create a report, set fields, add notes (in the region whose API key table lists them)
supervisor  everything an officer can, plus resolve review items
admin       the /admin surface only (links, config, burst, seed); not a report writer
"""
ROLES = ("officer", "supervisor", "admin")

PERMISSIONS = {
    "create": ("officer", "supervisor"),
    "set": ("officer", "supervisor"),
    "note": ("officer", "supervisor"),
    "resolve": ("supervisor",),  # POST /v1/reports/{id}/resolve (PDF 2 Phase 2)
    "admin": ("admin",),         # /admin/* when a route adopts require("admin")
}


def may(role, action: str) -> bool:
    """True when `role` is allowed to perform `action`. Unknown roles and actions are denied."""
    return role in PERMISSIONS.get(action, ())
