"""Task modules, imported here so `include` in app.py registers them all."""

from celery_worker.tasks import memory

__all__ = ["memory"]
