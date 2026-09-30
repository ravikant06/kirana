"""
Command line for the AI service until the HTTP API exists (Phase 1).

    python -m kirana_ai.cli ingest              # kb/seed -> chunks -> embeddings -> Qdrant
    python -m kirana_ai.cli ingest --recreate   # drop the collection first
    python -m kirana_ai.cli ask "Can I return opened rice?"
    python -m kirana_ai.cli ask -t "..."        # print every step: prompts, filters, hits
    python -m kirana_ai.cli chat --user 7       # a saved conversation (Postgres), with history
    python -m kirana_ai.cli chat --user 7 --thread <id>   # continue one
    python -m kirana_ai.cli threads --user 7    # list a shopper's threads

`ask` is stateless and touches no database. `chat` is the real turn: history,
thread and messages in Postgres, every LLM call recorded in ai.llm_calls.
"""
import argparse
import uuid
from datetime import datetime, timezone

import uuid as uuid_mod

from kirana_ai import agent, chat, chunker, config, embeddings, loader, sparse, trace, vector_store
from kirana_ai.llm import get_adapter


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


def _print_steps(steps: list[dict]) -> None:
    if steps:
        print("\nTOOL CALLS:")
        for i, step in enumerate(steps, 1):
            where = ", ".join(f"{k}={v}" for k, v in sorted(step["where"].items()))
            query = f" query={step['query']!r}" if step.get("query") else ""
            print(f"  [{i}] {step['tool']}{query}")
            print(f"      filters: tenant={config.TENANT_ID}{', ' + where if where else ''}")
            print(f"      results: {step['count']}")


def _print_answer(question: str) -> None:
    chunks, final_answer, steps = agent.answer(question)
    _print_steps(steps)

    if chunks:
        print("\nCHUNKS SEEN:")
        for i, c in enumerate(chunks, 1):
            where = c["source"] + (f" > {c['heading']}" if c.get("heading") else "")
            print(f"  [{i}] {where} (chunk {c['chunk_index']}, score={c['score']:.3f})")

    print("\nANSWER:")
    print(final_answer)


def _chat(user_id: int, thread_id: uuid_mod.UUID | None) -> None:
    llm = get_adapter()                     # one adapter for the session; a recorder per turn
    print(f"Kirana assistant ({llm.provider} / {llm.model}), shopper {user_id}, "
          f"history {config.HISTORY_TURNS} turn(s). Ask a question, or 'exit' to quit.")
    while True:
        question = input("\n> ").strip()
        if question.lower() in {"exit", "quit", ""}:
            break
        try:
            result = chat.send(user_id, question, thread_id=thread_id, llm=llm)
        except chat.ThreadNotFound:
            print(f"No thread {thread_id} for shopper {user_id}.")
            return
        thread_id = result.thread_id
        _print_steps(result.steps)
        print(f"\n{result.reply}")
        if result.citations:
            print("\ncitations: " + ", ".join(c["source"] for c in result.citations))
        u = result.usage
        print(f"\n[thread {thread_id}]  {u['llm_calls']} LLM call(s), "
              f"{u['input_tokens']} in / {u['output_tokens']} out tokens, "
              f"{u['latency_ms']} ms, cost {chat.total_cost(u)}")


def _threads(user_id: int) -> None:
    threads = chat.list_threads(user_id)
    if not threads:
        print(f"No threads for shopper {user_id}.")
    for t in threads:
        print(f"{t.id}  {t.updated_at:%Y-%m-%d %H:%M}  {t.title}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kirana_ai.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="index the knowledge base into Qdrant")
    p_ingest.add_argument("--recreate", action="store_true",
                          help="drop the collection first (removes chunks of deleted files)")

    p_threads = sub.add_parser("threads", help="list a shopper's threads")
    p_threads.add_argument("--user", type=int, required=True, help="Kirana user id")

    for name, help_text in (("ask", "answer one question (stateless)"),
                            ("chat", "saved conversation with history")):
        p = sub.add_parser(name, help=help_text)
        if name == "ask":
            p.add_argument("question")
        else:
            p.add_argument("--user", type=int, required=True, help="Kirana user id (owns the thread)")
            p.add_argument("--thread", type=uuid_mod.UUID, help="continue this thread")
        p.add_argument("-t", "--trace", action="store_true",
                       help="print every step: prompts, embeddings, filters, results")
        p.add_argument("--trace-full", action="store_true",
                       help="like --trace, without truncating long prompts")

    args = parser.parse_args()

    if args.command == "ingest":
        ingest(args.recreate)
        return
    if args.command == "threads":
        _threads(args.user)
        return

    if args.trace or args.trace_full:
        trace.enable(True, full=args.trace_full)

    if args.command == "ask":
        _print_answer(args.question)
        return
    _chat(args.user, args.thread)


if __name__ == "__main__":
    main()
