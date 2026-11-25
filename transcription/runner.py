"""Start a transcription job in the background.

On a server (TASK_RUNNER=celery) jobs go to the Celery worker.
On a local PC (TASK_RUNNER=thread) a small queue inside the web process runs them
one after another, so nothing else needs to be installed.
"""
import logging
import queue
import threading

from django.conf import settings
from django.db import close_old_connections

log = logging.getLogger(__name__)

_queue: "queue.Queue[tuple[int, str]]" = queue.Queue()
_workers: list[threading.Thread] = []
_lock = threading.Lock()
RUNNING: set[int] = set()  # job ids queued or running in this process
QUEUED: set[int] = set()
DELAYED: set[int] = set()  # waiting for an automatic retry


def _worker():
    from .tasks import process_job, translate_job

    while True:
        job_id, kind = _queue.get()
        QUEUED.discard(job_id)
        RUNNING.add(job_id)
        try:
            close_old_connections()
            (translate_job if kind == "translate" else process_job)(job_id)
        except Exception:
            log.exception("Background job %s crashed", job_id)
        finally:
            RUNNING.discard(job_id)
            close_old_connections()
            _queue.task_done()


def _ensure_workers():
    with _lock:
        alive = [w for w in _workers if w.is_alive()]
        _workers[:] = alive
        for n in range(max(1, settings.LOCAL_WORKERS) - len(alive)):
            t = threading.Thread(target=_worker, name=f"transcribe-worker-{n}", daemon=True)
            t.start()
            _workers.append(t)


def enqueue(job_id: int, kind: str = "transcribe", delay: float = 0):
    """kind: "transcribe" (full job) or "translate" (add English to a finished job)."""
    if settings.TASK_RUNNER == "celery":
        from .tasks import process_job, translate_job

        task = translate_job if kind == "translate" else process_job
        task.apply_async(args=[job_id], countdown=delay or None)
        return
    if delay:
        DELAYED.add(job_id)

        def later():
            DELAYED.discard(job_id)
            enqueue(job_id, kind)

        timer = threading.Timer(delay, later)
        timer.daemon = True
        timer.start()
        return
    _ensure_workers()
    QUEUED.add(job_id)
    _queue.put((job_id, kind))


def is_alive_here(job_id: int) -> bool:
    """For the thread runner: is this job still queued or running in this process?"""
    return job_id in RUNNING or job_id in QUEUED or job_id in DELAYED
