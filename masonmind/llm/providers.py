"""Thin standard-library HTTP adapters; credentials are never persisted."""
import base64
from copy import deepcopy
import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from typing import Any
from masonmind.llm.mock import MockClient


def strict_schema(schema: dict) -> dict:
    """Make all object fields explicit for OpenAI strict structured outputs."""
    result = deepcopy(schema)
    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in list(node.values()):
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(result)
    return result


class HTTPClient:
    """Shared transport, with redacted errors and usage accounting."""

    def __init__(self, config: dict):
        self.config = config
        self.usage: dict = {}

    def post(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        """POST JSON; never echo auth headers or provider error bodies."""
        request = Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(request, timeout=self.config["timeout_s"]) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"LLM request failed with HTTP {error.code}; check provider/model/credentials") from None
        except URLError:
            raise RuntimeError("LLM endpoint could not be reached; check connection and configured URL") from None

    def key(self, variable: str) -> str:
        """Read required credentials only from the process environment."""
        value = os.environ.get(variable)
        if not value:
            raise RuntimeError(f"Missing environment variable {variable}")
        return value


class OpenAIClient(HTTPClient):
    def complete(self, system: str, messages: list[dict], images: list[bytes] | None = None,
                 json_schema: dict | None = None) -> str:
        """Use Chat Completions JSON schema output and optional PNG inputs."""
        messages = deepcopy(messages)
        if images:
            messages[0]["content"] = [{"type": "text", "text": messages[0]["content"]}] + [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}}
                for image in images]
        payload = {"model": self.config["model"], "messages": [{"role": "system", "content": system}, *messages],
                   "max_completion_tokens": self.config["max_tokens"]}
        if json_schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "agent_output", "strict": True, "schema": strict_schema(json_schema)}}
        result = self.post("https://api.openai.com/v1/chat/completions", payload,
                           {"Authorization": "Bearer " + self.key("OPENAI_API_KEY")})
        self.usage = result.get("usage", {})
        message = result["choices"][0]["message"]
        if message.get("refusal") or not message.get("content"):
            raise RuntimeError("OpenAI refused or returned no text")
        return message["content"]


class AnthropicClient(HTTPClient):
    def complete(self, system: str, messages: list[dict], images: list[bytes] | None = None,
                 json_schema: dict | None = None) -> str:
        """Call Messages; schema is supplied in the system prompt for broad compatibility."""
        messages = deepcopy(messages)
        if images:
            messages[0]["content"] = [{"type": "text", "text": messages[0]["content"]}] + [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                 "data": base64.b64encode(image).decode()}} for image in images]
        result = self.post("https://api.anthropic.com/v1/messages",
            {"model": self.config["model"], "system": system, "messages": messages,
             "max_tokens": self.config["max_tokens"]},
            {"x-api-key": self.key("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"})
        self.usage = result.get("usage", {})
        return "".join(block["text"] for block in result["content"] if block["type"] == "text")


class OllamaClient(HTTPClient):
    def complete(self, system: str, messages: list[dict], images: list[bytes] | None = None,
                 json_schema: dict | None = None) -> str:
        """Call a configured local Ollama chat server without streaming."""
        messages = deepcopy(messages)
        if images:
            messages[0]["images"] = [base64.b64encode(image).decode() for image in images]
        result = self.post(self.config["url"].rstrip("/") + "/api/chat",
            {"model": self.config["model"], "messages": [{"role": "system", "content": system}, *messages],
             "stream": False, "format": json_schema or "json", "options": {"num_predict": self.config["max_tokens"]}})
        self.usage = {"input_tokens": result.get("prompt_eval_count"), "output_tokens": result.get("eval_count")}
        return result["message"]["content"]


def make_client(config: dict) -> Any:
    """Create the provider selected in YAML, without hard-coded model names."""
    clients = {"openai": OpenAIClient, "anthropic": AnthropicClient, "ollama": OllamaClient}
    if config["provider"] == "mock":
        return MockClient()
    if config["provider"] not in clients:
        raise ValueError(f"Unknown provider: {config['provider']}")
    return clients[config["provider"]](config)
