import json

import pytest

from ai_ecosystem.core.errors.exceptions import ModelMalformedError
from ai_ecosystem.intelligence.models.providers import MockModelProvider, ModelResponse, request_structured
from ai_ecosystem.intelligence.research.query import formulate_query


def test_query_is_reformulated_with_original_subject_and_gap():
    provider = MockModelProvider("m", handler=lambda request: ModelResponse(structured={"query": "Anthropic Claude IPO valuation"}))
    query = formulate_query(lambda req, schema: request_structured(provider, req, schema),
                            "What is Claude's IPO valuation?", "current company valuation")
    assert query == "Anthropic Claude IPO valuation"
    body = json.loads(provider.calls[0].prompt)
    assert body["original_task"] == "What is Claude's IPO valuation?"
    assert body["information_needed"] == "current company valuation"
    assert provider.calls[0].tools == []


def test_query_repairs_model_instruction_before_any_search():
    replies = iter(["Extract the day from the provided context", "current calendar date"])
    provider = MockModelProvider("m", handler=lambda req: ModelResponse(structured={"query": next(replies)}))
    query = formulate_query(lambda req, schema: request_structured(provider, req, schema), "What date is it today?")
    assert query == "current calendar date"
    assert len(provider.calls) == 2


def test_query_failure_does_not_fall_back_to_wrong_instruction():
    provider = MockModelProvider("m", handler=lambda req: ModelResponse(structured={"query": "extract from the provided context"}))
    with pytest.raises(ModelMalformedError, match="Could not form a useful search query"):
        formulate_query(lambda req, schema: request_structured(provider, req, schema), "What is the valuation?")
    assert len(provider.calls) == 2


def test_query_preserves_acronym_and_does_not_invent_year():
    replies = iter(["Claude stock price 2025 acquisitions", "Claude IPO valuation"])
    provider = MockModelProvider("m", handler=lambda req: ModelResponse(structured={"query": next(replies)}))
    assert formulate_query(lambda req, schema: request_structured(provider, req, schema), "What is Claude IPO valuation?") == "Claude IPO valuation"
    assert len(provider.calls) == 2


def test_query_retries_year_that_is_not_in_the_task():
    replies = iter(["Claude IPO valuation 1999", "Claude IPO valuation"])
    provider = MockModelProvider("m", handler=lambda req: ModelResponse(structured={"query": next(replies)}))
    assert formulate_query(lambda req, schema: request_structured(provider, req, schema), "What is Claude IPO valuation?") == "Claude IPO valuation"
    assert "invented years" in provider.calls[1].prompt
