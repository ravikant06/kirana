"""
MinIO for knowledge-base documents: the bucket, and the rule that turns its changes into Kafka events.

Two halves of one mechanism, configured in two places:

  - the MinIO *server* knows a Kafka target, id "KB" (infra/docker-compose.yml:
    brokers, topic, queue_dir). On its own it sends nothing.
  - the *bucket* gets a notification rule: "for object created / removed in
    kb-docs, send to arn:minio:sqs::KB:kafka". Set here, idempotently.

After that, every upload and delete becomes a record on kb.documents.v1, with the
object's x-amz-meta-* user metadata inside the event.
"""
from functools import cache

from minio import Minio
from minio.notificationconfig import NotificationConfig, QueueConfig

from kirana_ai import config
from kirana_ai.errors import UpstreamUnavailable

KB_EVENTS = ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]


@cache
def client() -> Minio:
    return Minio(config.MINIO_ENDPOINT, access_key=config.MINIO_ACCESS_KEY,
                 secret_key=config.MINIO_SECRET_KEY, secure=False, region=config.MINIO_REGION)


def ensure_kb_bucket() -> dict[str, str]:
    """Create the kb-docs bucket and its event rule if missing. Returns what happened."""
    mc = client()
    outcome = {}
    try:
        if mc.bucket_exists(config.KB_BUCKET):
            outcome["bucket"] = f"{config.KB_BUCKET} already exists"
        else:
            mc.make_bucket(config.KB_BUCKET)
            outcome["bucket"] = f"{config.KB_BUCKET} created"

        rule = QueueConfig(events=KB_EVENTS, config_id="kb-documents-to-kafka", queue=config.KB_EVENTS_ARN)
        current = mc.get_bucket_notification(config.KB_BUCKET)
        if any(q.queue == config.KB_EVENTS_ARN for q in current.queue_config_list):
            outcome["events"] = "rule already set"
        else:
            # Replaces the bucket's notification config: kb-docs has no other rules.
            mc.set_bucket_notification(config.KB_BUCKET, NotificationConfig(queue_config_list=[rule]))
            outcome["events"] = f"{', '.join(KB_EVENTS)} -> {config.KB_EVENTS_ARN}"
    except Exception as exc:
        raise UpstreamUnavailable(
            "minio",
            f"{type(exc).__name__}: {exc}. Is MinIO running with the Kafka target "
            "(MINIO_NOTIFY_KAFKA_*_KB in infra/docker-compose.yml)?",
        ) from exc
    return outcome
