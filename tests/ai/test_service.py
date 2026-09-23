from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Event

import pytest

from moneygraph.ai import AIUnavailable, ExplanationService
from moneygraph.ai.errors import InvalidExplanation
from moneygraph.ai.validation import validate_explanation
from conftest import FakeTransport, response_for


@pytest.mark.parametrize("scenario", ["collector", "distributor", "multiple_seeds", "boundary", "isolate"])
def test_five_scenarios_generation_review_and_citations(builder, snapshot, config, scenario):
    context = builder.for_client(snapshot[1][scenario])
    transport = FakeTransport(context)
    result = ExplanationService(config, transport=transport).explain_client(context)
    assert len(transport.calls) == 2
    assert result["metadata"]["snapshot_id"] == snapshot[0].run_id
    assert result["metadata"]["human_review_required"] is True
    assert result["facts"] == context.packet()["facts"]
    encoded = json.dumps(transport.calls, ensure_ascii=False)
    assert context.client_id not in encoded
    assert context.snapshot_id not in encoded
    assert "proposed_explanation" in transport.calls[1]["payload"]


def test_semantically_wrong_text_rejected_by_separate_review(context, config):
    response = response_for(context)
    # Valid number, invalid meaning: numerical membership alone cannot catch this.
    response["summary"][0]["text"] = "Наблюдается разных получателей: 13."
    validate_explanation(json.dumps(response, ensure_ascii=False), context)
    transport = FakeTransport(context, response=response,
                              review={"supported": False, "issues": ["Число плательщиков названо числом получателей"]})
    with pytest.raises(AIUnavailable) as error:
        ExplanationService(config, transport=transport).explain_client(context)
    assert error.value.code == "unsupported_claim"


def test_invalid_response_does_not_call_reviewer(context, config):
    transport = FakeTransport(context, response={"summary": "invented"})
    service = ExplanationService(config, transport=transport)
    with pytest.raises(AIUnavailable) as error:
        service.explain_client(context)
    assert error.value.code == "invalid_response"
    assert len(transport.calls) == 1


@pytest.mark.parametrize("review", [{"supported": True, "issues": ["Contradiction"]},
                                   {"supported": "true", "issues": []}, {"supported": True}])
def test_inconsistent_or_malformed_review_rejected(context, config, review):
    service = ExplanationService(config, transport=FakeTransport(context, review=review))
    with pytest.raises(AIUnavailable):
        service.explain_client(context)


def test_failure_never_cached_or_replaced_with_template(context, config):
    transport = FakeTransport(context, error=AIUnavailable("provider_unavailable"))
    service = ExplanationService(config, transport=transport)
    for _ in range(2):
        with pytest.raises(AIUnavailable) as error:
            service.explain_client(context)
        assert str(error.value) == "AI-объяснение недоступно"
    assert len(transport.calls) == 2
    assert not service._cache


def test_cache_isolated_and_does_not_return_mutable_shared_values(context, config):
    transport = FakeTransport(context)
    service = ExplanationService(config, transport=transport)
    first = service.explain_client(context)
    first["summary"][0]["text"] = "changed"
    second = service.explain_client(context)
    assert second["metadata"]["cached"] is True
    assert second["summary"][0]["text"] != "changed"
    assert len(transport.calls) == 2
    for changed in (replace(context, snapshot_id="other-run"), replace(context, client_id="other-client")):
        service.explain_client(changed)
    assert len(transport.calls) == 6
    other = ExplanationService(replace(config, model="other-model"), transport=transport)
    assert other.explain_client(context)["metadata"]["model"] == "other-model"
    assert len(transport.calls) == 8


def test_cache_bounded_and_expired_entries_recomputed(context, config, monkeypatch):
    transport = FakeTransport(context)
    service = ExplanationService(replace(config, cache_entries=1), transport=transport)
    service.explain_client(context)
    service.explain_client(replace(context, snapshot_id="different"))
    assert len(service._cache) == 1
    key = next(iter(service._cache))
    _, result = service._cache[key]
    service._cache[key] = (0, result)
    service.explain_client(replace(context, snapshot_id="different"))
    assert len(transport.calls) == 6


def test_duplicate_requests_are_bounded(context, config):
    entered, release = Event(), Event()
    fake = FakeTransport(context)

    class SlowTransport:
        def complete(self, **kwargs):
            entered.set()
            assert release.wait(3)
            return fake.complete(**kwargs)

    service = ExplanationService(config, transport=SlowTransport())
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.explain_client, context)
        try:
            assert entered.wait(3)
            with pytest.raises(AIUnavailable) as error:
                service.explain_client(context)
            assert error.value.code == "busy"
        finally:
            release.set()
        assert pending.result()["summary"]


def test_disabled_and_external_denied_before_any_request(context, config):
    for blocked in (replace(config, enabled=False),
                    replace(config, base_url="https://example.invalid/v1", api_key="test-secret")):
        transport = FakeTransport(context)
        with pytest.raises(AIUnavailable):
            ExplanationService(blocked, transport=transport).explain_client(context)
        assert not transport.calls


def test_oversized_content_fails(context, config):
    fake = FakeTransport(context)
    fake.response = {"text": "я" * config.max_response_bytes}
    with pytest.raises(AIUnavailable) as error:
        ExplanationService(config, transport=fake).explain_client(context)
    assert error.value.code == "response_too_large"
