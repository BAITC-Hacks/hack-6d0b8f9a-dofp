"""Synthetic snapshots use the real A/B pipeline; no mocked finance calculations."""
import json

import pandas as pd
import pytest

pytest.importorskip("pydantic", minversion="2", reason="AI tests require the existing [serve] extra")

from moneygraph.ai import AIConfig, SnapshotContextBuilder
from moneygraph.pipeline import run_pipeline


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    root = tmp_path_factory.mktemp("ai-snapshot")
    data = root / "data"
    data.mkdir()
    seeds = [2**53 + i for i in range(1, 14)]
    collector, distributor = 2**53 + 50, 2**53 + 51
    recipients = [2**53 + i for i in range(100, 116)]
    boundary, isolate = 2**53 + 200, 2**53 + 201
    gids = seeds + [collector, distributor] + recipients + [boundary, isolate]
    nodes = pd.DataFrame({"gid": gids, "depth": [0]*13 + [1, 2] + [3]*16 + [4, 0],
                          "is_seed": [True]*13 + [False]*19 + [True]})
    rows = [(gid, collector, 20000.01) for gid in seeds]
    rows += [(collector, distributor, 260000.13)]
    rows += [(distributor, gid, 15000.00) for gid in recipients]
    rows += [(recipients[0], boundary, 15000.00)]
    tx = pd.DataFrame([{"src": s, "dst": d, "sum_kzt": amount, "date": "2026-07-12"}
                       for s, d, amount in rows])
    edges = tx.groupby(["src", "dst"], as_index=False).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
    depth = dict(zip(nodes.gid, nodes.depth))
    edges["depth"] = edges.dst.map(depth)
    for name, frame in (("nodes", nodes), ("edges", edges), ("transactions", tx)):
        frame.to_parquet(data / f"{name}.parquet", index=False)
    completed = run_pipeline(data, root / "out")
    return completed, {"collector": str(collector), "distributor": str(distributor),
                       "multiple_seeds": str(collector), "boundary": str(boundary), "isolate": str(isolate)}


@pytest.fixture
def builder(snapshot):
    return SnapshotContextBuilder(snapshot[0].directory)


@pytest.fixture
def context(builder, snapshot):
    return builder.for_client(snapshot[1]["collector"])


@pytest.fixture
def config():
    return AIConfig(enabled=True, provider="test-local", model="synthetic-test-only",
                    base_url="http://127.0.0.1:9123/v1")


def response_for(context):
    """A fake model fixture only. Never used by production code as a fallback."""
    facts = {fact.key: fact for fact in context.facts}

    def statement(text, *keys, **extra):
        return {"text": text, "fact_ids": [facts[key].id for key in keys], **extra}

    return {
        "summary": [statement(f"Наблюдается разных плательщиков: {facts['in_degree'].value}.", "in_degree"),
                    statement(f"Наблюдаемая входящая сумма: {facts['in_minor'].value}.", "in_minor")],
        "reasons": [statement(f"Рассчитанная роль: {facts['role'].value['label']}; исходная поддержка и ограничения описаны в фактах.",
                              "role", "role_support", "role_score", "caps"),
                    statement("Приоритет отражает вклады охвата исходных клиентов, поддержки роли, положения в сети и объёма.",
                              "priority", "contributions")],
        "alternative_hypotheses": [statement("Гипотеза: наблюдаемая структура может соответствовать обычным расчётам; контекст отношений неизвестен.",
                                             "in_degree", "out_degree", status="hypothesis")],
        "next_checks": [statement("Проверить операции клиента и суммы по его контрагентам.",
                                  "action:inspect_transactions", "in_tx", action="inspect_transactions", availability="in_product"),
                        statement("Запросить контекст отношений с наблюдаемыми контрагентами.",
                                  "action:request_relationship_context", "in_degree", action="request_relationship_context", availability="request_data")],
        "limitations": [statement(fact.value, fact.key, code=fact.key.split(":", 1)[1])
                        for fact in context.facts if fact.key.startswith("limitation:")],
    }


class FakeTransport:
    def __init__(self, context, *, response=None, review=None, error=None):
        self.response = response_for(context) if response is None else response
        self.review = {"supported": True, "issues": []} if review is None else review
        self.error = error
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        value = self.response if "proposed_explanation" not in kwargs["payload"] else self.review
        return json.dumps(value, ensure_ascii=False)
