from __future__ import annotations

import uvicorn

from .config import get_settings


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8443,
        log_level=settings.log_level.lower(),
        ssl_certfile=str(settings.server_cert_path),
        ssl_keyfile=str(settings.server_key_path),
        workers=1,
    )
