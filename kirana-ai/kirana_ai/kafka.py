"""
Kafka for the AI service: the topics it owns, and a way to look at their events.

Kirana's broker has auto-create switched off, so topics exist only because someone
created them on purpose. The AI service owns the knowledge-base topics (MinIO
produces into them, our worker consumes), so it creates them here, the way
Kirana's KafkaConfig creates orders.v1. Running this twice changes nothing.
"""
import json
import uuid
from collections.abc import Iterator

from confluent_kafka import Consumer, KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from kirana_ai import config
from kirana_ai.errors import UpstreamUnavailable

# One broker on a laptop, so one copy of each partition.
REPLICATION = 1


def _admin() -> AdminClient:
    return AdminClient({"bootstrap.servers": config.KAFKA_BOOTSTRAP})


def ensure_topics() -> dict[str, str]:
    """Create kb.documents.v1 and its dead-letter topic if missing. Returns topic -> what happened."""
    wanted = [config.KB_TOPIC, config.KB_DLT]
    # Keep a reference until the futures resolve: if the client is garbage-collected
    # first, librdkafka destroys the connection and every future fails with _DESTROY.
    admin = _admin()
    futures = admin.create_topics(
        [NewTopic(t, num_partitions=config.KAFKA_PARTITIONS, replication_factor=REPLICATION)
         for t in wanted],
        request_timeout=10,
    )
    outcome = {}
    for topic, future in futures.items():
        try:
            future.result()
            outcome[topic] = "created"
        except KafkaException as exc:
            if exc.args[0].code() == KafkaError.TOPIC_ALREADY_EXISTS:
                outcome[topic] = "already exists"
            else:
                raise UpstreamUnavailable("kafka", f"cannot create {topic}: {exc}") from exc
    return outcome


def tail(topic: str, max_messages: int = 20, timeout_s: float = 5.0) -> Iterator[dict]:
    """
    Read a topic from the beginning and yield its records, without committing anything.

    A throwaway consumer group (random name, auto-commit off) means this never moves
    the offsets of the real worker group: looking at events never consumes them.
    """
    consumer = Consumer({
        "bootstrap.servers": config.KAFKA_BOOTSTRAP,
        "group.id": f"kirana-ai-tail-{uuid.uuid4().hex[:8]}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })
    consumer.subscribe([topic])
    try:
        seen = 0
        while seen < max_messages:
            msg = consumer.poll(timeout_s)
            if msg is None:
                return                       # nothing more within the timeout
            if msg.error():
                raise UpstreamUnavailable("kafka", str(msg.error()))
            seen += 1
            yield {
                "partition": msg.partition(),
                "offset": msg.offset(),
                "key": msg.key().decode() if msg.key() else None,
                "value": json.loads(msg.value()) if msg.value() else None,
            }
    finally:
        consumer.close()
