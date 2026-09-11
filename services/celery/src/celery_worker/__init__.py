from celery_worker.app import app

__all__ = ["app"]


def main() -> None:
    app.start()
