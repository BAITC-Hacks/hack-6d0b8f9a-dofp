from copy import deepcopy
import json

import pytest

from moneygraph.ai.errors import InvalidExplanation
from moneygraph.ai.validation import validate_explanation
from conftest import response_for


def test_valid_cited_response(context):
    result = validate_explanation(json.dumps(response_for(context), ensure_ascii=False), context)
    assert len(result.next_checks) == 2


@pytest.mark.parametrize("mutation", ["number", "reference", "limitation", "role_evidence", "availability", "hypothesis", "field", "markup", "alias", "accusation", "action_context"])
def test_unsafe_or_incomplete_output_rejected(context, mutation):
    response = response_for(context)
    if mutation == "number":
        response["summary"][0]["text"] = "Наблюдаются 987654 плательщика."
    elif mutation == "reference":
        response["summary"][0]["fact_ids"] = ["F999"]
    elif mutation == "limitation":
        response["limitations"].pop()
    elif mutation == "role_evidence":
        response["reasons"][0]["fact_ids"] = response["summary"][0]["fact_ids"]
    elif mutation == "availability":
        response["next_checks"][1]["availability"] = "in_product"
    elif mutation == "hypothesis":
        response["alternative_hypotheses"][0]["text"] = "Клиент ведёт обычную коммерческую деятельность."
    elif mutation == "field":
        response["crime_probability"] = .99
    elif mutation == "markup":
        response["summary"][0]["text"] = "Наблюдение <script>alert(1)</script>"
    elif mutation == "alias":
        response["summary"][0]["text"] = "Получателем является Клиент_ZZZ."
    elif mutation == "accusation":
        response["summary"][0]["text"] = "Клиент отмывает деньги."
    elif mutation == "action_context":
        response["next_checks"][0]["fact_ids"] = response["next_checks"][0]["fact_ids"][:1]
    with pytest.raises(InvalidExplanation):
        validate_explanation(json.dumps(response, ensure_ascii=False), context)


@pytest.mark.parametrize("raw", ["not json", '{"summary":[],"summary":[]}', '{"x":NaN}', '[]'])
def test_bad_json_rejected(raw, context):
    with pytest.raises(InvalidExplanation):
        validate_explanation(raw, context)
