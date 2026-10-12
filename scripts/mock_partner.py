"""Mock Convergence Partner Server (Simrit / Lane D).

Simulates Cybersecurity / Blockchain partner service (PDF 2 Section 16.1).
Run: python scripts/mock_partner.py [--port 8000]
"""
import argparse
import time
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock Convergence Partner", version="1")
PARTNER_KEY = "partner-secret-key"


def verify_partner_key(x_api_key: str = Header(None)):
    if x_api_key != PARTNER_KEY:
        raise HTTPException(status_code=401, detail="Invalid partner key")


class VerifyRequest(BaseModel):
    presentation: str


class AnchorRequest(BaseModel):
    region: str
    seq_from: int
    seq_to: int
    merkle_root: str
    sig: str | None = None


@app.get("/trust/keys")
def get_trust_keys(_=Header(None, alias="X-Api-Key")):
    return {
        "keys": [
            {
                "kid": "partner-root-key",
                "alg": "Ed25519",
                "public_key_b64": "MCowBQYDK2VwAyEA...MOCK_PUBLIC_KEY...",
                "issuer": "codeastra-partner-ca",
            }
        ]
    }


@app.post("/credentials/verify")
def verify_credential(body: VerifyRequest, _=Header(None, alias="X-Api-Key")):
    # Reject forged tokens for testing
    if "forged" in body.presentation.lower() or "revoked" in body.presentation.lower():
        return {
            "valid": False,
            "subject": "unknown",
            "role": "officer",
            "region_scope": "all",
            "credential_id": "cred-invalid",
            "exp": int(time.time()) - 100,
        }
    return {
        "valid": True,
        "subject": "officer-authorized",
        "role": "supervisor" if "supervisor" in body.presentation.lower() else "officer",
        "region_scope": "all",
        "credential_id": "cred-8899",
        "exp": int(time.time()) + 3600,
    }


@app.get("/credentials/status-list")
def get_status_list(_=Header(None, alias="X-Api-Key")):
    return {
        "version": 1,
        "issued_at": int(time.time()),
        "revoked": ["cred-revoked-001", "cred-bad-actor"],
    }


@app.post("/anchor")
def anchor_batch(body: AnchorRequest, _=Header(None, alias="X-Api-Key")):
    return {
        "anchor_id": f"anchor-{body.region}-{body.seq_from}-{body.seq_to}",
        "tx_ref": f"0xmocktx{int(time.time())}",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"Starting Mock Partner Server on port {args.port} (key={PARTNER_KEY})...")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
