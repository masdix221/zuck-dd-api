"""Facilitator-free direct payment verification (Base mainnet).

Why this exists: the stock x402 mainnet path needs Coinbase's CDP
facilitator, which needs a CDP API key (key ID + Ed25519 secret) that has to
be created in a web portal and stored as a server secret. This module removes
that whole dependency.

Flow (no facilitator, no API keys, no private keys anywhere — receive-only):
  1. Client calls a priced route with no payment header -> gets HTTP 402
     with JSON instructions: pay ``amount`` USDC on Base to ``pay_to``.
  2. Client submits the USDC transfer itself, from any wallet.
  3. Client retries the SAME request with header ``X-Payment-Tx: <tx_hash>``.
  4. Server fetches the receipt over public Base RPC (keyless, failover)
     and requires ALL of:
       - the tx exists and succeeded (status == 1)
       - the receipt holds a USDC Transfer log with to == payee
         and value >= the route price
       - the block is recent (default: within 24h — kills ancient-tx replay)
       - the tx hash was never used before (ledger UNIQUE on tx_hash)
  5. Server records the ledger row and serves the response.

The money moves peer-to-peer on-chain. This server only *reads* the chain,
so there is nothing to steal from it and no secret to rotate.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from app.dd.rpc import rpc

logger = logging.getLogger(__name__)

# USDC on Base (native Circle issuance, 6 decimals).
# Canonical per CoinGecko; EIP-55 checksummed.
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

# A payment tx older than this is rejected (replay window).
DEFAULT_MAX_AGE_SECONDS = 24 * 3600

_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class PaymentVerification:
    ok: bool
    reason: str
    payer: str | None = None
    value_raw: int | None = None
    block_timestamp: int | None = None


def parse_price_to_raw(price: str, decimals: int = 6) -> int:
    """'$0.50' -> 500000. Raises ValueError on garbage."""
    cleaned = price.strip().lstrip("$").replace(",", "")
    return int(round(float(cleaned) * (10**decimals)))


def _topic_address(topic: str) -> str | None:
    h = topic[2:] if topic.startswith("0x") else topic
    if len(h) != 64:
        return None
    return "0x" + h[-40:]


def verify_direct_payment(
    tx_hash: str,
    *,
    payee: str,
    amount_raw: int,
    usdc: str = USDC_BASE,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: int | None = None,
) -> PaymentVerification:
    """Verify a client-submitted USDC payment tx against chain state.

    Args:
        tx_hash: 0x-prefixed 64-hex transaction hash from X-Payment-Tx.
        payee: Expected recipient (the API's payee address).
        amount_raw: Minimum acceptable amount in USDC base units.
        usdc: USDC contract address (Base mainnet default).
        max_age_seconds: Reject txs mined older than this.
        now: Override for time.time() (tests).

    Returns:
        PaymentVerification with ok=True only if every check passes.
    """
    if not tx_hash or not _TX_HASH_RE.match(tx_hash.strip()):
        return PaymentVerification(False, "malformed tx hash")

    tx_hash = tx_hash.strip()
    try:
        receipt = rpc("eth_getTransactionReceipt", [tx_hash])
    except Exception as e:  # noqa: BLE001 - RPC failure is a verification failure
        logger.warning("direct-pay RPC failure for %s: %s", tx_hash[:12], e)
        return PaymentVerification(False, "chain lookup failed, retry")

    if not receipt:
        return PaymentVerification(False, "tx not found on Base")
    if receipt.get("status") != "0x1":
        return PaymentVerification(False, "tx failed on-chain")

    payee_l = payee.lower()
    usdc_l = usdc.lower()
    payer: str | None = None
    value_raw: int | None = None
    for log in receipt.get("logs") or []:
        if (log.get("address") or "").lower() != usdc_l:
            continue
        topics = log.get("topics") or []
        if not topics or (topics[0] or "").lower() != TRANSFER_TOPIC:
            continue
        if len(topics) < 3:
            continue
        to_addr = _topic_address(topics[2])
        if not to_addr or to_addr.lower() != payee_l:
            continue
        try:
            value = int(log.get("data") or "0x0", 16)
        except ValueError:
            continue
        if value >= amount_raw:
            payer = _topic_address(topics[1])
            value_raw = value
            break

    if payer is None:
        return PaymentVerification(
            False,
            f"no USDC transfer of >= {amount_raw} base units to payee in this tx",
        )

    # Recency check — the block must be fresh.
    try:
        block = rpc("eth_getBlockByHash", [receipt.get("blockHash"), False])
        ts = int((block or {}).get("timestamp") or "0x0", 16)
    except Exception:  # noqa: BLE001
        return PaymentVerification(False, "could not read block time, retry")
    age = (now if now is not None else int(time.time())) - ts
    if age < 0 or age > max_age_seconds:
        return PaymentVerification(False, "tx is older than the 24h payment window")

    return PaymentVerification(True, "verified", payer, value_raw, ts)


def payment_instructions(*, payee: str, price: str, amount_raw: int) -> dict:
    """The JSON body served with HTTP 402 in direct-pay mode."""
    return {
        "error": "Payment required",
        "payment": {
            "network": "eip155:8453",
            "network_name": "Base",
            "asset": USDC_BASE,
            "asset_symbol": "USDC",
            "pay_to": payee,
            "price": price,
            "amount_base_units": str(amount_raw),
        },
        "how_to_pay": [
            "1. Send the USDC amount above on Base to pay_to (any wallet).",
            "2. Retry the same request with header X-Payment-Tx: <your tx hash>.",
            "3. The server verifies the transfer on-chain and serves the response. "
            "Each tx hash works once; txs older than 24h are rejected.",
        ],
    }
