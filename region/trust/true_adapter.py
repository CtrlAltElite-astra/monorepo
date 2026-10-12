"""TRUE session adapter: plugs TRUE credential sessions into the existing TrustAdapter interface.

verify_actor(token, action) answers WHO is acting and whether they may do `action`, from local data only:
the pinned regional session keys and the cached, issuer-signed status snapshot. It never calls the issuer
and never calls the Node sidecar, so a dead node, a dead issuer or a dead sidecar does not stop an already
issued, still-valid session from working here.

auth_mode is computed from CURRENT evidence on every call (not from what the token advertised).
"""
import json
import time
from typing import Callable, Optional

from region import config
from region.trust import roles
from region.trust.adapter import AuthDecision, FORBIDDEN, TrustAdapter
from region.trust.true_session import AuthError, Session, SessionKeyRegistry, SessionVerifier
from region.trust.true_status import StatusStore, evaluate

PRIVILEGED_ACTIONS = ("resolve", "admin")


class TrueSessionAdapter(TrustAdapter):
    method = "true_session"

    def __init__(self, verifier: SessionVerifier, status: StatusStore, clock: Optional[Callable[[], float]] = None):
        self.verifier = verifier
        self.status = status
        self.clock = clock or time.time

    # -- the one decision function ------------------------------------------------------------
    def decide(self, token: Optional[str], action: Optional[str] = None) -> tuple:
        """Returns (AuthDecision, Session | None, Decision | None)."""
        try:
            s: Session = self.verifier.verify(token)
        except AuthError as e:
            return AuthDecision(False, reason=e.code), None, None
        snap = self.status.current()
        purpose = "privileged" if action in PRIVILEGED_ACTIONS else "write"
        d = evaluate(snap.statuses if snap else None, snap.issued_at if snap else None,
                     s.credential_id, int(self.clock()), purpose)
        base = dict(actor=s.actor_id, role=s.role, region_scope=config.settings.region, credential_id=s.credential_id,
                    claims=s.claims)
        if not d.allow:
            return AuthDecision(False, reason=d.reason, **base), s, d
        ver = str(snap.ver) if snap else None
        if action is not None and not roles.may(s.role, action):
            return AuthDecision(False, reason=FORBIDDEN, mode=d.mode, status_ver=ver, **base), s, d
        return AuthDecision(True, mode=d.mode, status_ver=ver, **base), s, d

    def verify_actor(self, presentation, action: Optional[str] = None) -> AuthDecision:
        return self.decide(presentation, action)[0]


def build_from_settings() -> TrueSessionAdapter:
    """Construct the adapter from the PINNED files named in settings. Fails loudly if they are missing."""
    s = config.settings
    if not (s.auth_issuer_file and s.auth_session_key_dir and s.auth_status_file):
        raise RuntimeError("AUTH_ENABLED needs TRUE_ISSUER_PUBLIC_JWK_FILE, AUTH_SESSION_KEY_DIR and AUTH_STATUS_FILE")
    with open(s.auth_issuer_file, "r", encoding="utf-8") as fh:
        pinned = json.load(fh)
    status = StatusStore(s.auth_status_file, pinned["issuerId"], pinned["publicJwk"])
    return TrueSessionAdapter(SessionVerifier(SessionKeyRegistry(s.auth_session_key_dir)), status)
