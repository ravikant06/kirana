"""
Tool routing (Phase 4 M5): does the agent pick the right tool for the question?

    python -m eval.run_routing      # ~16 questions x ~2 LLM calls: a few cents

Expected tools are a set: "products", "docs" (search_docs or list_documents), both, or none.
A case passes when the tools the agent actually called cover exactly the expected kinds.
"""
from kirana_ai import agent

KIND = {"search_products": "products", "search_docs": "docs", "list_documents": "docs"}
CASES = [
    ("Healthy snacks for my kids under ₹100?", {"products"}),
    ("Do you have almond milk?", {"products"}),
    ("I want to make biryani tonight, what do I need?", {"products"}),
    ("Suggest something for a diabetic breakfast", {"products"}),
    ("What can I use instead of maida for gluten-free rotis?", {"products"}),
    ("Can I return opened rice?", {"docs"}),
    ("How much is express delivery?", {"docs"}),
    ("When will my UPI refund arrive?", {"docs"}),
    ("What happens if I'm not home for the delivery?", {"docs"}),
    ("What policies do you have?", {"docs"}),
    ("How fresh is the paneer you deliver?", {"docs"}),
    ("Suggest a cold drink and tell me if delivery is free above some amount", {"products", "docs"}),
    ("Recommend frozen snacks, and can I return ice cream?", {"products", "docs"}),
    ("Hi!", set()),
    ("Thanks, that's all", set()),
    ("Is the coconut oil you sell cold-pressed?", {"products"}),
]


def main() -> None:
    passed = 0
    for question, expected in CASES:
        _, _, steps = agent.answer(question)
        got = {KIND.get(s["tool"], s["tool"]) for s in steps}
        ok = got == expected
        passed += ok
        print(f"{'✓' if ok else '✗'} {question[:62]:62} expected {sorted(expected) or ['none']}, got {sorted(got) or ['none']}")
    print(f"\n{passed}/{len(CASES)} routed correctly ({passed / len(CASES):.0%})")


if __name__ == "__main__":
    main()
