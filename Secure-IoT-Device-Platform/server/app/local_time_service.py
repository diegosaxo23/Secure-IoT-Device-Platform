from __future__ import annotations

import os
import re
import time
from functools import lru_cache
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .signed_time import PROTOCOL_ID, load_signing_key, public_key_fingerprint, sign_time

NONCE_RE = re.compile(r"^[0-9a-fA-F]{32,64}$")
TIME_SIGNING_KEY_PATH = Path(os.environ.get("TIME_SIGNING_KEY_PATH", "/pki/time/time-signing.key"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").lower()

app = FastAPI(
    title="IoT Device Platform Local Time Service",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@lru_cache(maxsize=1)
def signing_key():
    return load_signing_key(TIME_SIGNING_KEY_PATH)


@app.get("/health")
def health() -> JSONResponse:
    key = signing_key()
    return JSONResponse({"status": "ok", "protocol": PROTOCOL_ID, "key_id": public_key_fingerprint(key)})


@app.get("/api/v1/time")
def signed_time(nonce: str = Query(min_length=32, max_length=64)) -> JSONResponse:
    if not NONCE_RE.fullmatch(nonce):
        raise HTTPException(status_code=400, detail="nonce must be 16-32 random bytes encoded as hexadecimal")

    # Whole seconds are sufficient for X.509 validity checks and keep the signed
    # representation deterministic across Python and the ESP32 implementation.
    unix_time = int(time.time())
    key = signing_key()
    return JSONResponse(
        {
            "protocol": PROTOCOL_ID,
            "nonce": nonce.lower(),
            "unix_time": unix_time,
            "signature": sign_time(key, nonce.lower(), unix_time),
            "key_id": public_key_fingerprint(key),
        },
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


if __name__ == "__main__":
    uvicorn.run(
        "app.local_time_service:app",
        host="0.0.0.0",
        port=8091,
        log_level=LOG_LEVEL,
        workers=1,
    )
