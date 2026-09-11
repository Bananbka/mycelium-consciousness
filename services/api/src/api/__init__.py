import os

import uvicorn


def main() -> None:
    reload = os.getenv("API_RELOAD", "false").lower() in {"1", "true", "yes"}
    uvicorn.run(
        "api.main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=reload,
        workers=None if reload else int(os.getenv("API_WORKERS", "2")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
