"""Provider contracts and reproducible offline responses."""
from typing import Protocol


class LLMClient(Protocol):
    def complete(self, system: str, messages: list[dict], images: list[bytes] | None = None,
                 json_schema: dict | None = None) -> str:
        """Return a JSON response to a task-level request."""
        ...
