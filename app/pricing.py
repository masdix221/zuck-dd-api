"""Zuck DD API pricing + discovery metadata.

Price list: any "METHOD /path" in ROUTE_PRICING is a paid route.
Discovery metadata feeds the /.well-known/x402 manifest and CDP Bazaar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Price list. Dict insertion order is route-matching precedence (x402 returns
# the first match) — most-specific paths first.
# ---------------------------------------------------------------------------
ROUTE_PRICING: dict[str, str] = {
    "POST /dd/quick": "$0.50",      # mirrors Zuck's 0.50 USDC quick-screen tier
    "POST /dd/approvals": "$0.25",  # wallet approval audit add-on
}


@dataclass(frozen=True)
class RouteMeta:
    description: str
    output_example: Any | None = None
    output_schema: dict[str, Any] | None = None
    input_example: dict[str, Any] | None = None
    input_schema: dict[str, Any] | None = None


ROUTE_METADATA: dict[str, RouteMeta] = {
    "POST /dd/quick": RouteMeta(
        description=(
            "Quick memecoin risk screen on Base: honeypot simulation, buy/sell "
            "tax, mint authority, ownership, liquidity depth, holder concentration. "
            "Contract address in, 0-100 risk score + verdict out."
        ),
        input_example={"contract": "0x91A2DAe9699f0B82540B5886b0d8759C22820bA3"},
        input_schema={
            "type": "object",
            "properties": {"contract": {"type": "string"}},
            "required": ["contract"],
        },
        output_example={
            "contract": "0x91A2DAe9699f0B82540B5886b0d8759C22820bA3",
            "token": {"name": "Musebook", "symbol": "MUSEBOOK"},
            "risk_score": 35,
            "verdict": "MEDIUM",
            "flags": [{"level": "warn", "code": "MINTABLE", "message": "..."}],
        },
        output_schema={
            "type": "object",
            "properties": {
                "contract": {"type": "string"},
                "token": {"type": "object"},
                "risk_score": {"type": "number"},
                "verdict": {"type": "string"},
                "flags": {"type": "array"},
            },
        },
    ),
    "POST /dd/approvals": RouteMeta(
        description=(
            "Wallet token-approval audit on Base: scans a wallet's ERC-20 "
            "approvals, reads current allowances on-chain, flags unlimited "
            "approvals and unknown spenders. Wallet address in, ranked "
            "approval list out. Read-only — revoking stays with the owner."
        ),
        input_example={"wallet": "0xad365b23b3ff19e7902c53d4f3ebd317deb252b1"},
        input_schema={
            "type": "object",
            "properties": {"wallet": {"type": "string"}},
            "required": ["wallet"],
        },
        output_example={
            "wallet": "0xad365b23b3ff19e7902c53d4f3ebd317deb252b1",
            "summary": {"total_active_approvals": 3, "unlimited": 1},
            "approvals": [{"spender_label": "unknown", "unlimited": True, "risk": "HIGH"}],
        },
        output_schema={
            "type": "object",
            "properties": {
                "wallet": {"type": "string"},
                "summary": {"type": "object"},
                "approvals": {"type": "array"},
            },
        },
    ),
}


def metadata_for(method_and_path: str) -> RouteMeta | None:
    """Look up metadata by route key (e.g. ``"POST /dd/quick"``)."""
    return ROUTE_METADATA.get(method_and_path)
