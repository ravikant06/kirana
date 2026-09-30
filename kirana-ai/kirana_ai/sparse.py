"""
Step 3b: BM25 as a sparse vector.

A dense vector says what a chunk *means*. A sparse vector says which *words*
it literally contains, and how much each one matters. Dense retrieval is weak
exactly where words matter most — identifiers like `Retry-After` or
`payment_status` carry little meaning but are precisely what a user typed.

A sparse vector is just {token -> weight} with the zeros left out:

    "order-service runs 4 replicas"
      -> {order-service: 1.4, order: 1.4, service: 1.4, runs: 1.2, ...}

BM25 decides those weights. Its formula has three parts:

  tf                      how often the term appears in this chunk
  saturation (K1)         the 5th mention of a word adds less than the 2nd
  length norm (B)         a hit in a short chunk counts more than in a long one

        weight = tf * (K1 + 1) / (tf + K1 * (1 - B + B * len / avg_len))

The fourth part of BM25, IDF (rare words count more), is deliberately absent
here: Qdrant computes it at query time via `Modifier.IDF`. IDF depends on the
whole corpus, so if we baked it in at ingest, adding one document would
invalidate every vector already stored.
"""
import re
import zlib
from collections import Counter

from qdrant_client.http import models

from kirana_ai import config

# Keep dotted / underscored / hyphenated identifiers whole: `payment.failed`,
# `out_of_stock_skus`, `order-service` each match as one token.
_TOKEN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_SPLIT = re.compile(r"[._-]")


def tokenize(text: str) -> list[str]:
    """
    Lowercase into tokens, keeping compound identifiers AND their parts.

    `payment.failed` yields ["payment.failed", "payment", "failed"], so a
    query for either the full identifier or one word still matches.
    """
    tokens: list[str] = []
    for match in _TOKEN.findall(text.lower()):
        tokens.append(match)
        parts = [p for p in _SPLIT.split(match) if p]
        if len(parts) > 1:
            tokens.extend(parts)
    return tokens


def _index(token: str) -> int:
    """
    Qdrant sparse vectors are indexed by integer, so tokens are hashed.

    crc32, not Python's hash(): the built-in is salted per process, which
    would give a different index for the same word on every run.
    """
    return zlib.crc32(token.encode("utf-8")) % (2**31)


def _to_sparse(weights: dict[str, float]) -> models.SparseVector:
    return models.SparseVector(
        indices=[_index(token) for token in weights],
        values=list(weights.values()),
    )


def encode_documents(texts: list[str]) -> list[models.SparseVector]:
    """
    BM25-weight every chunk. Needs the whole corpus at once, because the
    length-normalisation term compares each chunk to the average length.
    """
    tokenised = [tokenize(text) for text in texts]
    lengths = [len(tokens) for tokens in tokenised]
    avg_len = (sum(lengths) / len(lengths)) if lengths else 1.0

    vectors = []
    for tokens, length in zip(tokenised, lengths):
        weights = {}
        for token, tf in Counter(tokens).items():
            denominator = tf + config.BM25_K1 * (
                1 - config.BM25_B + config.BM25_B * length / avg_len
            )
            weights[token] = tf * (config.BM25_K1 + 1) / denominator
        vectors.append(_to_sparse(weights))
    return vectors


def encode_query(text: str) -> models.SparseVector:
    """
    Queries are weighted 1.0 per distinct term.

    Scoring is the dot product of query and document vectors, so the document
    side already carries the BM25 weight; the query side only needs to say
    "this term is present". Qdrant then multiplies in IDF.
    """
    tokens = set(tokenize(text))
    return _to_sparse({token: 1.0 for token in tokens})
