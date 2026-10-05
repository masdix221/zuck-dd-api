"""Tests for app.direct_pay (facilitator-free payment verification)."""

from __future__ import annotations

import pytest

from app import direct_pay
from app.direct_pay import (
    TRANSFER_TOPIC,
    USDC_BASE,
    parse_price_to_raw,
    verify_direct_payment,
)

PAYEE = "0xc8bdd7de62f6555beF98C69eeaB1781ae3D68590"
PAYER = "0x1111111111111111111111111111111111111111"
AMOUNT = 500_000  # $0.50 USDC
NOW = 1_750_000_000


def _pad(addr: str) -> str:
    return "0x" + addr[2:].rjust(64, "0").lower()


def _fake_receipt(*, value=AMOUNT, to=PAYEE, status="0x1"):
    data = hex(value)[2:].rjust(64, "0")
    return {
        "status": status,
        "blockHash": "0x" + "ab" * 32,
        "logs": [
            {
                "address": USDC_BASE,
                "topics": [TRANSFER_TOPIC, _pad(PAYER), _pad(to)],
                "data": "0x" + data,
            }
        ],
    }


def _fake_block(*, ts=NOW - 60):
    return {"timestamp": hex(ts)}


def _patch_rpc(monkeypatch, receipt, block=None):
    def fake_rpc(method, params, timeout=20):
        if method == "eth_getTransactionReceipt":
            return receipt
        if method == "eth_getBlockByHash":
            return block if block is not None else _fake_block()
        raise AssertionError(method)

    monkeypatch.setattr(direct_pay, "rpc", fake_rpc)


def test_parse_price():
    assert parse_price_to_raw("$0.50") == 500_000
    assert parse_price_to_raw("$0.25") == 250_000
    assert parse_price_to_raw("2") == 2_000_000


def test_happy_path(monkeypatch):
    _patch_rpc(monkeypatch, _fake_receipt())
    v = verify_direct_payment("0x" + "11" * 32, payee=PAYEE, amount_raw=AMOUNT, now=NOW)
    assert v.ok, v.reason
    assert v.payer.lower() == PAYER.lower()
    assert v.value_raw == AMOUNT


def test_wrong_payee_rejected(monkeypatch):
    _patch_rpc(monkeypatch, _fake_receipt(to="0x2222222222222222222222222222222222222222"))
    v = verify_direct_payment("0x" + "11" * 32, payee=PAYEE, amount_raw=AMOUNT, now=NOW)
    assert not v.ok


def test_underpaid_rejected(monkeypatch):
    _patch_rpc(monkeypatch, _fake_receipt(value=AMOUNT - 1))
    v = verify_direct_payment("0x" + "11" * 32, payee=PAYEE, amount_raw=AMOUNT, now=NOW)
    assert not v.ok


def test_failed_tx_rejected(monkeypatch):
    _patch_rpc(monkeypatch, _fake_receipt(status="0x0"))
    v = verify_direct_payment("0x" + "11" * 32, payee=PAYEE, amount_raw=AMOUNT, now=NOW)
    assert not v.ok


def test_stale_tx_rejected(monkeypatch):
    _patch_rpc(monkeypatch, _fake_receipt(), block=_fake_block(ts=NOW - 100_000))
    v = verify_direct_payment("0x" + "11" * 32, payee=PAYEE, amount_raw=AMOUNT, now=NOW)
    assert not v.ok
    assert "24h" in v.reason


def test_malformed_hash_rejected():
    v = verify_direct_payment("nope", payee=PAYEE, amount_raw=AMOUNT, now=NOW)
    assert not v.ok


def _find_live_usdc_transfer():
    """Grab a recent real USDC transfer from Blockscout (read-only)."""
    import json
    import urllib.request

    url = (
        "https://base.blockscout.com/api/v2/tokens/"
        f"{USDC_BASE}/transfers"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "dd-api-test/1.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode())
    items = data.get("items") or []
    assert items, "blockscout returned no USDC transfers"
    t = items[0]
    return t["transaction_hash"], t["to"]["hash"], int(t["total"]["value"])


def test_live_mainnet_usdc_transfer():
    """End-to-end against real Base mainnet state (read-only).

    Verifies a REAL recent USDC transfer passes with its own payee/amount,
    and fails when the amount is inflated by 1 base unit.
    """
    tx_hash, to_addr, value = _find_live_usdc_transfer()
    assert value > 0

    v = verify_direct_payment(tx_hash, payee=to_addr, amount_raw=value)
    assert v.ok, f"live verify failed: {v.reason}"

    v2 = verify_direct_payment(tx_hash, payee=to_addr, amount_raw=value + 1)
    assert not v2.ok
