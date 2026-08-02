"""RQ queue wiring and the honest queue_position helper (§5).

Worker concurrency is 1 (§5); reports run one at a time. RQ has no built-in
"position in queue" API, so we derive it from the queue's ordered job ids:
``queue.job_ids.index(rq_job_id)``. Returns ``None`` once the job is no longer
queued (running/finished/failed/unknown) — covered by a unit test because §5
promises this field and it must not silently break.
"""

from __future__ import annotations

import os

from redis import Redis
from rq import Queue

QUEUE_NAME = "reports"
JOB_TIMEOUT = 15 * 60  # 15 minutes (§5): a crashed job must not wedge the queue.

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def get_redis() -> Redis:
    """Redis connection from ``REDIS_URL``."""
    return Redis.from_url(REDIS_URL)


def get_queue(connection: Redis | None = None) -> Queue:
    """The reports queue (single worker, 15-min job timeout)."""
    return Queue(
        QUEUE_NAME,
        connection=connection or get_redis(),
        default_timeout=JOB_TIMEOUT,
    )


def queue_position(queue: Queue, rq_job_id: str) -> int | None:
    """Number of jobs ahead of ``rq_job_id`` while it is still queued (§5).

    Returns 0 if it is at the front, N if N jobs precede it, or ``None`` if the
    job is no longer in the queue (started, finished, failed, or unknown id).
    """
    try:
        return queue.job_ids.index(rq_job_id)
    except ValueError:
        return None
