"""
Knowledge-base documents: the API side (Phase 2 M2).

    create_upload()  insert a `pending` row, sign a POST policy for the browser
    list_documents() what the admin tab shows
    delete_document() remove the object; the delete *event* finishes the job

The upload never passes through this service: the browser posts the file straight
to MinIO (Kirana's D7 pattern). What this service controls is the signed policy,
and the policy pins everything the ingest worker will later rely on:

    key            = {document_id}/{file_name}   -> the id survives into delete events
    Content-Type   = the declared type           -> PDF, Markdown or plain text only
    size           = 1 byte .. KB_MAX_UPLOAD_BYTES
    x-amz-meta-*   = document id, title, type, uploaded-by
                     -> copied by MinIO into its Kafka event (checked in M1)

Change any of those form fields in the browser and MinIO rejects the upload, so the
metadata in the event is what this service signed — not whatever a client sent.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from minio.datatypes import PostPolicy
from sqlalchemy import select

from kirana_ai import config, storage
from kirana_ai.db import session_scope
from kirana_ai.db.models import DocStatus, DocType, Document
from kirana_ai.errors import UpstreamUnavailable

# The only formats the worker can parse (M3).
CONTENT_TYPES = {"application/pdf": ".pdf", "text/markdown": ".md", "text/plain": ".txt"}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class DocumentNotFound(Exception):
    pass


def safe_file_name(name: str) -> str:
    """'Returns Policy (v2).pdf' -> 'Returns-Policy-v2-.pdf': safe in a URL, a key and a header."""
    cleaned = _UNSAFE.sub("-", name.strip()).strip("-.") or "document"
    return cleaned[-120:]


def create_upload(title: str, doc_type: DocType, file_name: str, content_type: str,
                  size_bytes: int, uploaded_by: str) -> tuple[Document, dict]:
    """Insert a pending document and return it with the browser's upload ticket."""
    doc_id = uuid.uuid4()
    key = f"{doc_id}/{safe_file_name(file_name)}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.KB_UPLOAD_LINK_MINUTES)

    # The title may contain any characters (ह, ₹, quotes); HTTP header values may not,
    # so it travels URL-encoded and the worker decodes it. The others are already safe.
    metadata = {
        "x-amz-meta-document-id": str(doc_id),
        "x-amz-meta-title": quote(title, safe=""),
        "x-amz-meta-doc-type": doc_type.value,
        "x-amz-meta-uploaded-by": uploaded_by,
    }
    policy = PostPolicy(config.KB_BUCKET, expires_at)
    policy.add_equals_condition("key", key)
    policy.add_equals_condition("Content-Type", content_type)
    policy.add_content_length_range_condition(1, config.KB_MAX_UPLOAD_BYTES)
    for field, value in metadata.items():
        policy.add_equals_condition(field, value)
    try:
        signed = storage.client().presigned_post_policy(policy)
    except Exception as exc:
        raise UpstreamUnavailable("minio", f"cannot sign the upload policy: {exc}") from exc

    # MinIO returns only the signature fields (policy, x-amz-*). Every field the policy
    # names must be posted too, or MinIO rejects the upload: add them (as Kirana's D7 does).
    form_fields = {"key": key, "Content-Type": content_type, **metadata, **signed}

    with session_scope() as session:
        doc = Document(id=doc_id, title=title, doc_type=doc_type, file_name=file_name,
                       object_key=key, content_type=content_type, size_bytes=size_bytes,
                       status=DocStatus.PENDING, uploaded_by=uploaded_by,
                       upload_expires_at=expires_at)
        session.add(doc)

    ticket = {
        "document_id": doc_id,
        "object_key": key,
        "upload_url": f"{config.MINIO_PUBLIC_URL}/{config.KB_BUCKET}",
        "form_fields": form_fields,
        "expires_at": expires_at,
    }
    return doc, ticket


def list_documents(limit: int = 200) -> list[Document]:
    """Everything not yet deleted, most recently changed first."""
    with session_scope() as session:
        return list(session.scalars(
            select(Document).where(Document.status != DocStatus.DELETED)
            .order_by(Document.updated_at.desc()).limit(limit)
        ))


def delete_document(doc_id: uuid.UUID) -> Document:
    """
    Remove the document's file. Its vectors go when the delete event is processed (M3).

    The file is always removed, even for a `pending` document: pending only means no
    upload event has been processed yet, not that no file exists (the browser may have
    uploaded it a second ago). Removing a missing object is harmless in S3/MinIO.

    A `pending` document has nothing indexed, so it is marked deleted at once; the worker
    must then ignore any late event for it. Anything else becomes `deleting` until the
    worker has removed its chunks from Qdrant.
    """
    with session_scope() as session:
        doc = session.get(Document, doc_id)
        if doc is None or doc.status == DocStatus.DELETED:
            raise DocumentNotFound(str(doc_id))
        object_key = doc.object_key
        doc.status = DocStatus.DELETED if doc.status == DocStatus.PENDING else DocStatus.DELETING

    try:
        storage.client().remove_object(config.KB_BUCKET, object_key)
    except Exception as exc:
        raise UpstreamUnavailable("minio", f"cannot delete {object_key}: {exc}") from exc
    return doc
