"""Kafka adapters used by both downstream analysis services."""

from __future__ import annotations

import json


class MessageDecodeError(ValueError):
    """A consumed value is not a JSON object event."""


def create_consumer(topic: str, bootstrap_servers: str, group_id: str):
    from kafka import KafkaConsumer

    return KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        enable_auto_commit=False,
        group_id=group_id,
        auto_offset_reset="latest",
        max_poll_records=10,
    )


def create_producer(bootstrap_servers: str):
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        api_version=(2, 4, 0),
        acks="all",
        retries=5,
        value_serializer=lambda value: json.dumps(value).encode("utf-8"),
    )


def decode_event(value: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MessageDecodeError(str(error)) from error
    if not isinstance(decoded, dict):
        raise MessageDecodeError("Kafka value must be a JSON object")
    return decoded


def publish(
    producer,
    topic: str,
    payload: dict[str, object],
    *,
    timeout_seconds: float,
):
    future = producer.send(topic, value=payload)
    return future.get(timeout=timeout_seconds)


def commit_offsets(consumer, offsets: dict[tuple[str, int], int]) -> None:
    from kafka import TopicPartition
    from kafka.structs import OffsetAndMetadata

    committed = {}
    for (topic, partition), offset in offsets.items():
        try:
            metadata = OffsetAndMetadata(offset, "", -1)
        except TypeError:
            # kafka-python before leader-epoch support used the two-field tuple.
            metadata = OffsetAndMetadata(offset, "")
        committed[TopicPartition(topic, partition)] = metadata
    consumer.commit(offsets=committed)
