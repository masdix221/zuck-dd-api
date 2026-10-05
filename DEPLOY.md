# Deploy Zuck's x402 DD API — Runbook
## Cost: $0 (Render free tier, no credit card required)

### What this is
A pay-per-call API that sells memecoin risk screens ($0.50/call) and wallet
approval audits ($0.25/call) over x402 (HTTP 402) on Base Sepolia testnet.
Fully built, 35/35 tests passing. Code: https://github.com/masdix221/zuck-dd-api

### Deploy steps (~5 minutes, browser required)

1. **Sign up at https://render.com** — free account, no credit card needed.
2. **New > Web Service** > Connect your GitHub account > select `masdix221/zuck-dd-api`.
3. Render will detect `render.yaml` (Blueprint). If prompted, confirm:
   - Runtime: **Docker**
   - Plan: **Free**
   - Health check path: `/v1/ping`
4. **Environment variables** (most are pre-set in render.yaml — verify these):
   | Key | Value |
   |-----|-------|
   | `X402_TESTNET` | `true` |
   | `X402_DIRECT_PAY` | `false` |
   | `PAYEE_EVM_ADDRESS` | `0xc8bdd7de62f6555beF98C69eeaB1781ae3D68590` |
   | `PUBLIC_BASE_URL` | `https://<your-render-url>.onrender.com` ← update AFTER deploy with the real URL |
   | `LEDGER_DB_PATH` | `/tmp/payments.db` |
   | `PORT` | `8000` |
5. Click **Deploy**. First build takes ~3-5 minutes.
6. After deploy, update `PUBLIC_BASE_URL` to the actual Render URL and redeploy
   (or leave it — the API works without it, it just won't advertise absolute URLs).

### Verify it works
- `GET https://<url>/v1/ping` → `{"status":"ok","service":"zuck-dd-api"}`
- `GET https://<url>/.well-known/x402` → discovery manifest (base-sepolia, $0.50/$0.25)
- `POST https://<url>/dd/quick` with `{"address":"0x..."}` → **HTTP 402** with x402 payment terms
  (402 = working correctly — it means "pay to use this")

### Free tier limits (Render)
- 750 hours/month (enough for 24/7)
- Sleeps after 15 min of inactivity — first request after sleep takes ~30s to wake
- 512 MB RAM, shared CPU — plenty for this API
- No custom domain on free tier (uses *.onrender.com)

### Switching to mainnet later (real USDC payments)
Two options, both need Beck's explicit go-ahead:

**Option A — Direct-pay (recommended, no API keys):**
1. In Render dashboard, set `X402_TESTNET=false` and `X402_DIRECT_PAY=true`
2. Keep `PAYEE_EVM_ADDRESS` as the gig wallet (or change to whichever wallet should receive)
3. Redeploy. Clients pay USDC directly on-chain to the payee address; the server
   verifies via public RPC. **The server never holds private keys — receive-only.**

**Option B — CDP facilitator:**
1. Sign up at https://portal.cdp.coinbase.com (free tier: 1000 settlements/month)
2. Create an API key, store `CDP_API_KEY_ID` and `CDP_API_KEY_SECRET` in Render env vars
3. Set `X402_TESTNET=false`, `X402_DIRECT_PAY=false`
4. Redeploy.

### What NOT to do
- Never put a private key in env vars. The payee address is public — that's all the server needs.
- Don't expect revenue yet. The x402 machine-payment ecosystem is unproven; this is
  positioned infrastructure, not a revenue plan. (See BUILD_REPORT.md demand assessment.)
