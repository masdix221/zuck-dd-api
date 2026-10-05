FROM python:3.12-slim

WORKDIR /srv/dd-api

# System deps for cryptography builds (wheels usually cover this; kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir .

# Runtime config comes from the host's env vars (never baked in):
#   PAYEE_EVM_ADDRESS, X402_TESTNET, CDP_API_KEY_ID, CDP_API_KEY_SECRET,
#   PUBLIC_BASE_URL, BASE_RPC_URL, LEDGER_DB_PATH
ENV PORT=8000 \
    LEDGER_DB_PATH=/data/payments.db

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
