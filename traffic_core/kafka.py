"""Kafka adapter creation, isolated from the detection domain logic."""

from __future__ import annotations

import json


def create_producer(bootstrap_servers: str):
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        api_version=(2, 4, 0),
        acks="all",
        retries=5,
        value_serializer=lambda value: json.dumps(value).encode("utf-8"),
    )


def publish(
    producer,
    topic: str,
    partition: int,
    payload: dict[str, object],
    *,
    timeout_seconds: float,
):
    """Publish one trigger and wait until Kafka acknowledges it.

    Returning before the broker acknowledgement would allow the detector to
    discard its track even though no downstream analysis service can see the
    event.
    """
    future = producer.send(topic=topic, partition=partition, value=payload)
    return future.get(timeout=timeout_seconds)
