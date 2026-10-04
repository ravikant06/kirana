"""
LLM-as-judge for answers (Phase 3 M3).

The judge sees the question, the passages the agent retrieved, the answer, and (when the
question is answerable) the expected facts. It returns a verdict as JSON:

    answered   did the answer try to answer, rather than say it couldn't find it?
    correct    does it state the expected facts without contradicting them?   (answerable only)
    faithful   is every claim in it supported by the retrieved passages?

Same model family as the agent (AD19, no second key yet), which risks self-preference:
a model tends to rate its own family's answers generously. `run_answers --grade` measures
how often this judge agrees with a human, and that agreement is what makes its scores usable.
"""
import json
import re

from kirana_ai.llm import Message, get_adapter

SYSTEM = """You are a strict evaluator of a store assistant's answers. Judge only what is written.

Return ONLY a JSON object, no prose, no code fence:
{"answered": true|false, "correct": true|false|null, "faithful": true|false, "reason": "<one sentence>"}

- answered: true if the answer gives an answer; false if it says it could not find the
  information or declines.
- correct: only when EXPECTED FACTS are given. true if the answer states them (wording may
  differ) and contradicts none of them; false otherwise, including when it declined.
  null when no expected facts are given.
- faithful: true if every factual claim in the answer is supported by the PASSAGES. A
  decline is faithful. Any number, fee, time limit or rule not in the passages makes it false.
"""


def judge(question: str, passages: list[dict], answer: str, expected: str | None) -> dict:
    context = "\n\n".join(f"[{i}] {p.get('source')}: {p.get('text', '')}" for i, p in enumerate(passages, 1))
    prompt = (f"QUESTION:\n{question}\n\nPASSAGES:\n{context or '(none retrieved)'}\n\n"
              f"ANSWER:\n{answer}\n\nEXPECTED FACTS:\n{expected or '(none: the store documents do not answer this)'}")
    reply = get_adapter().complete([Message.user(prompt)], system=SYSTEM)
    return parse_verdict(reply.text or "")


def parse_verdict(text: str) -> dict:
    """The judge's JSON, tolerating a code fence or stray prose around it."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        verdict = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        verdict = {}
    return {
        "answered": verdict.get("answered"),
        "correct": verdict.get("correct"),
        "faithful": verdict.get("faithful"),
        "reason": verdict.get("reason") or f"unparseable judge output: {text[:200]!r}",
    }
