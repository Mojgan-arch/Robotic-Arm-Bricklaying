"""Append-only rule history with keyword, confidence and recency retrieval."""
import math
import re
from pathlib import Path
from masonmind.io import append_json
from masonmind.schemas import MemoryRule


class MemoryStore:
    """Keep validated evidence locally; disabled memory performs no file I/O."""

    def __init__(self, config: dict) -> None:
        self.enabled, self.path, self.limit = config["enabled"], Path(config["path"]), config["max_rules"]

    def rules(self) -> list[MemoryRule]:
        """Return the latest confirmation of each normalized rule text."""
        if not self.enabled or not self.path.exists():
            return []
        latest: dict[str, MemoryRule] = {}
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rule = MemoryRule.model_validate_json(line)
            except ValueError as error:
                raise ValueError(f"Invalid memory at {self.path}:{line_number}: {error}") from error
            key = rule.text.strip().casefold()
            latest.pop(key, None)
            latest[key] = rule
        return list(latest.values())

    def add(self, rule: MemoryRule) -> None:
        """Append evidence; repeat confirmations preserve prior records."""
        if not self.enabled:
            return
        matching = [old for old in self.rules() if old.text.strip().casefold() == rule.text.strip().casefold()]
        if matching:
            rule = rule.model_copy(update={"times_confirmed": matching[-1].times_confirmed + 1})
        append_json(self.path, rule)

    def retrieve(self, query: str) -> list[MemoryRule]:
        """Rank by token overlap, confidence, confirmations and file-order recency."""
        rules = self.rules()
        tokens = set(re.findall(r"\w+", query.casefold()))
        def score(item: tuple[int, MemoryRule]) -> float:
            index, rule = item
            overlap = len(tokens & set(re.findall(r"\w+", rule.text.casefold()))) / max(1, len(tokens))
            return 2 * overlap + rule.confidence + math.log1p(rule.times_confirmed) / 4 + (index + 1) / max(1, len(rules)) / 4
        return [rule for _, rule in sorted(enumerate(rules), key=score, reverse=True)[:self.limit]]
