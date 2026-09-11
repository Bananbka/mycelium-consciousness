import sys

from celery_worker.app import app

__all__ = ["app"]


def main() -> None:
    argv = sys.argv[1:] or ["worker", "--loglevel=INFO"]
    app.worker_main(argv=["celery", *argv])
