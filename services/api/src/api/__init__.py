import uvicorn

from shared.settings import settings


def main() -> None:
    reload = settings.api_reload
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=reload,
        workers=None if reload else settings.api_workers,
        log_level=settings.log_level.lower(),
    )
