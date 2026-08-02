"""Unit tests for queue_position (§5) using fakeredis — no real Redis/worker."""

from __future__ import annotations

import fakeredis
import pytest
from rq import Queue

from app.queue import queue_position


def _noop(*args: object, **kwargs: object) -> None:
    """A trivial job target enqueued without running a worker."""
    return None


@pytest.fixture
def queue() -> Queue:
    conn = fakeredis.FakeStrictRedis()
    # is_async=False would execute immediately; we want jobs to stay queued so we
    # can inspect ordering, so keep async but never start a worker.
    return Queue("reports", connection=conn, is_async=True)


def test_queue_position_reflects_order(queue: Queue) -> None:
    job1 = queue.enqueue(_noop)
    job2 = queue.enqueue(_noop)
    job3 = queue.enqueue(_noop)

    assert queue_position(queue, job1.id) == 0
    assert queue_position(queue, job2.id) == 1
    assert queue_position(queue, job3.id) == 2


def test_queue_position_none_for_unknown_job(queue: Queue) -> None:
    queue.enqueue(_noop)
    assert queue_position(queue, "does-not-exist") is None


def test_queue_position_none_after_dequeue(queue: Queue) -> None:
    job1 = queue.enqueue(_noop)
    job2 = queue.enqueue(_noop)

    # Simulate the worker pulling job1 off the queue.
    dequeued = queue.dequeue_any([queue], timeout=None, connection=queue.connection)
    assert dequeued is not None

    assert queue_position(queue, job1.id) is None  # no longer queued
    assert queue_position(queue, job2.id) == 0  # now at the front


def test_queue_position_empty_queue(queue: Queue) -> None:
    assert queue_position(queue, "anything") is None
