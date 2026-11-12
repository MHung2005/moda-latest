"""Kafka consumer that stores recognition results in the results SQLite DB.

Runs as its own container (`python -m moda_ui record`) so that restarting the web UI never
drops events: offsets are committed only after the rows are written.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time

from . import results


logger = logging.getLogger(__name__)

DEFAULT_TOPICS = {
    "vehicle": "VehicleDetection_Gate1",
    "analysis": "AnalysisResult_Gate1",
    "failure": "AnalysisFailure_Gate1",
}


def _topics() -> dict[str, str]:
    return {
        "vehicle": os.environ.get("MODA_RESULTS_VEHICLE_TOPIC", DEFAULT_TOPICS["vehicle"]),
        "analysis": os.environ.get("MODA_RESULTS_ANALYSIS_TOPIC", DEFAULT_TOPICS["analysis"]),
        "failure": os.environ.get("MODA_RESULTS_FAILURE_TOPIC", DEFAULT_TOPICS["failure"]),
    }


def _decode(raw: bytes | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def run() -> int:
    from kafka import KafkaConsumer
    from kafka.errors import KafkaError

    bootstrap = os.environ.get("MODA_KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not bootstrap:
        logger.error("MODA_KAFKA_BOOTSTRAP_SERVERS is not set")
        return 1
    group_id = os.environ.get("MODA_RESULTS_GROUP_ID", "moda-results-recorder")
    topics = _topics()
    kinds = {topic: kind for kind, topic in topics.items()}

    stopping = False

    def _stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    conn = results.connect()
    results.init_schema(conn)
    logger.info("Results DB: %s", results.get_settings().results_db_path)

    while not stopping:
        try:
            consumer = KafkaConsumer(
                *topics.values(),
                bootstrap_servers=[item.strip() for item in bootstrap.split(",") if item.strip()],
                group_id=group_id,
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                consumer_timeout_ms=-1,
            )
        except KafkaError as error:
            logger.warning("Kafka unavailable (%s); retrying in 5s", error)
            time.sleep(5)
            continue

        logger.info("Consuming %s from %s as group %s", list(topics.values()), bootstrap, group_id)
        try:
            while not stopping:
                batches = consumer.poll(timeout_ms=1000, max_records=200)
                if not batches:
                    continue
                stored = 0
                with results.transaction(conn):
                    for partition, messages in batches.items():
                        kind = kinds.get(partition.topic)
                        for message in messages:
                            event = _decode(message.value)
                            if event is None:
                                logger.warning("Skipping non-JSON message %s@%s", partition.topic, message.offset)
                                continue
                            if kind == "vehicle":
                                ok = results.record_vehicle(conn, event, partition.partition, message.offset)
                            elif kind == "analysis":
                                ok = results.record_analysis(conn, event)
                            else:
                                ok = results.record_failure(conn, event)
                            stored += int(ok)
                consumer.commit()
                logger.info("Stored %d message(s)", stored)
        except KafkaError as error:
            logger.warning("Kafka error (%s); reconnecting in 5s", error)
            time.sleep(5)
        finally:
            consumer.close()
    conn.close()
    logger.info("Recorder stopped")
    return 0
