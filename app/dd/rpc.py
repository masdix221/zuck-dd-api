"""Read-only Base mainnet RPC helpers.

No API keys, no signing. Tries a list of public RPC endpoints with
failover, since any single public endpoint can rate-limit or lag.
"""

from __future__ import annotations

import os
import time

import requests

RPC_URLS = [
    u for u in [
        os.environ.get("BASE_RPC_URL", "").strip(),
        "https://base.publicnode.com",
        "https://mainnet.base.org",
        "https://1rpc.io/base",
    ]
    if u
]

_REQUEST_TIMEOUT = 20


def rpc(method: str, params: list, timeout: int = _REQUEST_TIMEOUT) -> object:
    """Raw JSON-RPC call with failover across public endpoints."""
    last_err: object = None
    for url in RPC_URLS:
        try:
            r = requests.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "error" in data:
                last_err = data["error"]
                continue
            return data.get("result") if isinstance(data, dict) else None
        except Exception as e:  # noqa: BLE001 - failover, keep last error
            last_err = e
            time.sleep(0.2)
    raise RuntimeError(f"all Base RPC endpoints failed (last: {last_err})")


def eth_call(to: str, data: str, block: str = "latest") -> str | None:
    result = rpc("eth_call", [{"to": to, "data": data}, block])
    return result if isinstance(result, str) else None


def get_code(address: str) -> str:
    result = rpc("eth_getCode", [address, "latest"])
    return result if isinstance(result, str) else "0x"


def get_logs(
    address: str,
    topics: list[str | None],
    from_block: str = "0x0",
    to_block: str = "latest",
) -> list:
    """eth_getLogs with an address filter (full history allowed by most RPCs)."""
    result = rpc(
        "eth_getLogs",
        [
            {
                "address": address,
                "topics": topics,
                "fromBlock": from_block,
                "toBlock": to_block,
            }
        ],
        timeout=45,
    )
    return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# Minimal ABI decoding (avoids a web3 dependency for simple reads)
# ---------------------------------------------------------------------------

def decode_uint256(hexdata: str | None) -> int | None:
    if not hexdata or hexdata == "0x":
        return None
    try:
        return int(hexdata, 16)
    except ValueError:
        return None


def decode_address(hexdata: str | None) -> str | None:
    """Decode an address from a 32-byte ABI word (last 20 bytes)."""
    if not hexdata or hexdata == "0x":
        return None
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    if len(h) < 40:
        return None
    return "0x" + h[-40:]


def decode_string(hexdata: str | None) -> str | None:
    """Decode an ABI string, falling back to bytes32 for old tokens."""
    if not hexdata or hexdata == "0x":
        return None
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    try:
        raw = bytes.fromhex(h)
    except ValueError:
        return None
    # Dynamic string: first word is offset (0x20), second word is length.
    if len(raw) >= 64 and int.from_bytes(raw[0:32], "big") == 32:
        length = int.from_bytes(raw[32:64], "big")
        if length > 10_000:
            return None
        try:
            return raw[64 : 64 + length].decode("utf-8", errors="replace").rstrip("\x00")
        except Exception:  # noqa: BLE001
            return None
    # bytes32 fallback
    try:
        return raw[:32].decode("utf-8", errors="replace").rstrip("\x00") or None
    except Exception:  # noqa: BLE001
        return None


# Common ERC-20 / Ownable selectors
SIG_NAME = "0x06fdde03"
SIG_SYMBOL = "0x95d89b41"
SIG_DECIMALS = "0x313ce567"
SIG_TOTAL_SUPPLY = "0x18160ddd"
SIG_OWNER = "0x8da5cb5b"
SIG_MINT = "40c10f19"  # mint(address,uint256) — bytecode heuristic only
APPROVAL_TOPIC = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3db"
MAX_UINT256 = 2**256 - 1


def allowance(token: str, owner: str, spender: str) -> int | None:
    """allowance(address owner, address spender) -> uint256. Selector 0xdd62ed3e."""
    data = (
        "0xdd62ed3e"
        + owner[2:].rjust(64, "0")
        + spender[2:].rjust(64, "0")
    )
    return decode_uint256(eth_call(token, data))


def pad_topic_address(address: str) -> str:
    return "0x" + address[2:].rjust(64, "0").lower()
