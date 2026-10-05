# Zuck DD API — pay-per-call memecoin due diligence on Base

Vending machine for token DD: an AI agent (or person) pays a few cents in USDC
per call via [x402](https://x402.org) (HTTP 402) and gets the data back. No
accounts, no API keys, no Stripe. Built on the x402-paid-api-starter.

## Endpoints

| Route | Price | What it does |
|---|---|---|
| `POST /dd/quick` | **$0.50** | Contract risk screen — mirrors Zuck's 0.50 USDC quick-screen tier |
| `POST /dd/approvals` | **$0.25** | Wallet token-approval audit (in-house DD add-on) |
| `GET /v1/ping` | free | Health check |

**POST /dd/quick** — body `{"contract": "0x..."}`. Returns token metadata,
honeypot simulation + buy/sell tax (honeypot.is), mint-authority heuristic,
ownership status, Base liquidity depth (Dexscreener), holder concentration
(Blockscout), a `flags` list, a 0–100 `risk_score`, and a `verdict`
(LOW / MEDIUM / HIGH / CRITICAL). Automated screen, not financial advice.

**POST /dd/approvals** — body `{"wallet": "0x..."}`. Paginates the wallet's
outgoing transactions via Blockscout, finds successful `approve()` calls from
their calldata (selector `0x095ea7b3`), keeps the latest approval per
token/spender pair, and reads the **current** allowance live on-chain — so
revoked or spent-down approvals are excluded. Flags unlimited approvals,
especially to unknown spenders. Known Base contracts (Permit2, 1inch v6,
Uniswap V3 Position Manager, 0x Exchange Proxy, OpenSea Seaport 1.5, Base
L2StandardBridge) are labeled; other spenders resolve to their Blockscout
contract name where available, otherwise plain "unknown". Read-only; revoking
stays with the wallet owner.

> Why not `Approval` event logs? Public Base RPCs reject broad historical
> `eth_getLogs` ranges (PublicNode requires an archive token, base.org caps
> at 2,000 blocks, 1RPC at 50 blocks), so event-log enumeration was
> abandoned in favor of tx-history calldata scanning — see LEARNINGS.md.

All chain reads are **Base mainnet** (that's the product data) and read-only.
Payment settlement is **Base Sepolia** on testnet, **Base mainnet** in production.

## Quickstart (testnet, free)

```bash
cd ~/workspace/services/dd-api
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
# leave PAYEE_EVM_ADDRESS blank = dev mode, everything FREE
.venv/bin/uvicorn app.main:app --reload
```

```bash
curl localhost:8000/v1/ping
curl -s -X POST localhost:8000/dd/quick \
  -H 'Content-Type: application/json' \
  -d '{"contract":"0x91A2DAe9699f0B82540B5886b0d8759C22820bA3"}' | python3 -m json.tool
curl -s -X POST localhost:8000/dd/approvals \
  -H 'Content-Type: application/json' \
  -d '{"wallet":"0xad365b23b3ff19e7902c53d4f3ebd317deb252b1"}' | python3 -m json.tool
```

With a payee set, paid routes return `402 Payment Required` instead:
```bash
PAYEE_EVM_ADDRESS=0xYourPublicAddress .venv/bin/uvicorn app.main:app
curl -i -X POST localhost:8000/dd/quick -H 'Content-Type: application/json' \
  -d '{"contract":"0x91A2DAe9699f0B82540B5886b0d8759C22820bA3"}'   # -> 402
.venv/bin/python scripts/peek.py http://localhost:8000/dd/quick  # decode challenge
```

## Environment

| Var | Required | Purpose |
|---|---|---|
| `PAYEE_EVM_ADDRESS` | mainnet | Public wallet receiving USDC. **Blank = dev mode (all free). Never hardcode — set per deploy.** |
| `X402_TESTNET` | — | `true` = Base Sepolia via free public facilitator (default). `false` = Base mainnet via CDP. |
| `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET` | mainnet | Coinbase CDP facilitator auth (free tier: 1000 settlements/mo). |
| `PAYEE_SOLANA_ADDRESS` | no | Also accept USDC on Solana mainnet (pre-fund with tiny USDC first). |
| `PUBLIC_BASE_URL` | no | Public URL for the `/.well-known/x402` discovery manifest. |
| `BASE_RPC_URL` | no | Override Base mainnet RPC (default: publicnode → mainnet.base.org → 1rpc.io failover). |
| `LEDGER_DB_PATH` | no | SQLite settlement ledger (default `payments.db`). |

## Project layout

```
app/
  config.py     env-driven settings
  pricing.py    price list ($0.50 / $0.25) + discovery metadata
  routes.py     endpoint handlers (no payment code)
  payment.py    x402 middleware (402 / verify / settle / ledger) — from starter
  ledger.py     SQLite settlement log — from starter
  discovery.py  /.well-known/x402 manifest + Bazaar — from starter
  paywall.py    no-JS browser paywall page — from starter
  dd/
    rpc.py        read-only Base mainnet RPC + minimal ABI decoding
    sources.py    keyless APIs: honeypot.is, Dexscreener, Blockscout
    quick.py      contract risk screen logic
    approvals.py  wallet approval audit logic
tests/          pytest (network-free unit tests)
```

## Go-live checklist (mainnet)

1. **CDP API key** — sign up at portal.cdp.coinbase.com (free tier), set `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET`. ⬅ needs Beck
2. **Payee address** — Beck designates which Base wallet receives USDC; set `PAYEE_EVM_ADDRESS`. ⬅ needs Beck (public address only — server never holds a key)
3. **Hosting** — pick where this runs 24/7 (VPS / Railway / Fly / etc.) with `PUBLIC_BASE_URL` set. ⬅ needs Beck
4. Flip `X402_TESTNET=false`, run one real $0.50 self-test, list on CDP Bazaar via `docs/DISCOVERY.md`.

## Security notes

- Server is receive-only: it never holds a private key. Do not add one.
- Never commit `.env`. Never put a real key in scripts or shell history.
- `app/payment.py` is third-party starter code — skimmed, no exfiltration/obfuscation found, but re-audit before mainnet.
- Test client (`scripts/pay_example.py`) uses a throwaway testnet key only.

## MCP server (agent-to-agent distribution)

`mcp_server.py` exposes the paid endpoints as installable MCP tools so other
AI agents (Claude Code, Cursor, Claude Desktop) can call Zuck's DD service
in natural language:

    claude mcp add zuck-dd -- /path/to/.venv/bin/python /path/to/mcp_server.py

Tools: `dd_quick_screen` ($0.50), `dd_approvals_audit` ($0.25), `dd_ping` (free).
On a 402 the server signs an EIP-3009 USDC authorization with `DD_PAYER_KEY`
and retries automatically; without a key it returns the price/pay-to info
instead of failing. `DD_API_URL` points it at the API (default
http://localhost:8000). Pattern learned from QuoStr's QUOSTR Agent Tools
and ortegarod/onchain-agent's x402 client (2026-09-18).
