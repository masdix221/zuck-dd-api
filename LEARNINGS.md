# Zuck DD API — LEARNINGS.md

Build log of what actually bit us, 2026-09-18. Read before touching
`app/dd/` or deploying.

## 1. Never hand-type contract addresses

Three failure modes, all in one afternoon:

- **Malformed:** I dropped hex chars typing Permit2, the 0x Exchange Proxy,
  and Seaport 1.5 from memory (39/41-char addresses). Caught by a regex
  check `0x[0-9a-fA-F]{40}` — but only after a unit test silently passed a
  *also-wrong* address.
- **Wrong chain:** Uniswap's mainnet Universal Router (`0x3fC91A...`),
  SwapRouter02 (`0x2626664c...`), and V2 Router02 (`0x7a250d...`) have **no
  code on Base** (verified via `eth_getCode`). Uniswap's own docs warn not
  to assume cross-chain address parity. Removed from `KNOWN_SPENDERS` with
  a do-not-re-add comment.
- **Wrong tail:** my "remembered" Seaport 1.5 differed from the canonical
  `0x00000000000000ADc04C56Bf30aC9d3c0aAF14dC` (seaport repo, confirmed 2x).

**Rule:** every curated address gets format-validated AND `eth_getCode`'d on
the chain the product runs on, at build time. A unit test pins the verified
set. The audit's correctness doesn't depend on the list (unknown is the
safe default), but wrong labels are worse than no labels.

## 2. Public Base RPCs reject broad historical `eth_getLogs`

First `/dd/approvals` implementation enumerated `Approval` events with
`eth_getLogs`. Dead on arrival:

- PublicNode: requires an archive token for historical ranges.
- `mainnet.base.org`: caps log queries at 2,000 blocks.
- 1RPC: caps at 50 blocks.

**Replacement:** paginate the wallet's outgoing txs via Blockscout
(`?module=account&action=txlist`), filter for the `approve()` selector
(`0x095ea7b3`) in calldata, keep the latest per token/spender pair, then
read **current** `allowance()` live via RPC. Only still-active allowances
are reported — revoked/spent-down approvals drop out naturally.

Calldata slice (don't get this wrong): with `h` = `0x`-prefixed input,
the spender is the last 40 hex chars of the first 32-byte word:
`h[32:72]`. A unit test pins this against a real encoded `approve()`.

## 3. Case-sensitivity in address lookups

Decoded calldata addresses come back lowercase; curated dicts use
checksummed mixed case. `KNOWN_SPENDERS.get(spender)` silently missed the
0x Exchange Proxy until lookups went through a lowercase index
(`_KNOWN_SPENDERS_LC`). Test pins case-insensitive matching.

## 4. httpx chokes on bracketed IPv6 in `no_proxy`

The x402 facilitator client builds `httpx.Client()` which parses
`no_proxy`. Entries like `[::1]` raise
`httpx.InvalidURL: Invalid port: ':1]'` — turning every paid route into
HTTP 500. Fix: unset `no_proxy`/`NO_PROXY` in environments that set them
(this VM's egress config has them). Deployment note: check the host's
proxy env before blaming the payment code.

## 5. "Unknown" spenders should still carry signal

Resolving unknown spenders to their Blockscout contract name
(`unknown (AllowanceHolder)`) turned a dead-end label into actionable
signal — Beck's wallet has a near-max USDC allowance to a contract
Blockscout names "AllowanceHolder". Cached per-process
(`_SPENDER_NAMES`); one extra Blockscout call per unknown spender.

## 6. 402 challenges are testable without signing

Set any dummy `PAYEE_EVM_ADDRESS` + `X402_TESTNET=true` and POST without a
payment: the server returns a valid x402 v2 `402 Payment Required`
challenge. Verified amounts: `500000` ($0.50 quick), `250000` ($0.25
approvals) in 6-decimal USDC on `eip155:84532` (Base Sepolia). No wallet,
no signature, no money moves — challenge-shape only. **Never** run a
signed settlement test without Beck's explicit approval.

## 7. Shell hygiene

`pkill -f "uvicorn.*8471"` matches your own command line (the pattern is
in it) and SIGTERMs your shell. Use the bracket trick:
`pkill -f "[u]vicorn app.main:app"`.

## 8. Performance envelope (2026-09-18, Beck's wallet)

`/dd/approvals`: 498 txs scanned, 216 approve calls, 9 active pairs →
~60s wall time. Cost drivers: Blockscout pagination (~10 pages) + one
`allowance()` RPC call per unique pair. Fine at $0.25/call, but add a
short TTL cache before any real traffic.
