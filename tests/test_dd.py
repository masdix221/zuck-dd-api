"""Network-free unit tests for the DD helpers."""

from app.dd import rpc
from app.dd.quick import _is_address as quick_is_address
from app.dd.approvals import KNOWN_SPENDERS, _KNOWN_SPENDERS_LC, _is_address as appr_is_address


def test_is_address_valid():
    assert quick_is_address("0x91A2DAe9699f0B82540B5886b0d8759C22820bA3")
    assert appr_is_address("0xad365b23b3ff19e7902c53d4f3ebd317deb252b1")


def test_is_address_invalid():
    assert not quick_is_address("not-an-address")
    assert not quick_is_address("0x1234")
    assert not quick_is_address("")


def test_decode_uint256():
    assert rpc.decode_uint256("0x0a") == 10
    assert rpc.decode_uint256("0x") is None
    assert rpc.decode_uint256(None) is None


def test_decode_address():
    word = "0x" + "00" * 12 + "ad365b23b3ff19e7902c53d4f3ebd317deb252b1"
    assert rpc.decode_address(word) == "0xad365b23b3ff19e7902c53d4f3ebd317deb252b1"
    assert rpc.decode_address("0x") is None


def test_decode_string_dynamic():
    # "MUSEBOOK" as ABI-encoded dynamic string
    s = "MUSEBOOK".encode()
    word = (32).to_bytes(32, "big") + len(s).to_bytes(32, "big") + s.ljust(32, b"\x00")
    assert rpc.decode_string("0x" + word.hex()) == "MUSEBOOK"


def test_decode_string_bytes32():
    raw = "DRB".encode().ljust(32, b"\x00")
    assert rpc.decode_string("0x" + raw.hex()) == "DRB"


def test_pad_topic_address():
    assert (
        rpc.pad_topic_address("0xad365b23b3ff19e7902c53d4f3ebd317deb252b1")
        == "0x000000000000000000000000ad365b23b3ff19e7902c53d4f3ebd317deb252b1"
    )


def test_known_spenders_labeled():
    assert "Uniswap Permit2" in KNOWN_SPENDERS.values()
    assert "0x000000000022D473030F116dDEE9F6B43aC78BA3".lower() in (
        k.lower() for k in KNOWN_SPENDERS
    )


def test_known_spender_lookup_case_insensitive():
    # Decoded calldata addresses come back lowercase; dict keys are checksummed.
    assert _KNOWN_SPENDERS_LC["0xdef1c0ded9bec7f1a1670819833240f027b25eff"] == "0x Exchange Proxy"
    assert _KNOWN_SPENDERS_LC["0x000000000022d473030f116ddee9f6b43ac78ba3"] == "Uniswap Permit2"


def test_quick_rejects_bad_address():
    from app.dd.quick import quick_screen

    out = quick_screen("nope")
    assert out["error"] == "invalid contract address"


def test_approvals_rejects_bad_address():
    from app.dd.approvals import approvals_audit

    out = approvals_audit("0x123")
    assert out["error"] == "invalid wallet address"
