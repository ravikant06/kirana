"""
Command line for the AI service: try the agent, inspect Kafka, operate the knowledge base.

    python -m kirana_ai.cli ask "Can I return opened rice?"
    python -m kirana_ai.cli ask -t "..."        # print every step: prompts, filters, hits
    python -m kirana_ai.cli chat --user 7       # a saved conversation (Postgres), with history
    python -m kirana_ai.cli chat --user 7 --thread <id>   # continue one
    python -m kirana_ai.cli threads --user 7    # list a shopper's threads
    python -m kirana_ai.cli kafka-setup         # topics + kb-docs bucket + its event rule (idempotent)
    python -m kirana_ai.cli events              # raw records on kb.documents.v1 (commits nothing)
    python -m kirana_ai.cli seed-kb             # upload kb/seed/* to MinIO (the worker indexes them)
    python -m kirana_ai.cli redrive             # replay the dead-letter topic
    python -m kirana_ai.cli reindex [--dry-run] # repair drift between MinIO, ai.documents and Qdrant
    python -m kirana_ai.worker                  # the ingest worker (Kafka consumer)
    python -m kirana_ai.cli index-products      # (re)build the product index from Kirana's API
    python -m kirana_ai.catalog                 # the catalog worker: catalog.v1 -> product index

`ask` is stateless and touches no database. `chat` is the real turn: history,
thread and messages in Postgres, every LLM call recorded in ai.llm_calls.
"""
import argparse
import time
import json
import uuid as uuid_mod

from kirana_ai import agent, chat, config, trace
from kirana_ai.errors import UpstreamUnavailable
from kirana_ai.llm import LLMError, get_adapter


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


def _kafka_setup() -> None:
    from kirana_ai import kafka, storage
    for topic, what in kafka.ensure_topics().items():
        print(f"topic  {topic:<24} {what}")
    for part, what in storage.ensure_kb_bucket().items():
        print(f"{part:<6} {what}")


def _events(topic: str, limit: int, full: bool) -> None:
    from kirana_ai import kafka
    n = 0
    for rec in kafka.tail(topic, max_messages=limit):
        n += 1
        print(f"partition {rec['partition']}  offset {rec['offset']}  key {rec['key']!r}")
        value = rec["value"] or {}
        if full:
            print(json.dumps(value, indent=2))
        for r in value.get("Records", []):
            obj = r.get("s3", {}).get("object", {})
            print(f"  {r.get('eventName')}  {obj.get('key')}  size={obj.get('size')}")
            for k, v in (obj.get("userMetadata") or {}).items():
                print(f"    {k}: {v}")
    print(f"{n} record(s) on {topic}")


def _threads(user_id: int) -> None:
    threads = chat.list_threads(user_id)
    if not threads:
        print(f"No threads for shopper {user_id}.")
    for t in threads:
        print(f"{t.id}  {t.updated_at:%Y-%m-%d %H:%M}  {t.title}")


def main() -> None:
    try:
        _main()
    except (UpstreamUnavailable, LLMError) as exc:
        raise SystemExit(f"error: {exc}") from exc   # fine here: this is the CLI's exit


def _main() -> None:
    parser = argparse.ArgumentParser(prog="kirana_ai.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("index-products", help="snapshot: index every live product from Kirana's API")
    sub.add_parser("seed-kb", help="upload kb/seed/* into MinIO; the worker indexes them")
    sub.add_parser("redrive", help=f"replay {config.KB_DLT} onto {config.KB_TOPIC}")
    p_reindex = sub.add_parser("reindex", help="make Qdrant and ai.documents match the files in MinIO")
    p_reindex.add_argument("--dry-run", action="store_true", help="only print what would change")
    sub.add_parser("kafka-setup", help="create kb topics, the kb-docs bucket and its Kafka event rule")
    p_events = sub.add_parser("events", help="print records on a topic, from the start (commits nothing)")
    p_events.add_argument("--topic", default=config.KB_TOPIC)
    p_events.add_argument("--limit", type=int, default=20)
    p_events.add_argument("--full", action="store_true", help="print each event's whole JSON")

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

    if args.command == "threads":
        _threads(args.user)
        return
    if args.command == "index-products":
        from kirana_ai import catalog
        started = time.perf_counter()
        counts = catalog.rebuild()
        print(f"{counts} in {time.perf_counter() - started:.1f} s -> {config.PRODUCTS_COLLECTION}")
        return
    if args.command == "seed-kb":
        from kirana_ai import reconcile
        print("\n".join(reconcile.seed()))
        return
    if args.command == "redrive":
        from kirana_ai import reconcile
        print(f"re-drove {reconcile.redrive()} message(s) from {config.KB_DLT}")
        return
    if args.command == "reindex":
        from kirana_ai import reconcile
        print("\n".join(reconcile.reindex(dry_run=args.dry_run)))
        return
    if args.command == "kafka-setup":
        _kafka_setup()
        return
    if args.command == "events":
        _events(args.topic, args.limit, args.full)
        return

    if args.trace or args.trace_full:
        trace.enable(True, full=args.trace_full)

    if args.command == "ask":
        _print_answer(args.question)
        return
    _chat(args.user, args.thread)


if __name__ == "__main__":
    main()
