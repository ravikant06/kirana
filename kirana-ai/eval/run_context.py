"""
Context measurement (Phase 7 M1): a scripted conversation, and what each turn's prompt is made of.

    python -m eval.run_context                 # 30 turns, one new thread, real Gemini (about ₹50-60)
    python -m eval.run_context --turns 12      # cheaper

Every LLM call records an estimate of its input tokens per block (ai.llm_calls.context) and the
tokens served from the provider's prompt cache (ai.llm_calls.cached_tokens). This prints, per turn:
how many calls, input tokens, how many of them were history, tool rounds and the fixed prefix
(system prompt + tool specs), how many were cached, and the cost.

It is the BEFORE picture for Phase 7: run it again after the rolling summary (M2) and prompt
ordering (M5) to see what they change. The questions are public (policies, products), so no
sign-in is needed; turn 2 states facts that later milestones test recall of.
"""
import argparse
import uuid
from collections import Counter
from decimal import Decimal

from sqlalchemy import select

from kirana_ai import chat, policy
from kirana_ai.db import session_scope
from kirana_ai.db.models import LlmCall

SCRIPT = [
    "Hi! I'm planning my groceries for the week.",
    "We're a family of 4 and we're vegetarian. My son is allergic to peanuts.",
    "What's the delivery fee for a ₹350 order?",
    "And if I order for ₹600?",
    "Suggest some healthy breakfast options.",
    "Anything gluten-free among those?",
    "Can I return opened rice if I don't like it?",
    "What about fruit that arrives bruised?",
    "Suggest snacks for kids' tiffin boxes.",
    "Which of those are safe for my son?",
    "How long do UPI refunds take?",
    "And card refunds?",
    "What cooking oils do you sell?",
    "Which one is best for deep frying?",
    "Do you have paneer?",
    "How fresh will the paneer be when it arrives?",
    "Suggest ingredients for dal tadka.",
    "What spices do I need for it?",
    "Can I cancel an order after it's packed?",
    "What happens if I miss the delivery?",
    "Suggest some drinks for a party.",
    "Anything without added sugar?",
    "Is there a minimum order value?",
    "Do you sell alcohol?",
    "Suggest something for a quick dinner tonight.",
    "Remind me, how many people am I shopping for, and what should I avoid for my son?",
    "What frozen foods do you have?",
    "Can I return ice cream?",
    "Suggest a dessert for the weekend.",
    "Summarise what we planned today.",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--turns", type=int, default=len(SCRIPT))
    parser.add_argument("--user", type=int, default=1, help="who owns the thread (no tools need sign-in)")
    args = parser.parse_args()

    thread_id = None
    totals = Counter()
    cost_total = Decimal(0)
    print(f"{'turn':>4} {'calls':>5} {'input':>7} {'history':>8} {'tools rd':>8} {'prefix':>7} "
          f"{'cached':>7} {'cost $':>9}  question")
    for n, question in enumerate(SCRIPT[: args.turns], start=1):
        result = chat.send(args.user, question, thread_id=thread_id,
                           caller=policy.Caller(user_id=args.user))
        thread_id = result.thread_id
        chat.after_turn(thread_id)          # the rolling summary, as the API runs it after each answer
        with session_scope() as session:
            calls = session.scalars(select(LlmCall).where(LlmCall.turn_id == result.message_id)).all()
            blocks, cached, cost = Counter(), 0, Decimal(0)
            for c in calls:
                blocks.update(c.context or {})
                cached += c.cached_tokens or 0
                cost += c.cost_usd or 0
        input_tokens = sum(blocks.values())
        totals.update(blocks)
        totals["cached"] += cached
        cost_total += cost
        print(f"{n:>4} {len(calls):>5} {input_tokens:>7} {blocks['history']:>8} {blocks['tool_rounds']:>8} "
              f"{blocks['system'] + blocks['tools']:>7} {cached:>7} {cost:>9.5f}  {question[:44]}")

    # Summary calls run after a turn and belong to no turn: count them separately, or the
    # cost of the summary (what Phase 7 adds) would be invisible here.
    with session_scope() as session:
        summaries = session.scalars(select(LlmCall).where(LlmCall.thread_id == thread_id,
                                                          LlmCall.turn_id.is_(None))).all()
    summary_tokens = sum((c.input_tokens or 0) + (c.output_tokens or 0) for c in summaries)
    summary_cost = sum((c.cost_usd or 0) for c in summaries)
    cost_total += summary_cost

    sent = sum(v for k, v in totals.items() if k != "cached")
    print(f"\nthread {thread_id}")
    print(f"summary calls: {len(summaries)} · {summary_tokens:,} tokens · ${summary_cost:.4f} (included in the cost below)")
    print(f"input tokens over {args.turns} turns: {sent:,} · cached: {totals['cached']:,} "
          f"({totals['cached'] / sent:.0%}) · cost: ${cost_total:.4f}")
    print("share by block: " + ", ".join(f"{k} {v / sent:.0%}" for k, v in totals.most_common() if k != "cached"))


if __name__ == "__main__":
    main()
