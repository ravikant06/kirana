"""
Command line for the AI service until the HTTP API exists (Phase 1).

    python -m kirana_ai.cli ingest              # kb/seed -> chunks -> embeddings -> Qdrant
    python -m kirana_ai.cli ingest --recreate   # drop the collection first
    python -m kirana_ai.cli ask "Can I return opened rice?"
    python -m kirana_ai.cli ask -t "..."        # print every step: prompts, filters, hits
    python -m kirana_ai.cli chat                # interactive, one question at a time
"""
import argparse
import uuid
from datetime import datetime, timezone

from kirana_ai import agent, chunker, config, embeddings, loader, sparse, trace, vector_store


def ingest(recreate: bool) -> None:
    batch_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"

    print(f"Loading documents from {config.KB_DIR} ...")
    documents = loader.load_documents(config.KB_DIR)
    chunks = chunker.chunk_documents(
        documents, config.CHUNK_SIZE, config.CHUNK_OVERLAP, batch_id=batch_id
    )

    client = vector_store.get_client()
    if recreate and vector_store.drop_collection(client):
        print(f"Dropped collection {config.COLLECTION_NAME}")

    print(f"Embedding {len(chunks)} chunks with {config.EMBEDDING_MODEL} ...")
    vectors = embeddings.embed_documents(chunks)

    sparse_vectors = None
    if config.HYBRID_SEARCH:
        # BM25 weights are computed locally — no API call, no model.
        sparse_vectors = sparse.encode_documents([c["text"] for c in chunks])

    vector_store.ensure_collection(client)
    written = vector_store.upsert_chunks(client, chunks, vectors, sparse_vectors)

    print()
    print(f"Documents loaded: {len(documents)}")
    print(f"Chunks indexed:   {written}")
    print(f"Hybrid (BM25):    {'on' if sparse_vectors else 'off'}")
    print(f"Batch id:         {batch_id}")
    print(f"Collection:       {config.COLLECTION_NAME} @ {config.QDRANT_URL}")


def _print_answer(question: str) -> None:
    chunks, final_answer, steps = agent.answer(question)

    if steps:
        print("\nTOOL CALLS:")
        for i, step in enumerate(steps, 1):
            where = ", ".join(f"{k}={v}" for k, v in sorted(step["where"].items()))
            query = f" query={step['query']!r}" if step.get("query") else ""
            print(f"  [{i}] {step['tool']}{query}")
            print(f"      filters: tenant={config.TENANT_ID}{', ' + where if where else ''}")
            print(f"      results: {step['count']}")

    if chunks:
        print("\nCHUNKS SEEN:")
        for i, c in enumerate(chunks, 1):
            where = c["source"] + (f" > {c['heading']}" if c.get("heading") else "")
            print(f"  [{i}] {where} (chunk {c['chunk_index']}, score={c['score']:.3f})")

    print("\nANSWER:")
    print(final_answer)


def main() -> None:
    parser = argparse.ArgumentParser(prog="kirana_ai.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="index the knowledge base into Qdrant")
    p_ingest.add_argument("--recreate", action="store_true",
                          help="drop the collection first (removes chunks of deleted files)")

    for name, help_text in (("ask", "answer one question"), ("chat", "interactive loop")):
        p = sub.add_parser(name, help=help_text)
        if name == "ask":
            p.add_argument("question")
        p.add_argument("-t", "--trace", action="store_true",
                       help="print every step: prompts, embeddings, filters, results")
        p.add_argument("--trace-full", action="store_true",
                       help="like --trace, without truncating long prompts")

    args = parser.parse_args()

    if args.command == "ingest":
        ingest(args.recreate)
        return

    if args.trace or args.trace_full:
        trace.enable(True, full=args.trace_full)

    if args.command == "ask":
        _print_answer(args.question)
        return

    print(f"Kirana assistant ({config.LLM_PROVIDER} / {config.GENERATION_MODEL}). "
          "Ask a question, or 'exit' to quit.")
    while True:
        question = input("\n> ").strip()
        if question.lower() in {"exit", "quit", ""}:
            break
        _print_answer(question)


if __name__ == "__main__":
    main()
