try:
    from .celery import app as celery_app
except ImportError:  # the desktop app runs jobs in a background thread and ships without Celery
    celery_app = None

__all__ = ("celery_app",)
