# Attack material

Files used to attack the assistant on purpose (prompt injection, memory poisoning). They are
NOT seed data: `cli seed-kb` uploads everything in `kb/seed/`, so keeping them there would plant
them in the knowledge base on every re-seed. Upload one by hand (Manage → Knowledge base) to run an
experiment, then delete it.

- `policy_injection.md`: an obvious "ASSISTANT INSTRUCTION" to cancel orders when refunds are asked about.
