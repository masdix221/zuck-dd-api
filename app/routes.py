"""Zuck DD API routes.

POST /dd/quick     — contract risk screen  (paid, $0.50 — mirrors the quick-screen tier)
POST /dd/approvals — wallet approval audit (paid, $0.25 — DD add-on)

A route is *paid* purely by being listed in app.pricing.ROUTE_PRICING.
Handlers contain no payment logic — by the time they run, the x402
middleware has already verified payment.

Reads are Base MAINNET (product data is mainnet); payment settlement is
Base Sepolia on testnet / Base mainnet in production.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.dd.approvals import approvals_audit
from app.dd.quick import quick_screen

router = APIRouter()


class QuickRequest(BaseModel):
    contract: str = Field(..., description="ERC-20 contract address on Base")


class ApprovalsRequest(BaseModel):
    wallet: str = Field(..., description="Wallet address to audit on Base")


@router.get("/v1/ping")
def ping() -> dict[str, str]:
    """Free health-check route (not in ROUTE_PRICING, so no payment required)."""
    return {"status": "ok", "service": "zuck-dd-api"}


@router.post("/dd/quick")
def dd_quick(req: QuickRequest) -> dict:
    """Paid: quick contract risk screen."""
    return quick_screen(req.contract)


@router.post("/dd/approvals")
def dd_approvals(req: ApprovalsRequest) -> dict:
    """Paid: wallet token-approval audit."""
    return approvals_audit(req.wallet)
