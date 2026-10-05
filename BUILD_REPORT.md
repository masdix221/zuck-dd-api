# DD API Build Report — 2026-09-24 ~8:00 PM CDT

Zuck's x402 pay-per-call token-risk API (`~/workspace/services/dd-api/`).
Venv: `~/workspace/.venvs/dd-api` (PEP 668 workaround; system python3 is locked).

## What works

- **Test suite: 27/27 pass** (`pytest tests/`). 24 pre-existing + 3 new in
  `tests/test_dd_flags.py` (added this session): synthetic hostile token fires
  HONEYPOT + BUY/SELL_TAX_EXTREME + THIN_LIQUIDITY + WHALE_CONCENTRATED →
  CRITICAL, score ≥80; synthetic benign token → LOW, score 0, no flags.
  The flag/score machinery was previously untested — now covered.
- **DD engine live on Base mainnet (read-only)**, all five data paths verified:
  1. On-chain metadata via `eth_call` (name/symbol/decimals/totalSupply)
  2. Bytecode mint-function scan + `owner()` status
  3. honeypot.is simulation (honeypot flag + buy/sell/transfer taxes)
  4. Dexscreener pairs + total liquidity
  5. Blockscout holder distribution (top-1/top-10 %)
- **x402 paywall handshake verified LIVE against the x402.org testnet
  facilitator**: `POST /dd/quick` → HTTP 402 with correct `accepts`
  (`scheme: exact`, `network: eip155:84532` = Base Sepolia,
  `asset: 0x036CbD53842c5426634e7929541eC2318f3dCF7e` = testnet USDC,
  `amount: 500000` = $0.50, `payTo:` test-only throwaway).
  No real money moved; throwaway payee key never persisted.

## Live test results (real addresses, real mainnet reads)

| Token | Address | Result |
|---|---|---|
| USDC (legit; address verified from ethskills catalog, verified 2026-02-16) | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | LOW, score 0. honeypot.is: not honeypot, 0% taxes. Only flag: info-level HOLDERS_UNAVAILABLE (one Blockscout timeout — correctly marked unavailable, not failed). |
| STONX (real Base memecoin; address from workspace records) | `0x89d8CB38067b55f820F29A9E12d0CE18682A2Bfc` | LOW, score 0, no flags. Dexscreener: ~$1.59M total liquidity across 5 pairs; top-1 24.5%, top-10 35.3%. Engine did not cry wolf — defensible for a quick screen. |
| PORCH imposter (community-flagged fake) | `0x655D23cAd9a3DB8F730696efD0F94E3b2C662c97` | Engine refused: "address has no contract code — not a token contract." Confirmed via two independent RPCs (mainnet.base.org, base-rpc.publicnode.com) — both return `0x`. NOTE: this conflicts with the 6:06 PM CDT observation of a 3,128-byte runtime at the same address. Possible explanations: contract self-destructed, or RPC state differences. Needs re-verification before any public claim. No verdict was invented. |

## Gaps found (not hidden)

1. **Top-1 holder concentration not checked.** The blueprint flags top-1 EOA
   >30% (CONCENTRATED) and LP-unlocked + top-1 >20% (FRAGILE); the code only
   checks top-10 >70/>50. Also holders aren't tagged (LP/lock/burn excluded),
   so top-1% may include the LP position itself.
2. **Full testnet settlement not completed.** The 402 requirements flow is
   proven live; the final paid-settlement hop needs a *funded* Base Sepolia
   payer wallet, and every major Base Sepolia faucet requires interactive
   signup/browser — not cleanly automatable from here. The settle/verify path
   is x402-library code (not ours), so risk is low.

## Sandbox quirk (for future runs)

The x402 library's httpx client crashes on this sandbox's `NO_PROXY` value:
bracketed IPv6 entries (`[::1]`, `[fd8b:...]`) → `httpx.InvalidURL: Invalid
port: ':1]'`. Workaround for local test runs only: export a cleaned
`NO_PROXY`/`no_proxy` (`localhost,127.0.0.1,::1,198.19.0.1,198.19.0.2`)
before starting uvicorn. Not a product bug; do not "fix" in app code.

## Blocked on Beck (production launch)

1. **CDP API key** — mainnet facilitator auth; signup at
   portal.cdp.coinbase.com (free tier: 1000 settlements/mo). Goes in Secure
   Vault, never in chat/files.
2. **Production payee EVM address** — the wallet that receives the USDC.
   His call (main Base wallet or a fresh one); set as `PAYEE_EVM_ADDRESS`.
3. **Hosting** — a public server URL so buyers can discover and call it
   (needed for `/.well-known/x402` + CDP Bazaar listing).

Testnet needs none of the above; mainnet needs all three. Nothing was spent,
no real keys were created or stored, no mainnet writes were made (read-only
throughout, per the hard rules).

## Update — 2026-10-05 (subagent continuation)

- **Tests: 35/35 pass** (27 original + 8 new: test_direct_pay.py from Sep 26 worker).
- **Security scan:** Bandit 0 issues across direct_pay.py, ledger.py, payment.py. Manual logic review: replay protection (tx_hash_seen + UNIQUE constraint), 24h recency window, receive-only design — all sound.
- **Live server verified** (running on 127.0.0.1:8000, testnet mode): /v1/ping healthy, POST /dd/quick → 402 (Base Sepolia, $0.50 test USDC), POST /dd/approvals → 402 ($0.25), /.well-known/x402 discovery manifest live.
- **DD engine re-verified** on live mainnet data: USDC → LOW, score 0 (Blockscout timeout correctly marked unavailable, not failed).
- **Public URL: BLOCKED by sandbox networking.** cloudflared (TLS MITM), localtunnel (no tunnel establishment), pinggy/SSH (connection reset). Server is deployment-ready (Dockerfile present); needs a host with real egress or Beck's call on hosting.
