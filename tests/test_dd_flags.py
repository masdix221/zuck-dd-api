"""Synthetic tests for the /dd/quick flag + scoring machinery.

No network: all data sources are monkeypatched to return hostile values,
proving the flag branches fire and the verdict escalates. Complements the
live smoke tests (USDC/STONX on mainnet) which only exercised the no-flag path.
"""

import sys

import pytest

sys.path.insert(0, ".")

from app.dd import quick as quick_mod
from app.dd import rpc, sources


def _hex_uint(n: int) -> str:
    return "0x" + format(n, "064x")


def _run_hostile(monkeypatch):
    """Feed quick_screen a fully hostile token; return the result dict."""
    monkeypatch.setattr(rpc, "get_code", lambda c: "0x6080604052348015600f57600080fd5b506040516020806101348339018101")
    monkeypatch.setattr(rpc, "decode_string", lambda v: "SCAM")
    monkeypatch.setattr(rpc, "decode_uint256", lambda v: 18)
    # owner() returns a live EOA (not renounced)
    monkeypatch.setattr(
        rpc,
        "decode_address",
        lambda v: "0x1111111111111111111111111111111111111111",
    )
    monkeypatch.setattr(rpc, "eth_call", lambda *a, **k: "0x")

    monkeypatch.setattr(
        sources,
        "honeypot_screen",
        lambda c: {
            "is_honeypot": True,
            "honeypot_reason": "transferFrom failed",
            "buy_tax_pct": 60.0,
            "sell_tax_pct": 99.0,
        },
    )
    monkeypatch.setattr(
        sources,
        "dex_pairs",
        lambda c: {"pairs_found": 2, "total_liquidity_usd": 500.0, "pairs": []},
    )
    big = 10**24
    monkeypatch.setattr(
        sources,
        "token_holders",
        lambda c: {
            "holders": [
                {"address": "0xaaa", "value": int(big * 0.80)},
                {"address": "0xbbb", "value": int(big * 0.05)},
            ]
        },
    )
    return quick_mod.quick_screen("0x" + "ab" * 20)


def test_hostile_token_fires_critical_flags(monkeypatch):
    r = _run_hostile(monkeypatch)
    codes = {f["code"] for f in r["flags"]}
    assert "HONEYPOT" in codes
    assert "BUY_TAX_EXTREME" in codes
    assert "SELL_TAX_EXTREME" in codes
    assert "THIN_LIQUIDITY" in codes
    assert "WHALE_CONCENTRATED" in codes  # top10 = 85% > 70


def test_hostile_token_verdict_and_score(monkeypatch):
    r = _run_hostile(monkeypatch)
    assert r["risk_score"] >= 80
    assert r["verdict"] == "CRITICAL"


def test_benign_token_stays_low(monkeypatch):
    monkeypatch.setattr(rpc, "get_code", lambda c: "0x60806040")
    monkeypatch.setattr(rpc, "decode_string", lambda v: "SAFE")

    def fake_call(contract, sig, *a, **k):
        if sig == rpc.SIG_DECIMALS:
            return "decimals"
        if sig == rpc.SIG_TOTAL_SUPPLY:
            return "supply"
        return "0x"

    def fake_uint(v):
        if v == "decimals":
            return 18
        if v == "supply":
            return 10**24
        return 0

    monkeypatch.setattr(rpc, "eth_call", fake_call)
    monkeypatch.setattr(rpc, "decode_uint256", fake_uint)
    monkeypatch.setattr(
        rpc, "decode_address", lambda v: "0x0000000000000000000000000000000000000000"
    )
    monkeypatch.setattr(
        sources,
        "honeypot_screen",
        lambda c: {"is_honeypot": False, "buy_tax_pct": 0.0, "sell_tax_pct": 0.0},
    )
    monkeypatch.setattr(
        sources,
        "dex_pairs",
        lambda c: {"pairs_found": 3, "total_liquidity_usd": 2_000_000.0, "pairs": []},
    )
    big = 10**24
    holders = [{"address": f"0x{i:040x}", "value": int(big * 0.01)} for i in range(50)]
    monkeypatch.setattr(sources, "token_holders", lambda c: {"holders": holders})
    r = quick_mod.quick_screen("0x" + "cd" * 20)
    assert r["verdict"] == "LOW"
    assert r["risk_score"] == 0
    assert r["flags"] == []
