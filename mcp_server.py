#!/usr/bin/env python3
"""Zuck DD MCP server — the pay-per-call DD API as installable agent tools.

Stdio MCP server. Any MCP-compatible client (Claude Code, Cursor,
Claude Desktop) can add it with one line and call Zuck's due-diligence
tools in natural language — no API keys, no accounts.

    claude mcp add zuck-dd -- python /path/to/mcp_server.py

Then ask your agent:
    "Quick-screen 0x91A2DAe9699f0B82540B5886b0d8759C22820bA3 for me"

Payment: the DD API speaks x402 (HTTP 402 Payment Required). This server
acts as the x402 *client*: on a 402 it signs an EIP-3009 USDC
authorization with the operator's key and retries automatically.
Set DD_PAYER_KEY to a funded key (testnet: Base Sepolia USDC) to enable
paid calls. Without it, paid tools return the price and pay-to address so
the caller knows what the call costs instead of failing cryptically.

Env:
    DD_API_URL    Base URL of the DD API (default http://localhost:8000)
    DD_PAYER_KEY  Hex private key used to pay x402 charges (optional)

Pattern credit: QuoStr's QUOSTR Agent Tools + ortegarod/onchain-agent's
pay_x402 tool showed the shape — MCP toolkits as products, with the
agent handling 402 payment client-side via EIP-3009.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import time

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data
from mcp.server.mcpserver import MCPServer

log = logging.getLogger("zuck-dd-mcp")

API_URL = os.environ.get("DD_API_URL", "http://localhost:8000").rstrip("/")
PAYER_KEY = os.environ.get("DD_PAYER_KEY", "").strip()

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


# ---------------------------------------------------------------------------
# x402 client (mirrors the DD API's 402 flow + onchain-agent's payX402)
# ---------------------------------------------------------------------------

def _parse_402(resp: httpx.Response) -> dict:
    """Decode the base64 `payment-required` header into the 402 challenge."""
    header = resp.headers.get("payment-required")
    if not header:
        raise RuntimeError("402 response missing payment-required header")
    challenge = json.loads(base64.b64decode(header).decode())
    accepts = challenge.get("accepts") or []
    if not accepts:
        raise RuntimeError("402 challenge has no payment options")
    return challenge


def _sign_eip3009(accepted: dict, payer: str) -> tuple[str, dict]:
    """Sign an EIP-3009 TransferWithAuthorization for the accepted option.

    Returns (signature_hex, authorization_dict).
    """
    network = accepted.get("network", "")
    try:
        chain_id = int(network.split(":")[1])
    except (IndexError, ValueError):
        raise RuntimeError(f"Cannot parse chain id from network {network!r}")

    extra = accepted.get("extra") or {}
    amount = accepted.get("amount")
    if amount is not None:
        value = int(amount)
    else:
        price = (
            accepted.get("maxAmountRequired")
            or accepted.get("price")
            or "$0.01"
        )
        value = int(float(str(price).replace("$", "")) * 1_000_000)

    valid_before = int(time.time()) + int(accepted.get("maxTimeoutSeconds", 3600))
    nonce = "0x" + secrets.token_hex(32)

    typed = {
        "domain": {
            "name": extra.get("name", "USDC"),
            "version": str(extra.get("version", "2")),
            "chainId": chain_id,
            "verifyingContract": accepted["asset"],
        },
        "types": {
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ]
        },
        "primaryType": "TransferWithAuthorization",
        "message": {
            "from": payer,
            "to": accepted["payTo"],
            "value": value,
            "validAfter": 0,
            "validBefore": valid_before,
            "nonce": nonce,
        },
    }
    signed = Account.sign_message(encode_typed_data(typed))
    authorization = {
        "from": payer,
        "to": accepted["payTo"],
        "value": str(value),
        "validAfter": "0",
        "validBefore": str(valid_before),
        "nonce": nonce,
    }
    return signed.signature.hex(), authorization


def call_dd(path: str, body: dict | None) -> dict:
    """POST to the DD API, handling the x402 402 flow automatically."""
    url = API_URL + path
    with httpx.Client(timeout=120) as client:
        resp = client.post(url, json=body or {})

        if resp.status_code != 402:
            resp.raise_for_status()
            out = resp.json()
            out["_paid"] = False
            return out

        # --- 402: payment required ---
        challenge = _parse_402(resp)
        accepted = challenge["accepts"][0]
        price = (
            accepted.get("price")
            or accepted.get("maxAmountRequired")
            or (f"${int(accepted['amount']) / 1_000_000:.2f}" if accepted.get("amount") else "?")
        )

        if not PAYER_KEY:
            return {
                "_paid": False,
                "_payment_required": True,
                "price": price,
                "pay_to": accepted.get("payTo"),
                "network": accepted.get("network"),
                "message": (
                    f"This call costs {price} USDC via x402, and no DD_PAYER_KEY "
                    "is configured on this MCP server, so it was not executed. "
                    "Set DD_PAYER_KEY to a funded key to enable automatic payment."
                ),
            }

        payer = Account.from_key(PAYER_KEY).address
        signature, authorization = _sign_eip3009(accepted, payer)
        payload = {
            "x402Version": challenge.get("x402Version", 2),
            "resource": challenge.get("resource"),
            "accepted": accepted,
            "payload": {"signature": signature, "authorization": authorization},
        }
        headers = {
            "payment-signature": base64.b64encode(
                json.dumps(payload).encode()
            ).decode()
        }
        paid = client.post(url, json=body or {}, headers=headers)
        if not paid.ok:
            raise RuntimeError(
                f"x402 payment failed: HTTP {paid.status_code} — {paid.text[:300]}"
            )
        out = paid.json()
        out["_paid"] = True
        out["_price"] = price
        tx_header = paid.headers.get("payment-response")
        if tx_header:
            try:
                out["_tx"] = json.loads(base64.b64decode(tx_header).decode()).get(
                    "transaction"
                )
            except Exception:  # noqa: BLE001
                pass
        return out


def get_ping() -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.get(API_URL + "/v1/ping")
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

mcp = MCPServer("zuck-dd")


@mcp.tool(
    name="dd_quick_screen",
    description="""Quick-screen a token contract for risk — Zuck's 0.50 USDC quick-screen tier, pay-per-call.

