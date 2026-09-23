from dataclasses import replace
import json

import pytest

from moneygraph.ai import AIConfig, AIUnavailable, ExplanationService
from moneygraph.ai.check import main
from moneygraph.ai.models import ModelExplanation, Review
from moneygraph.ai.provider import ChatCompletionsTransport
from conftest import FakeTransport


@pytest.fixture
def openai_env(monkeypatch):
    for name in ("MONEYGRAPH_AI_MODEL", "MONEYGRAPH_AI_BASE_URL", "MONEYGRAPH_AI_API_KEY",
                 "MONEYGRAPH_AI_REASONING_EFFORT", "MONEYGRAPH_AI_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MONEYGRAPH_AI_PROVIDER", "openai")
    monkeypatch.setenv("MONEYGRAPH_AI_ENABLED", "true")
    monkeypatch.setenv("MONEYGRAPH_AI_ALLOW_EXTERNAL", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key-never-send")
    return AIConfig.from_env()


def test_openai_preset_and_key_precedence(openai_env, monkeypatch):
    assert openai_env.model == "gpt-6-luna"
    assert openai_env.endpoint() == "https://api.openai.com/v1/chat/completions"
    assert openai_env.reasoning_effort == "low"
    assert openai_env.review_max_tokens == 2048
    assert openai_env.api_key == "synthetic-key-never-send"
    assert openai_env.api_key not in repr(openai_env)
    monkeypatch.setenv("MONEYGRAPH_AI_API_KEY", "explicit-project-key")
    assert AIConfig.from_env().api_key == "explicit-project-key"


@pytest.mark.parametrize("url", ["https://example.invalid/v1", "https://api.openai.com.example.invalid/v1",
                                "https://api.openai.com:8443/v1", "http://127.0.0.1/v1"])
def test_openai_key_cannot_be_redirected_by_base_url(openai_env, url):
    with pytest.raises(AIUnavailable):
        replace(openai_env, base_url=url).endpoint()


def test_openai_key_not_loaded_for_other_providers(openai_env, monkeypatch):
    monkeypatch.setenv("MONEYGRAPH_AI_PROVIDER", "other")
    monkeypatch.setenv("MONEYGRAPH_AI_BASE_URL", "https://other.invalid/v1")
    monkeypatch.setenv("MONEYGRAPH_AI_MODEL", "other-model")
    config = AIConfig.from_env()
    assert config.api_key == ""
    with pytest.raises(AIUnavailable) as error:
        config.endpoint()
    assert error.value.code == "missing_api_key"


@pytest.mark.parametrize("model", [ModelExplanation, Review])
def test_openai_request_uses_strict_schema_and_current_token_limit(openai_env, model):
    schema = model.model_json_schema()
    payload = {"subject": "Клиент_A", "facts": []}
    request = ChatCompletionsTransport(openai_env).request_body(
        system="Return JSON", payload=payload, schema=schema, max_tokens=2048)
    assert request["max_completion_tokens"] == 2048
    assert "max_tokens" not in request
    assert request["store"] is False
    assert request["reasoning_effort"] == "low"
    structured = request["response_format"]
    assert structured["type"] == "json_schema"
    assert structured["json_schema"]["strict"] is True
    assert structured["json_schema"]["schema"] == schema
    assert openai_env.api_key not in json.dumps(request)
    for definition in (schema, *schema.get("$defs", {}).values()):
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])


def test_reasoning_budget_reaches_review_and_cache_changes(openai_env, context):
    transport = FakeTransport(context)
    result = ExplanationService(openai_env, transport=transport).explain_client(context)
    assert result["metadata"]["model"] == openai_env.model
    assert transport.calls[1]["max_tokens"] == 2048


def test_settings_check_has_no_network_and_does_not_print_secrets(openai_env, monkeypatch, capsys):
    def no_network(*args, **kwargs):
        pytest.fail("Settings-only check must not send a request")
    monkeypatch.setattr(ChatCompletionsTransport, "complete", no_network)
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "Сетевых запросов не было" in captured.out
    assert openai_env.api_key not in captured.out + captured.err


def test_live_check_only_sends_synthetic_data(openai_env, monkeypatch, capsys):
    calls = []
    def complete(self, **kwargs):
        calls.append(kwargs)
        return '{"ok":true}'
    monkeypatch.setattr(ChatCompletionsTransport, "complete", complete)
    assert main(["--live"]) == 0
    assert len(calls) == 1
    assert calls[0]["payload"] == {"check": "connection_only"}
    assert "без финансовых данных" in capsys.readouterr().out


def test_missing_key_check_is_actionable(openai_env, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY")
    assert main([]) == 2
    assert "missing_api_key" in capsys.readouterr().err


def test_live_check_rejects_wrong_result(openai_env, monkeypatch, capsys):
    monkeypatch.setattr(ChatCompletionsTransport, "complete", lambda *a, **k: '{"ok":1}')
    assert main(["--live"]) == 2
    captured = capsys.readouterr()
    assert "invalid_response" in captured.err
    assert "не подтверждает ошибку ключа" in captured.err
    assert openai_env.api_key not in captured.out + captured.err
