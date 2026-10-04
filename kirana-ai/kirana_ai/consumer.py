"""
The Kafka consumer loop every AI worker shares (Phase 2 ingest, Phase 4 catalog).

    poll -> process(value, dead_letter) -> commit -> log one line

The rules, in one tested place instead of one copy per worker:
  - auto-commit is off; the offset is committed only after a message is fully handled
    (done, recorded as failed, or dead-lettered): at-least-once delivery;
  - a dead letter is confirmed by the broker before that commit (produce_confirmed);
  - retries happen in place (retrying()), so one key's events stay in order;
  - Ctrl-C / SIGTERM leaves the group cleanly, handing partitions to the other members.
"""
import logging
import random
import signal
import time
from collections.abc import Callable

from kirana_ai import config

log = logging.getLogger("kirana_ai.consumer")


class PermanentError(Exception):
    """Retrying cannot help: the event or the data itself is the problem."""


def retrying(fn: Callable[[], str], *, attempts: int, base_seconds: float,
             dead_letter: Callable[[str, int], None], on_failed: Callable[[str], None],
             sleep=time.sleep) -> str:
    """
    Run fn with the shared failure policy and return an outcome line:
      permanent  -> on_failed(reason), no retry
      transient  -> retry with exponential backoff and jitter; after the last attempt,
                    dead_letter(reason, attempts) then on_failed(...)
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except PermanentError as exc:
            on_failed(str(exc))
            return f"failed: {exc}"
        except Exception as exc:           # transient until proven otherwise
            if attempt == attempts:
                reason = f"{type(exc).__name__}: {exc}"
                dead_letter(reason, attempt)
                on_failed(f"Could not process after {attempt} attempts ({reason}). "
                          "The event is in the dead-letter topic; run `cli redrive` once fixed.")
                return f"dead-lettered after {attempt} attempts: {reason}"
            delay = base_seconds * 2 ** (attempt - 1) * random.uniform(0.8, 1.2)
            log.warning("attempt %d failed (%s: %s); retrying in %.1f s",
                        attempt, type(exc).__name__, exc, delay)
            sleep(delay)
    raise AssertionError("unreachable")


def consumer_config(group: str) -> dict:
    return {
        "bootstrap.servers": config.KAFKA_BOOTSTRAP,
        "group.id": group,
        "enable.auto.commit": False,             # we commit after the work, never before
        "auto.offset.reset": "earliest",         # a new group starts with events already waiting
        "partition.assignment.strategy": "cooperative-sticky",
    }


def dlt_producer():
    from confluent_kafka import Producer
    return Producer({"bootstrap.servers": config.KAFKA_BOOTSTRAP,
                     "acks": "all", "enable.idempotence": True})


def produce_confirmed(producer, topic: str, **kwargs) -> None:
    """
    Produce one message and wait until the broker has it, or raise.

    flush() returning is not proof: a failed delivery is reported only through the
    delivery callback, and a timed-out flush just returns how many are still queued.
    Callers commit an offset right after this, so "probably sent" would lose the event.
    """
    errors = []
    producer.produce(topic, on_delivery=lambda err, _msg: err and errors.append(err), **kwargs)
    remaining = producer.flush(10)
    if errors or remaining:
        raise RuntimeError(f"could not write to {topic}: {errors[0] if errors else 'timed out'}")


def run(topic: str, group: str, dlt: str, process: Callable[[bytes, Callable], str]) -> None:
    """Consume `topic` as a member of `group` until stopped. process() must handle a message completely."""
    from confluent_kafka import Consumer, KafkaError

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # One line per message is the useful signal; per-HTTP-request lines from the SDKs drown it.
    for noisy in ("httpx", "httpcore", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    consumer = Consumer(consumer_config(group))
    producer = dlt_producer()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    def on_assign(_c, partitions):
        log.info("assigned partitions %s", sorted(p.partition for p in partitions))

    def on_revoke(_c, partitions):
        log.info("revoked partitions %s", sorted(p.partition for p in partitions))

    consumer.subscribe([topic], on_assign=on_assign, on_revoke=on_revoke)
    log.info("group %s reading %s from %s", group, topic, config.KAFKA_BOOTSTRAP)
    try:
        while not stopping:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("kafka: %s", msg.error())
                continue

            def dead_letter(reason: str, attempts: int, msg=msg) -> None:
                produce_confirmed(producer, dlt, key=msg.key(), value=msg.value(), headers=[
                    ("error", reason.encode()[:1000]),
                    ("attempts", str(attempts).encode()),
                    ("original-topic", msg.topic().encode()),
                    ("original-partition", str(msg.partition()).encode()),
                    ("original-offset", str(msg.offset()).encode()),
                ])
                # If that raised, process() raises too: no commit, the worker stops, and the
                # message is read again on restart. Losing it is the one unacceptable outcome.

            started = time.perf_counter()
            outcome = process(msg.value(), dead_letter)
            consumer.commit(message=msg, asynchronous=False)
            log.info("p%d@%d %s -> %s (%d ms)", msg.partition(), msg.offset(),
                     msg.key().decode() if msg.key() else "-", outcome,
                     (time.perf_counter() - started) * 1000)
    finally:
        log.info("stopping: leaving the group")
        consumer.close()          # commits nothing extra; hands our partitions to the others
        producer.flush(10)
