"""POST /dd/quick — contract risk screen (Zuck's 0.50 USDC quick-screen tier).

Read-only checks on Base mainnet:
  1. Token metadata (name / symbol / decimals / totalSupply) via eth_call
  2. Honeypot + buy/sell tax via honeypot.is (keyless)
  3. Liquidity + pairs via Dexscreener (keyless)
  4. Holder concentration via Blockscout (keyless)
  5. Mint-function heuristic via bytecode scan (0x40c10f19)
  6. Ownership status via owner()

Outputs a 0-100 risk score, a verdict, and a human-readable flags list.
This is a screen, not financial advice — the response says so.
"""

from __future__ import annotations

import logging
import time

from app.dd import rpc, sources

logger = logging.getLogger(__name__)

_ZERO = "0x0000000000000000000000000000000000000000"


def _is_address(value: str) -> bool:
    v = value.strip()
    return v.startswith("0x") and len(v) == 42 and all(
        c in "0123456789abcdefABCDEF" for c in v[2:]
    )


def quick_screen(contract: str) -> dict:
    contract = contract.strip()
    if not _is_address(contract):
        return {"error": "invalid contract address", "contract": contract}

    started = time.time()
    flags: list[dict] = []
    score = 0

    def flag(level: str, code: str, message: str, points: int = 0) -> None:
        nonlocal score
        flags.append({"level": level, "code": code, "message": message})
        score += points

    # --- 1. on-chain metadata -------------------------------------------------
    meta: dict = {"contract": contract, "chain": "base", "chain_id": 8453}
    try:
        code = rpc.get_code(contract)
        meta["is_contract"] = code not in ("0x", "0x0", "")
        if not meta["is_contract"]:
            return {
                "contract": contract,
                "error": "address has no contract code — not a token contract",
            }
        meta["name"] = rpc.decode_string(rpc.eth_call(contract, rpc.SIG_NAME))
        meta["symbol"] = rpc.decode_string(rpc.eth_call(contract, rpc.SIG_SYMBOL))
        decimals = rpc.decode_uint256(rpc.eth_call(contract, rpc.SIG_DECIMALS))
        meta["decimals"] = decimals
        total_supply = rpc.decode_uint256(rpc.eth_call(contract, rpc.SIG_TOTAL_SUPPLY))
        meta["total_supply_raw"] = total_supply
        if decimals is not None and total_supply is not None:
            meta["total_supply"] = total_supply / (10**decimals)
    except Exception as e:  # noqa: BLE001
        logger.warning("quick metadata failed for %s: %s", contract, e)
        meta["metadata_error"] = "on-chain metadata unavailable"

    # --- 2. mint heuristic + ownership ---------------------------------------
    mint_info: dict = {}
    try:
        bytecode = rpc.get_code(contract).lower()
        mint_info["mint_function_present"] = rpc.SIG_MINT in bytecode.replace("0x", "")
        if mint_info["mint_function_present"]:
            flag("warn", "MINTABLE",
                 "Contract bytecode contains a mint(address,uint256) function — supply can grow.", 20)
    except Exception as e:  # noqa: BLE001
        mint_info["error"] = str(e)
    try:
        owner = rpc.decode_address(rpc.eth_call(contract, rpc.SIG_OWNER))
        if owner is None:
            mint_info["owner"] = None
            mint_info["ownership"] = "no owner() — not a standard Ownable contract"
        elif owner.lower() == _ZERO:
            mint_info["owner"] = owner
            mint_info["ownership"] = "renounced (owner is zero address)"
        else:
            mint_info["owner"] = owner
            mint_info["ownership"] = "owned — owner may retain privileged functions"
            if mint_info.get("mint_function_present"):
                flag("warn", "OWNER_CAN_MINT",
                     "Token is mintable AND ownership is not renounced.", 10)
    except Exception as e:  # noqa: BLE001
        mint_info["ownership"] = f"unreadable: {e}"

    # --- 3. honeypot / taxes --------------------------------------------------
    hp = sources.honeypot_screen(contract)
    if hp is None:
        flag("info", "HONEYPOT_UNAVAILABLE",
             "Honeypot simulation unavailable for this token — treat taxes as unknown.")
    else:
        if hp.get("is_honeypot"):
            flag("critical", "HONEYPOT",
                 f"Honeypot simulation flagged this token. Reason: {hp.get('honeypot_reason')}", 100)
        for label, key, sev in (("buy", "buy_tax_pct", 60), ("sell", "sell_tax_pct", 60)):
            tax = hp.get(key)
            if tax is None:
                continue
            if tax > 50:
                flag("critical", f"{label.upper()}_TAX_EXTREME",
                     f"{label} tax is {tax}% — effectively untradable.", sev)
            elif tax > 20:
                flag("warn", f"{label.upper()}_TAX_HIGH",
                     f"{label} tax is {tax}% — steep.", 30)
            elif tax > 10:
                flag("warn", f"{label.upper()}_TAX_ELEVATED",
                     f"{label} tax is {tax}% — above normal.", 15)
    meta["honeypot"] = hp

    # --- 4. liquidity ----------------------------------------------------------
    dex = sources.dex_pairs(contract)
    if dex is None:
        flag("info", "LIQUIDITY_UNAVAILABLE", "Liquidity data unavailable.")
    elif dex.get("pairs_found", 0) == 0:
        flag("critical", "NO_LIQUIDITY",
             "No Base DEX pairs found — token may be untradable or brand new.", 30)
    else:
        liq = dex.get("total_liquidity_usd") or 0
        if liq < 10_000:
            flag("warn", "THIN_LIQUIDITY",
                 f"Total Base liquidity ${liq:,.0f} — thin, expect high slippage.", 25)
        elif liq < 50_000:
            flag("info", "LOW_LIQUIDITY",
                 f"Total Base liquidity ${liq:,.0f} — modest.", 10)
    meta["liquidity"] = dex

    # --- 5. holder concentration ------------------------------------------------
    holders_data = sources.token_holders(contract)
    conc: dict = {}
    if holders_data and holders_data.get("holders") and meta.get("total_supply_raw"):
        total = meta["total_supply_raw"]
        hs = holders_data["holders"]
        top1 = hs[0]["value"] / total * 100 if total else 0
        top10 = sum(h["value"] for h in hs[:10]) / total * 100 if total else 0
        conc = {
            "top1_pct": round(top1, 2),
            "top10_pct": round(top10, 2),
            "holders_sampled": len(hs),
            "top_holders": [
                {"address": h["address"], "share_pct": round(h["value"] / total * 100, 2)}
                for h in hs[:5]
            ],
        }
        if top10 > 70:
            flag("warn", "WHALE_CONCENTRATED",
                 f"Top 10 holders own {top10:.1f}% of supply — dump risk.", 25)
        elif top10 > 50:
            flag("info", "CONCENTRATED",
                 f"Top 10 holders own {top10:.1f}% of supply.", 15)
    else:
        conc = {"unavailable": True}
        flag("info", "HOLDERS_UNAVAILABLE", "Holder distribution unavailable.")
    meta["holders"] = conc

    score = min(100, max(0, score))
    verdict = (
        "CRITICAL" if score >= 80
        else "HIGH" if score >= 50
        else "MEDIUM" if score >= 25
        else "LOW"
    )

    return {
        "contract": contract,
        "chain": "base",
        "chain_id": 8453,
        "token": {
            "name": meta.get("name"),
            "symbol": meta.get("symbol"),
            "decimals": meta.get("decimals"),
            "total_supply": meta.get("total_supply"),
        },
        "mint": mint_info,
        "honeypot": hp,
        "liquidity": dex,
        "holder_concentration": conc,
        "flags": flags,
        "risk_score": score,
        "verdict": verdict,
        "elapsed_ms": int((time.time() - started) * 1000),
        "disclaimer": "Automated screen, not financial advice. Verify before trading.",
    }
