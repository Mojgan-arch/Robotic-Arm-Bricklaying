"""Schema validation, retries, caching and complete call audits."""
import hashlib
import json
import time
from pathlib import Path
from typing import TypeVar
from pydantic import BaseModel, ValidationError
from masonmind.io import append_json, json_value
from masonmind.llm import LLMClient

Output = TypeVar("Output", bound=BaseModel)


class Agent:
    """One named role with a versioned prompt and validated JSON output."""
    name: str
    schema: type[BaseModel]

    def __init__(self, client: LLMClient, config: dict, run: Path, retries: int = 3):
        self.client, self.config, self.run, self.retries = client, config, run, retries

    def build_input(self, **evidence: object) -> dict:
        """Build role evidence without any hidden simulator state."""
        return evidence

    def decide(self, data: dict, images: list[bytes] | None = None) -> BaseModel:
        """Call, validate, and repair malformed JSON up to the retry budget."""
        schema = self.schema.model_json_schema()
        system = (Path(__file__).parent / "prompts" / f"{self.name}.md").read_text(encoding="utf-8")
        system += "\nReturn exactly this JSON schema, with no Markdown fences:\n" + json.dumps(schema)
        messages = [{"role": "user", "content": json.dumps(data, default=json_value, sort_keys=True)}]
        for attempt in range(self.retries + 1):
            key = hashlib.sha256(json.dumps({"provider": self.config["provider"], "model": self.config["model"],
                "url": self.config.get("url"), "system": system, "messages": messages,
                "images": [hashlib.sha256(i).hexdigest() for i in images or []]}, sort_keys=True).encode()).hexdigest()
            cache = Path(self.config["cache_dir"]) / f"{key}.json"
            cached = self.config["cache"] and cache.exists()
            started = time.perf_counter()
            response = cache.read_text(encoding="utf-8") if cached else self.client.complete(system, messages, images, schema)
            entry = {"agent": self.name, "system": system, "messages": messages, "response": response,
                     "latency_s": time.perf_counter() - started, "attempt": attempt, "cached": cached,
                     "usage": {} if cached else getattr(self.client, "usage", {}), "image_count": len(images or [])}
            try:
                output = self.schema.model_validate_json(response)
            except ValidationError as error:
                entry["validation_error"] = str(error)
                append_json(self.run / "llm_log.jsonl", entry)
                messages.extend([{"role": "assistant", "content": response},
                                 {"role": "user", "content": f"Validation failed; repair the JSON: {error}"}])
                if attempt == self.retries:
                    raise RuntimeError(f"{self.name}: invalid output after {attempt + 1} attempts: {error}") from error
            else:
                append_json(self.run / "llm_log.jsonl", entry)
                if self.config["cache"] and not cached:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(response, encoding="utf-8")
                return output
        raise AssertionError("Unreachable agent retry state")
