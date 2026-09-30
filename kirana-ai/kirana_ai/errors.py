"""
Failures of the services a chat turn depends on, as ordinary exceptions.

The CLI prints them and exits; the API turns them into 503 ProblemDetails.
Neither may use SystemExit: in a server that would end the process, not the
request. (LLM provider failures are kirana_ai.llm.LLMError.)
"""


class UpstreamUnavailable(Exception):
    """Qdrant, the embedding API, or the knowledge base is not usable right now."""

    def __init__(self, service: str, message: str) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service
