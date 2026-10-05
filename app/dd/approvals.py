"""POST /dd/approvals — wallet token-approval audit (in-house DD add-on).

Replaces the unverified "GitLawb Leash" lead: built in-house, read-only.

How it works (all read-only, keyless):
  1. Paginate the wallet's outgoing transactions via Blockscout, filtering to
     decoded ``approve`` calls. The tx list item carries ``raw_input``, so the
     spender and amount decode with zero extra calls.
  2. Keep the LATEST approval per (token, spender) pair (newest-first pages,
     first occurrence wins — a later approve(0) revocation supersedes).
  3. For each pair whose latest approval amount > 0, read the CURRENT on-chain
     ``allowance`` — state may have changed since (spent down via transferFrom).
  4. Flag unlimited allowances (2**256-1), ranked highest when the spender is
     not in the curated known-good list.

Known-good spenders (major routers/bridges) are labeled; anything else is
"unknown" and ranked higher risk. Revoking is the wallet owner's own action —
this endpoint only reports.

Limitations (documented, not hidden):
  - ``permit()`` (EIP-2612 signature) approvals leave no approve-tx and are
    not enumerated here.
  - Very old history is bounded by pagination depth (10 pages ~ 500 txs).
"""

from __future__ import annotations

import logging
import time

import requests

from app.dd import rpc, sources

logger = logging.getLogger(__name__)

# Curated known-good spenders on Base (same deterministic deployments as
# Ethereum mainnet unless noted). Anything not listed -> "unknown".
KNOWN_SPENDERS: dict[str, str] = {
    "0x000000000022D473030F116dDEE9F6B43aC78BA3": "Uniswap Permit2",
    "0x1111111254eeb25477b68fb85ed929f73a960582": "1inch Aggregation Router v6",
    # NOTE: Uniswap's mainnet Universal/Swap/V2 router addresses have NO code
    # on Base (verified via eth_getCode 2026-09-18) — do NOT re-add them.
    "0xc36442b4a4522e871399cd717abdd847ab11fe88": "Uniswap V3 Position Manager",
    "0xDef1C0ded9bec7F1a1670819833240f027b25EfF": "0x Exchange Proxy",
    "0x00000000000000ADc04C56Bf30aC9d3c0aAF14dC": "OpenSea Seaport 1.5",
    "0x4200000000000000000000000000000000000010": "Base L2StandardBridge (predeploy)",
}
# Lowercase index so lookups match regardless of checksum casing.
_KNOWN_SPENDERS_LC = {k.lower(): v for k, v in KNOWN_SPENDERS.items()}

_APPROVE_SELECTOR = "0x095ea7b3"
_MAX_PAGES = 10
_MAX_PAIRS_CHECKED = 100
_TIMEOUT = 20
_HEADERS = {"User-Agent": "zuck-dd-api/0.1 (+musebook.lol)"}

# Cache for Blockscout contract-name lookups on unknown spenders.
_SPENDER_NAMES: dict[str, str | None] = {}


