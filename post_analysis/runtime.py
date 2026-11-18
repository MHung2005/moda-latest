"""Bounded process supervision and reliable Kafka offset ownership."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import multiprocessing
from multiprocessing.connection import Connection
import time
import traceback
from typing import Callable

from .config import KafkaConfig, WorkerConfig
from .logs import configure_logging
from .kafka import (
    commit_offsets,
    create_consumer,
    create_producer,
    decode_event,
    publish,
)


logger = logging.getLogger(__name__)
PartitionKey = tuple[str, int]
WorkerHandler = Callable[[object, dict[str, object], object], None]
RoutingKey = Callable[[dict[str, object]], str | None]
PROCESS_CONTEXT = multiprocessing.get_context("spawn")


@dataclass(frozen=True)
class SourceRecord:
    topic: str
    partition: int
    offset: int

    @property
    def partition_key(self) -> PartitionKey:
        return self.topic, self.partition


@dataclass
class WorkItem:
    source: SourceRecord
    event: dict[str, object]
    attempt: int = 1
    ready_at: float = 0.0


@dataclass
class ActiveJob:
    item: WorkItem
    process: object
    result: Connection
    stop_event: object
    started_at: float
    routing_key: str | None


class OffsetTracker:
    """Expose only the highest contiguous completed offset for each partition."""

    def __init__(self) -> None:
        self._offsets: dict[PartitionKey, OrderedDict[int, bool]] = {}

    def register(self, source: SourceRecord) -> None:
        offsets = self._offsets.setdefault(source.partition_key, OrderedDict())
        offsets.setdefault(source.offset, False)

    def complete(self, source: SourceRecord) -> None:
        try:
            self._offsets[source.partition_key][source.offset] = True
        except KeyError as error:
            raise RuntimeError(f"Unregistered Kafka offset: {source}") from error

    def take_committable(self) -> dict[PartitionKey, int]:
        committable: dict[PartitionKey, int] = {}
        empty_partitions: list[PartitionKey] = []
        for partition_key, offsets in self._offsets.items():
            next_offset = None
            while offsets:
                offset, completed = next(iter(offsets.items()))
                if not completed:
                    break
                offsets.popitem(last=False)
                next_offset = offset + 1
            if next_offset is not None:
                committable[partition_key] = next_offset
            if not offsets:
                empty_partitions.append(partition_key)
        for partition_key in empty_partitions:
            del self._offsets[partition_key]
        return committable


def _worker_entry(
    handler: WorkerHandler,
    handler_config: object,
    event: dict[str, object],
    stop_event,
    result: Connection,
) -> None:
    # Spawned children do not inherit the parent's logging setup.
    configure_logging()
    try:
        handler(handler_config, event, stop_event)
        result.send((True, ""))
    except Exception:
        result.send((False, traceback.format_exc(limit=12)))
    finally:
        result.close()


def _start_job(
    handler: WorkerHandler,
    handler_config: object,
    item: WorkItem,
    routing_key: str | None,
) -> ActiveJob:
    parent_result, child_result = PROCESS_CONTEXT.Pipe(duplex=False)
    stop_event = PROCESS_CONTEXT.Event()
    process = PROCESS_CONTEXT.Process(
        target=_worker_entry,
        args=(handler, handler_config, item.event, stop_event, child_result),
        name=f"analysis-{item.source.partition}-{item.source.offset}",
    )
    process.start()
    child_result.close()
    return ActiveJob(
        item=item,
        process=process,
        result=parent_result,
        stop_event=stop_event,
        started_at=time.monotonic(),
        routing_key=routing_key,
    )


def _stop_job(job: ActiveJob, grace_seconds: float) -> None:
    job.stop_event.set()
    job.process.join(timeout=grace_seconds)
    if job.process.is_alive():
        job.process.terminate()
        job.process.join()
    job.result.close()


def _close_finished_job(job: ActiveJob, grace_seconds: float) -> None:
    job.process.join(timeout=grace_seconds)
    if job.process.is_alive():
        logger.warning("Worker sent a result but did not exit; terminating it")
        job.process.terminate()
        job.process.join()
    job.result.close()


def _receive_outcome(job: ActiveJob) -> tuple[bool, str]:
    try:
        return job.result.recv()
    except (EOFError, OSError) as error:
        return (
            False,
            "Worker result channel closed unexpectedly "
            f"(exit code {job.process.exitcode}): {error}",
        )


def _failure_payload(
    service_name: str,
    item: WorkItem,
    reason: str,
) -> dict[str, object]:
    return {
        "service": service_name,
        "source_topic": item.source.topic,
        "source_partition": item.source.partition,
        "source_offset": item.source.offset,
        "trigger_code": item.event.get("trigger_code"),
        "attempts": item.attempt,
        "error": reason[-2_000:],
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }


def describe(item: WorkItem) -> str:
    """One-line identity of an event for logs: offset, lane, section and plate."""
    event = item.event
    section = str(event.get("section_id") or "")[:8] or "-"
    return (
        f"offset={item.source.offset} trigger={event.get('trigger_code') or '-'}"
        f" section={section} plate={event.get('detected_object_id') or '-'}"
    )


def short_reason(reason: str) -> str:
    """Last meaningful line of a traceback (the exception message)."""
    lines = [line.strip() for line in reason.strip().splitlines() if line.strip()]
    return lines[-1] if lines else reason


def _publish_failure(
    producer,
    service_name: str,
    kafka: KafkaConfig,
    item: WorkItem,
    reason: str,
) -> None:
    publish(
        producer,
        kafka.dead_letter_topic,
        _failure_payload(service_name, item, reason),
        timeout_seconds=kafka.publish_timeout_seconds,
    )


def run_worker_service(
    *,
    service_name: str,
    kafka: KafkaConfig,
    worker: WorkerConfig,
    handler: WorkerHandler,
    handler_config: object,
    routing_key: RoutingKey | None = None,
    supersede_same_key: bool = False,
) -> None:
    """Run bounded child processes and commit only successfully finalized events."""
    consumer = create_consumer(kafka.input_topic, kafka.bootstrap_servers, kafka.group_id)
    failure_producer = create_producer(kafka.bootstrap_servers)
    tracker = OffsetTracker()
    active: dict[tuple[PartitionKey, int], ActiveJob] = {}
    pending: deque[WorkItem] = deque()

    def job_id(item: WorkItem) -> tuple[PartitionKey, int]:
        return item.source.partition_key, item.source.offset

    def complete_failure(item: WorkItem, reason: str) -> None:
        _publish_failure(failure_producer, service_name, kafka, item, reason)
        tracker.complete(item.source)
        logger.info(
            "%s %s written to %s: %s",
            service_name, describe(item), kafka.dead_letter_topic, short_reason(reason),
        )

    def start(item: WorkItem, item_key: str | None) -> None:
        job = _start_job(handler, handler_config, item, item_key)
        active[job_id(item)] = job
        logger.info(
            "%s start %s attempt=%s/%s pid=%s",
            service_name, describe(item), item.attempt, worker.max_attempts, job.process.pid,
        )

    def retry_or_fail(item: WorkItem, reason: str) -> None:
        if item.attempt < worker.max_attempts:
            logger.warning(
                "%s %s attempt %s/%s failed: %s",
                service_name, describe(item), item.attempt, worker.max_attempts, short_reason(reason),
            )
            item.attempt += 1
            delay = worker.retry_backoff_seconds * (2 ** (item.attempt - 2))
            item.ready_at = time.monotonic() + delay
            pending.append(item)
            logger.info("%s %s retry in %.1fs", service_name, describe(item), delay)
            return
        logger.error(
            "%s %s failed permanently after %s attempt(s): %s\n%s",
            service_name, describe(item), item.attempt, short_reason(reason), reason.rstrip(),
        )
        complete_failure(item, reason)

    def commit_completed() -> None:
        offsets = tracker.take_committable()
        if offsets:
            commit_offsets(consumer, offsets)

    def key_for(event: dict[str, object]) -> str | None:
        return None if routing_key is None else routing_key(event)

    try:
        while True:
            now = time.monotonic()

            for identifier, job in list(active.items()):
                outcome: tuple[bool, str] | None = None
                if job.result.poll():
                    outcome = _receive_outcome(job)
                elif now - job.started_at > worker.event_timeout_seconds:
                    _stop_job(job, worker.cancellation_grace_seconds)
                    del active[identifier]
                    retry_or_fail(
                        job.item,
                        f"Event processing deadline exceeded ({worker.event_timeout_seconds:.0f}s);"
                        " see the worker's last [step] log line for where it hung",
                    )
                    continue
                elif not job.process.is_alive():
                    if job.result.poll(0.1):
                        outcome = _receive_outcome(job)
                    else:
                        outcome = (
                            False,
                            f"Worker exited without a result (exit code {job.process.exitcode})",
                        )

                if outcome is None:
                    continue
                _close_finished_job(job, worker.cancellation_grace_seconds)
                del active[identifier]
                succeeded, error = outcome
                if succeeded:
                    tracker.complete(job.item.source)
                    logger.info(
                        "%s done %s in %.1fs",
                        service_name, describe(job.item), time.monotonic() - job.started_at,
                    )
                else:
                    retry_or_fail(job.item, error)

            for _ in range(len(pending)):
                if len(active) >= worker.max_workers:
                    break
                item = pending.popleft()
                if item.ready_at > now:
                    pending.append(item)
                    continue
                item_key = key_for(item.event)
                if item_key is not None and any(
                    job.routing_key == item_key for job in active.values()
                ):
                    pending.append(item)
                    continue
                start(item, item_key)

            commit_completed()

            capacity = worker.max_workers - len(active) - len(pending)
            can_poll = supersede_same_key or capacity > 0
            if not can_poll:
                time.sleep(0.05)
                continue

            max_records = 10 if supersede_same_key else max(1, capacity)
            batches = consumer.poll(timeout_ms=250, max_records=max_records)
            for messages in batches.values():
                for message in messages:
                    source = SourceRecord(message.topic, message.partition, message.offset)
                    tracker.register(source)
                    try:
                        event = decode_event(message.value)
                    except Exception as error:
                        item = WorkItem(source=source, event={})
                        complete_failure(item, f"Invalid Kafka message: {error}")
                        continue

                    item = WorkItem(source=source, event=event)
                    logger.info("%s received %s", service_name, describe(item))
                    item_key = key_for(event)
                    if routing_key is not None and item_key is None:
                        complete_failure(item, "Unsupported or missing trigger_code")
                        continue

                    if supersede_same_key and item_key is not None:
                        for identifier, existing in list(active.items()):
                            if existing.routing_key != item_key:
                                continue
                            logger.warning(
                                "%s %s superseded by offset %s (same %s); cancelling it",
                                service_name, describe(existing.item), source.offset, item_key,
                            )
                            _stop_job(existing, worker.cancellation_grace_seconds)
                            del active[identifier]
                            complete_failure(
                                existing.item,
                                f"Superseded by offset {source.offset} for {item_key}",
                            )
                        retained: deque[WorkItem] = deque()
                        while pending:
                            queued = pending.popleft()
                            if key_for(queued.event) == item_key:
                                complete_failure(
                                    queued,
                                    f"Superseded by offset {source.offset} for {item_key}",
                                )
                            else:
                                retained.append(queued)
                        pending = retained

                    if len(active) < worker.max_workers:
                        start(item, item_key)
                    else:
                        pending.append(item)
                        logger.info(
                            "%s queued %s (%s worker(s) busy)", service_name, describe(item), len(active)
                        )
            commit_completed()
    finally:
        for job in active.values():
            _stop_job(job, worker.cancellation_grace_seconds)
        consumer.close()
        failure_producer.close()
