"""Trust adapter interface (owner: Akshit). One implementation per partner problem statement.

The write path asks the adapter WHO is acting and whether they may do it. Rules every
implementation must obey (PDF 2 section 16):

- A partner outage never blocks a local write: it lowers `mode` (online -> cached -> stale),
  it does not raise. Unknown issuer or bad signature fails closed (`allowed=False`).
- Partner calls use connect 0.5 s, read 1.5 s, at most one retry.
- Methods that this adapter does not support return a neutral result, never an exception.

Phase 1 ships the interface and `LocalKeyAdapter` (API keys from config). The HTTP partner
adapter arrives in Phase 2 behind the same interface; the contract is contracts/partner-contract.md.
"""
from dataclasses import dataclass
from typing import Literal, Optional, Union

from region import config
from region.trust import roles

Mode = Literal["online", "cached", "stale"]

# Machine-readable denial reasons (auth.py maps them to HTTP 401 / 403).
UNKNOWN_CREDENTIAL = "unknown_credential"
FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    actor: Optional[str] = None
    role: Optional[str] = None
    region_scope: Optional[str] = None
    credential_id: Optional[str] = None
    mode: Mode = "online"                 # how fresh the trust data behind this decision is
    status_ver: Optional[Union[int, str]] = None  # version of the revocation/status list used, if any
    reason: str = ""                      # "" when allowed, else UNKNOWN_CREDENTIAL / FORBIDDEN / ...


@dataclass(frozen=True)
class EvidenceVerdict:                    # partner PS 2 (anti-counterfeit)
    verdict: Literal["verified", "counterfeit", "unverified"] = "unverified"
    lot_id: Optional[str] = None
    reason: str = "not supported by this adapter"


@dataclass(frozen=True)
class AnchorReceipt:                      # optional ledger anchoring of event-batch Merkle roots
    anchored: bool = False
    anchor_id: Optional[str] = None
    tx_ref: Optional[str] = None
    reason: str = "not supported by this adapter"


@dataclass(frozen=True)
class TrustVerdict:                       # partner PS 3 (dependency trust)
    risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    reason: str = "not supported by this adapter"


class TrustAdapter:
    """Interface. Subclass and override what the partner supports."""

    method = "api_key"  # value of event.auth.method for decisions made here

    def verify_actor(self, presentation, action: Optional[str] = None) -> AuthDecision:
        """`presentation`: an API key (or, from Phase 2, a verifiable-credential presentation).
        `action`: a key of roles.PERMISSIONS, or None to authenticate without an action check."""
        raise NotImplementedError

    def check_evidence(self, lot_id, scan) -> EvidenceVerdict:
        return EvidenceVerdict(lot_id=lot_id)

    def anchor(self, region, seq_from, seq_to, merkle_root) -> AnchorReceipt:
        return AnchorReceipt()

    def peer_trust(self, region, manifest) -> TrustVerdict:
        return TrustVerdict()


class LocalKeyAdapter(TrustAdapter):
    """Default adapter: the API-key table from config. No partner involved, so always 'online'."""

    def verify_actor(self, presentation, action: Optional[str] = None) -> AuthDecision:
        entry = config.settings.api_keys.get(presentation or "")
        if entry is None:
            return AuthDecision(False, reason=UNKNOWN_CREDENTIAL)
        actor, role = entry
        base = dict(actor=actor, role=role, region_scope=config.settings.region, credential_id=actor)
        if action is not None and not roles.may(role, action):
            return AuthDecision(False, reason=FORBIDDEN, **base)
        return AuthDecision(True, **base)


_adapter: TrustAdapter = LocalKeyAdapter()


def get_adapter() -> TrustAdapter:
    return _adapter


def set_adapter(adapter: TrustAdapter) -> None:
    """Swap the active adapter (Phase 2 partner adapter, or a test double)."""
    global _adapter
    _adapter = adapter
