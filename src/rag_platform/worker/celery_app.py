"""Celery application.

Redis (already in the stack) as broker; NO result backend — the documents
table is the single status source of truth, and a second store that can
disagree with it is a bug generator, not a feature.
"""

from celery import Celery

from rag_platform.config import Settings, get_settings
from rag_platform.logging import configure_logging


def create_celery_app(settings: Settings) -> Celery:
    celery = Celery(
        "rag_platform",
        broker=str(settings.redis_url),
        include=["rag_platform.worker.tasks"],
    )
    celery.conf.update(
        task_ignore_result=True,  # see module docstring
        # acks_late + prefetch 1: a worker killed mid-task gets the message
        # redelivered instead of silently dropped; process() is idempotent to
        # make that at-least-once delivery safe. Prefetch 1 because embedding
        # tasks run seconds-to-minutes — hoarding queued tasks in one worker
        # starves the others.
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_always_eager=settings.celery_eager,
        task_eager_propagates=True,
        broker_connection_retry_on_startup=True,
        # keep our structlog JSON pipeline instead of Celery's root-logger hijack
        worker_hijack_root_logger=False,
    )
    return celery


settings = get_settings()
configure_logging(settings.log_level)
app = create_celery_app(settings)