Pass any Base token contract address. Returns token metadata, honeypot
simulation + buy/sell tax, mint-authority heuristic, ownership status,
Base liquidity depth, holder concentration, a flags list, a 0-100
risk_score, and a verdict (LOW / MEDIUM / HIGH / CRITICAL).

Costs $0.50 USDC per call via x402 (paid automatically if this server
has DD_PAYER_KEY set). Read-only — never moves funds.""",
)
def dd_quick_screen(contract: str) -> str:
    """Screen a Base token contract for risk."""
    contract = (contract or "").strip()
    if not ADDR_RE.match(contract):
        return f"Error: invalid contract address: {contract!r}"
    try:
        result = call_dd("/dd/quick", {"contract": contract})
        return json.dumps(result, indent=2)
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


@mcp.tool(
    name="dd_approvals_audit",
    description="""Audit a wallet's token approvals for risk — Zuck's in-house DD add-on, pay-per-call.

Pass any EVM wallet address. Scans the wallet's outgoing transaction
history, finds approve() calls, and reads each spender's CURRENT
allowance live on-chain (revoked or spent-down approvals are excluded).
Flags unlimited approvals, especially to unknown spenders. Known Base
contracts (Permit2, 1inch v6, Uniswap V3 Position Manager, 0x Exchange
Proxy, OpenSea Seaport 1.5, Base L2StandardBridge) are labeled by name.

Costs $0.25 USDC per call via x402 (paid automatically if this server
has DD_PAYER_KEY set). Read-only — revoking stays with the wallet owner.""",
)
def dd_approvals_audit(wallet: str) -> str:
    """Audit a wallet's token approvals."""
    wallet = (wallet or "").strip()
    if not ADDR_RE.match(wallet):
        return f"Error: invalid wallet address: {wallet!r}"
    try:
        result = call_dd("/dd/approvals", {"wallet": wallet})
        return json.dumps(result, indent=2)
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


@mcp.tool(
    name="dd_ping",
    description="""Health check for the Zuck DD API. Free — verifies the API is reachable
before spending on a paid screen.""",
)
def dd_ping() -> str:
    """Ping the DD API."""
    try:
        return json.dumps(get_ping(), indent=2)
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    mcp.run(transport="stdio")
