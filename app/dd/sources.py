"""Public no-key data sources for token DD.

All three are free, keyless HTTP APIs:
- honeypot.is v2  — honeypot simulation + buy/sell tax (chainID 8453 = Base)
- Dexscreener     — pairs, liquidity, volume, price (keyless, generous limits)
- Blockscout v2   — token holders + token metadata (Base's official explorer)

Every fetcher degrades to None / empty on failure — a source being down must
never crash a paid call; the response just marks that section "unavailable".
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_HEADERS = {"User-Agent": "zuck-dd-api/0.1 (+musebook.lol)"}


def _get(url: str, params: dict | None = None) -> dict | list | None:
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            logger.warning("source %s -> HTTP %s", url, r.status_code)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001 - degrade, don't crash
        logger.warning("source %s failed: %s", url, e)
        return None


def honeypot_screen(contract: str) -> dict | None:
    """honeypot.is simulation: is_honeypot, buy/sell/transfer tax."""
    data = _get(
        "https://api.honeypot.is/v2/IsHoneypot",
        params={"address": contract, "chainID": "8453"},
    )
    if not isinstance(data, dict):
        return None
    hp = data.get("honeypotResult") or {}
    sim = data.get("simulationResult") or {}
    holder = data.get("holderAnalysis") or {}
    return {
        "is_honeypot": bool(hp.get("isHoneypot", False)),
        "honeypot_reason": hp.get("honeypotReason"),
        "buy_tax_pct": _pct(sim.get("buyTax")),
        "sell_tax_pct": _pct(sim.get("sellTax")),
        "transfer_tax_pct": _pct(sim.get("transferTax")),
        "buy_gas": sim.get("buyGas"),
        "sell_gas": sim.get("sellGas"),
        "holders": holder.get("holders"),
        "successful_swaps": (data.get("summary") or {}).get("successful"),
    }


def _pct(value: object) -> float | None:
    try:
        return float(value) if value is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def dex_pairs(contract: str) -> dict | None:
    """Dexscreener pairs on Base: liquidity, price, volume, fdv."""
    data = _get(f"https://api.dexscreener.com/latest/dex/tokens/{contract}")
    if not isinstance(data, dict):
        return None
    pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "base"]
    if not pairs:
        return {"pairs_found": 0, "total_liquidity_usd": 0.0, "pairs": []}
    total_liq = 0.0
    out_pairs = []
    for p in pairs[:10]:
        liq = ((p.get("liquidity") or {}).get("usd")) or 0
        try:
            total_liq += float(liq)
        except (TypeError, ValueError):
            pass
        out_pairs.append(
            {
                "dex": p.get("dexId"),
                "pair_address": p.get("pairAddress"),
                "price_usd": p.get("priceUsd"),
                "liquidity_usd": liq,
                "fdv": p.get("fdv"),
                "market_cap": p.get("marketCap"),
                "volume_h24_usd": (p.get("volume") or {}).get("h24"),
                "txns_h24": p.get("txns", {}).get("h24"),
            }
        )
    out_pairs.sort(key=lambda x: float(x["liquidity_usd"] or 0), reverse=True)
    return {
        "pairs_found": len(pairs),
        "total_liquidity_usd": round(total_liq, 2),
        "top_pair": out_pairs[0],
        "pairs": out_pairs,
    }


def token_holders(contract: str, limit: int = 50) -> dict | None:
    """Blockscout top holders -> concentration metrics."""
    data = _get(f"https://base.blockscout.com/api/v2/tokens/{contract}/holders")
    if not isinstance(data, dict):
        return None
    items = data.get("items") or []
    holders = []
    for it in items[:limit]:
        addr = (it.get("address") or {}).get("hash")
        try:
            value = int(it.get("value") or 0)
        except (TypeError, ValueError):
            value = 0
        if addr:
            holders.append({"address": addr, "value": value})
    return {"holders": holders}


def token_balances(wallet: str) -> list[dict]:
    """Blockscout ERC-20 balances for a wallet (approval-audit input)."""
    data = _get(f"https://base.blockscout.com/api/v2/addresses/{wallet}/token-balances")
    if not isinstance(data, list):
        return []
    out = []
    for t in data:
        try:
            value = int(t.get("value") or 0)
        except (TypeError, ValueError):
            value = 0
        token = t.get("token") or {}
        out.append(
            {
                "contract": token.get("address_hash") or token.get("address"),
                "symbol": token.get("symbol"),
                "decimals": token.get("decimals"),
                "value": value,
                "token_type": token.get("type"),
            }
        )
    return [t for t in out if t["contract"] and t["token_type"] == "ERC-20"]
