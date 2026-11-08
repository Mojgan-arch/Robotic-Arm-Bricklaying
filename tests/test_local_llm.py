import json
from masonmind.config import load_config
from masonmind.llm.providers import OllamaClient


def test_local_profile_does_not_replace_offline_default():
    assert load_config("llm")["provider"] == "mock"
    local = load_config("llm_local")
    assert local["provider"] == "ollama"
    assert local["url"] == "http://127.0.0.1:11434"


def test_local_adapter_sends_schema_images_and_bounded_options(monkeypatch):
    client = OllamaClient(load_config("llm_local"))
    captured = {}
    def post(url, payload):
        captured.update(url=url, payload=payload)
        return {"message": {"content": '{"approved":true}'}, "prompt_eval_count": 14, "eval_count": 8}
    monkeypatch.setattr(client, "post", post)
    schema = {"type": "object", "properties": {"approved": {"type": "boolean"}}}
    result = client.complete("Review", [{"role": "user", "content": "Inspect"}], [b"png"], schema)
    assert json.loads(result)["approved"]
    payload = captured["payload"]
    assert payload["think"] is False
    assert payload["format"] == schema
    assert payload["options"]["num_ctx"] == 8192
    assert payload["messages"][1]["images"] == ["cG5n"]
    assert client.usage["output_tokens"] == 8
