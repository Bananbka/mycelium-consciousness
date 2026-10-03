from pathlib import Path

import uvicorn

import shared
from shared.settings import settings


def main() -> None:
    reload = settings.api_reload
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=reload,
        # Watch only our own source, not .venv or the CWD.
        reload_dirs=[str(Path(__file__).parent), str(Path(shared.__file__).parent)]
        if reload
        else None,
        workers=None if reload else settings.api_workers,
        log_level=settings.log_level.lower(),
    )