def _spender_contract_name(spender: str) -> str | None:
    """Best-effort contract name for an unknown spender (Blockscout label).

    Returns None for EOAs, unlabeled contracts, or lookup failures — the
    caller falls back to plain "unknown".
    """
    key = spender.lower()
    if key in _SPENDER_NAMES:
        return _SPENDER_NAMES[key]
    name: str | None = None
    try:
        resp = requests.get(
            f"https://base.blockscout.com/api/v2/addresses/{spender}",
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("is_contract"):
            raw = (data.get("name") or "").strip()
            name = raw or None
    except Exception:  # noqa: BLE001
        name = None
    _SPENDER_NAMES[key] = name
    return name


def _is_address(value: str) -> bool:
    v = value.strip()
    return v.startswith("0x") and len(v) == 42 and all(
        c in "0123456789abcdefABCDEF" for c in v[2:]
    )


def _decode_approve_input(raw_input: str) -> tuple[str, int] | None:
    """Decode approve(spender, amount) calldata -> (spender, amount)."""
    if not raw_input or not raw_input.lower().startswith(_APPROVE_SELECTOR):
        return None
    h = raw_input[2:] if raw_input.startswith("0x") else raw_input
    if len(h) < 8 + 64 + 64:
        return None
    spender = "0x" + h[8 + 24 : 8 + 64]
    try:
        amount = int(h[8 + 64 : 8 + 128], 16)
    except ValueError:
        return None
    if not _is_address(spender):
        return None
    return spender.lower(), amount


def _tx_pages(wallet: str):
    """Yield transaction-list pages (newest first) for outgoing txs."""
    url = f"https://base.blockscout.com/api/v2/addresses/{wallet}/transactions"
    params: dict = {"filter": "from"}
    for _ in range(_MAX_PAGES):
        try:
            r = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code != 200:
                logger.warning("tx list HTTP %s", r.status_code)
                return
            data = r.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("tx list failed: %s", e)
            return
        items = data.get("items") or []
        if not items:
            return
        yield items
        nxt = data.get("next_page_params")
        if not nxt:
            return
        params = dict(nxt)
        params["filter"] = "from"


def approvals_audit(wallet: str) -> dict:
    wallet = wallet.strip()
    if not _is_address(wallet):
        return {"error": "invalid wallet address", "wallet": wallet}
    wallet = "0x" + wallet[2:].lower()

    started = time.time()
    errors: list[str] = []

    # --- 1. enumerate latest approve() per (token, spender) -------------------
    latest: dict[tuple[str, str], dict] = {}
    txs_seen = 0
    approve_txs = 0
    for page in _tx_pages(wallet):
        for tx in page:
            txs_seen += 1
            if tx.get("status") != "ok":
                continue
            method = (tx.get("method") or "").lower()
            raw = tx.get("raw_input") or ""
            if method != "approve" and not raw.lower().startswith(_APPROVE_SELECTOR):
                continue
            decoded = _decode_approve_input(raw)
            if not decoded:
                continue
            spender, amount = decoded
            to = tx.get("to") or {}
            token = (to.get("hash") or "").lower()
            if not token or not _is_address(token):
                continue
            approve_txs += 1
            key = (token, spender)
            if key not in latest:  # newest-first: first occurrence = latest
                latest[key] = {
                    "token": token,
                    "spender": spender,
                    "last_approved_raw": str(amount),
                    "last_approved_unlimited": amount == rpc.MAX_UINT256,
                    "tx_hash": tx.get("hash"),
                    "block_number": tx.get("block_number"),
                }

    # --- 2. token symbols (one Blockscout call, matched locally) --------------
    symbols: dict[str, str] = {}
    tokens_held = 0
    try:
        balances = sources.token_balances(wallet)
        tokens_held = len(balances)
        for b in balances:
            if b.get("symbol"):
                symbols[b["contract"].lower()] = b["symbol"]
    except Exception as e:  # noqa: BLE001
        errors.append(f"token list failed: {e}")

    # --- 3. current on-chain allowance per pair --------------------------------
    findings: list[dict] = []
    pairs = list(latest.values())[:_MAX_PAIRS_CHECKED]
    for p in pairs:
        try:
            allowed = rpc.allowance(p["token"], wallet, p["spender"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"allowance read failed {p['token']}->{p['spender']}: {e}")
            continue
        if not allowed:
            continue  # revoked or fully spent — nothing active to report
        unlimited = allowed == rpc.MAX_UINT256
        known_label = _KNOWN_SPENDERS_LC.get(p["spender"].lower())
        if known_label:
            label, is_known = known_label, True
        else:
            cname = _spender_contract_name(p["spender"])
            label, is_known = (f"unknown ({cname})" if cname else "unknown"), False
        risk = (
            "HIGH" if unlimited and not is_known
            else "MEDIUM" if unlimited
            else "LOW" if not is_known
            else "INFO"
        )
        symbol = symbols.get(p["token"])
        if not symbol:
            try:
                symbol = rpc.decode_string(rpc.eth_call(p["token"], rpc.SIG_SYMBOL))
            except Exception:  # noqa: BLE001
                symbol = None
        findings.append(
            {
                "token": p["token"],
                "token_symbol": symbol,
                "spender": p["spender"],
                "spender_label": label,
                "spender_known": is_known,
                "allowance_raw": str(allowed),
                "unlimited": unlimited,
                "risk": risk,
                "last_approve_tx": p["tx_hash"],
            }
        )

    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    findings.sort(key=lambda f: rank.get(f["risk"], 4))

    summary = {
        "total_active_approvals": len(findings),
        "unlimited": sum(1 for f in findings if f["unlimited"]),
        "high_risk": sum(1 for f in findings if f["risk"] == "HIGH"),
        "unknown_spenders": sum(1 for f in findings if not f["spender_known"]),
    }

    return {
        "wallet": wallet,
        "chain": "base",
        "chain_id": 8453,
        "transactions_scanned": txs_seen,
        "approve_calls_found": approve_txs,
        "tokens_held": tokens_held,
        "summary": summary,
        "approvals": findings,
        "errors": errors,
        "elapsed_ms": int((time.time() - started) * 1000),
        "note": (
            "Read-only audit from on-chain approve() history. 'unknown' spender = "
            "not in the curated router/bridge list — review before revoking; some "
            "dapps legitimately need approvals. permit() signature approvals are "
            "not enumerated. Revoking is done by the wallet owner, never by this API."
        ),
    }
