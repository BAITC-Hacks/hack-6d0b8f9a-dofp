"""Build a small, immutable fact packet from bytes of one completed snapshot."""
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
import csv
import io
import json
import math
from pathlib import Path
import re

from moneygraph.io.snapshots import ARTIFACTS, SCHEMA_VERSION, identity_hash

from .errors import InvalidContext

CONTEXT_VERSION = "aml-facts-v1"
MAX_CONTEXT_BYTES = 24000
ROLES = {
    "consolidator": "сборщик", "transit": "транзитный участник",
    "distributor": "распределитель", "terminal": "возможный конечный получатель",
    "coordinator": "возможный координатор", "peripheral": "периферийный участник",
}
LIMITATIONS = {
    "partial_network": "Видна только часть внутрибанковской сети по исходящему обходу; внешние связи и полный баланс неизвестны.",
    "date_only": "Даты имеют точность до дня; порядок операций внутри дня неизвестен.",
    "structural_paths": "Направленный путь показывает связи, но не доказывает движение одной и той же суммы.",
    "not_crime_probability": "Роль — гипотеза по правилам. Поддержка роли, её оценка и приоритет не являются вероятностью преступления.",
    "sampling_threshold": "Выборка ограничена порогом суммы перевода; операции ниже порога не представлены.",
    "depth_boundary": "Четвёртое колено — граница обхода. Отсутствие видимых исходящих не доказывает удержание денег.",
    "seed_inflow_incomplete": "Входящие средства исходного клиента неполны из-за способа сбора выборки.",
    "isolated": "В выборке у клиента нет связей. Это не доказывает отсутствие операций вне выборки или безопасность клиента.",
    "out_exceeds_observed_in": "Исходящие превышают видимые входящие; это не доказывает аномалию при неполной выборке.",
    "incoming_date_unavailable": "Дата последнего входящего перевода неизвестна.",
    "active_in_days_unavailable": "Число дней с входящими переводами неизвестно.",
    "month_end_window": "Конец периода ограничивает наблюдение дальнейших переводов.",
    "terminal_observed_only": "Конец цепочки предполагается только в этой выборке; удержание денег не доказано.",
    "no_temporal_confirmation": "Временного подтверждения транзита нет.",
    "temporal_confirmed": "Даты совместимы с транзитом, но не доказывают передачу тех же денег.",
    "ambiguous_roles": "Несколько ролей имеют близкую поддержку; итоговая оценка роли ограничена правилами.",
    "missing_values": "Некоторые показатели отсутствуют. Неизвестное значение нельзя считать нулём.",
}
CHECKS = {
    "inspect_transactions": ("in_product", "Посмотреть операции клиента, суммы, даты и контрагентов"),
    "inspect_paths": ("in_product", "Посмотреть направленные связи с исходными клиентами"),
    "inspect_community": ("in_product", "Посмотреть состав сообщества и его наблюдаемые связи"),
    "request_payment_purpose": ("request_data", "Запросить назначения платежей"),
    "request_relationship_context": ("request_data", "Запросить контекст отношений с контрагентами"),
    "request_full_history": ("request_data", "Запросить полную историю входящих и исходящих за более широкий период"),
    "request_intraday_times": ("request_data", "Запросить время операций внутри дня"),
}


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _integer(value, *, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r"\d+", str(value)):
        raise InvalidContext("Expected an exact nonnegative integer")
    return int(value)


def format_money(value):
    if value is None:
        return None
    minor = _integer(value)
    return f"{minor // 100}.{minor % 100:02d} KZT"


def _score(value):
    if value is None:
        return None
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
        raise InvalidContext("Invalid score")
    # Scores are not money. Match the UI's 0..100 scale, with an explicit label.
    return f"{value * 100:.2f} / 100"


def _numeric_mapping(value, *, boolean=False):
    if not isinstance(value, dict) or len(value) > 24:
        raise InvalidContext("Invalid rule mapping")
    result = {}
    for key, item in value.items():
        if not re.fullmatch(r"[a-z_]{1,60}", key):
            raise InvalidContext("Invalid rule key")
        if boolean:
            if type(item) is not bool:
                raise InvalidContext("Invalid rule condition")
            result[key] = item
        else:
            result[key] = _score(item)
    return result


@dataclass(frozen=True)
class Fact:
    id: str
    key: str
    label: str
    value_json: str
    source: str

    @property
    def value(self):
        return json.loads(self.value_json)

    def to_dict(self):
        return {"id": self.id, "key": self.key, "label": self.label,
                "value": self.value, "source": self.source}


@dataclass(frozen=True)
class ClientContext:
    snapshot_id: str
    client_id: str = field(repr=False)
    facts: tuple[Fact, ...]
    alias_map: tuple[tuple[str, str], ...] = field(repr=False)

    def packet(self) -> dict:
        # Neither run identity nor real gids are sent to the provider.
        return {"version": CONTEXT_VERSION, "subject": "Клиент_A",
                "facts": [fact.to_dict() for fact in self.facts]}

    @property
    def digest(self) -> str:
        return sha256(json_bytes(self.packet())).hexdigest()


class SnapshotContextBuilder:
    """Load once at server startup; resolve only the pinned run, never current.json.

    Byte hashes are checked after reading, so later path replacements cannot mix
    data in the context. Free-text evidence/cluster hypotheses are intentionally
    excluded: structured conditions already explain the decision without leaking
    embedded client identifiers or importing instructions from those fields.
    """

    def __init__(self, directory: Path):
        try:
            directory = Path(directory).resolve()
            manifest = json.loads((directory / "manifest.json").read_bytes())
            if (manifest["schema_version"] != SCHEMA_VERSION
                    or manifest["run_id"] != identity_hash(manifest["identity"])
                    or set(manifest["artifacts"]) != ARTIFACTS):
                raise InvalidContext("Invalid snapshot manifest")
            blobs = {}
            for name, expected in manifest["artifacts"].items():
                path = directory / name
                if path.is_symlink():
                    raise InvalidContext("Symlink artifact")
                blob = path.read_bytes()
                if sha256(blob).hexdigest() != expected:
                    raise InvalidContext("Snapshot bytes changed")
                blobs[name] = blob
            graph = json.loads(blobs["graph.json"])
            if graph.get("directed") is not True or graph.get("currency") != "KZT" or graph.get("scale") != 2:
                raise InvalidContext("Unexpected graph units")
            self.run_id = manifest["run_id"]
            self._config = manifest["identity"]["config"]
            self._nodes = self._index(graph["nodes"], lambda n: str(n["gid"]))
            records = [json.loads(line) for line in blobs["explanations.jsonl"].splitlines() if line.strip()]
            self._records = self._index(records, lambda n: str(n["features"]["gid"]))
            self._edges = {(str(e["src"]), str(e["dst"])) for e in graph["edges"]}
            clusters = csv.DictReader(io.StringIO(blobs["clusters.csv"].decode("utf-8-sig")))
            self._clusters = self._index(clusters, lambda c: str(c["cluster_id"]))
            if self._nodes.keys() != self._records.keys():
                raise InvalidContext("Snapshot node sets differ")
        except (KeyError, TypeError, ValueError, OSError):
            raise InvalidContext("Cannot read a completed compatible snapshot") from None

    @staticmethod
    def _index(rows, key):
        result = {}
        for row in rows:
            item = key(row)
            if item in result:
                raise InvalidContext("Duplicate snapshot record")
            result[item] = row
        return result

    def for_client(self, gid: str | int) -> ClientContext:
        gid = str(gid)
        if gid not in self._nodes:
            raise InvalidContext("Client missing from snapshot")
        try:
            return self._build(gid)
        except (KeyError, TypeError, ValueError):
            raise InvalidContext("Client facts are incomplete or inconsistent") from None

    def _build(self, gid):
        node, record = self._nodes[gid], self._records[gid]
        assignment = record["assignment"]
        for key in ("role", "role_score"):
            if node[key] != assignment[key]:
                raise InvalidContext("Graph and explanation disagree")
        if node["priority_score"] != record["priority_score"]:
            raise InvalidContext("Priority mismatch")
        for key, value in record["features"].items():
            if key in node and json_bytes(node[key]) != json_bytes(value):
                raise InvalidContext("Feature mismatch")
        facts = []
        aliases = {gid: "Клиент_A"}

        def add(key, label, value, source):
            facts.append(Fact(f"F{len(facts)+1}", key, label,
                              json_bytes(value).decode("utf-8"), source))

        start = date.fromisoformat(self._config["period_start"])
        end = date.fromisoformat(self._config["period_end"])
        if start > end:
            raise InvalidContext("Invalid period")
        add("period", "Период наблюдения", {"start": start.isoformat(), "end": end.isoformat()}, "manifest.identity.config")
        role = assignment["role"]
        add("role", "Рассчитанная роль, не изменять", {"code": role, "label": ROLES[role]}, "explanations.assignment.role")
        for key, label, value, source in (
            ("role_support", "Исходная поддержка роли до ограничений", assignment["support"], "assignment.support"),
            ("role_score", "Оценка роли после ограничений", assignment["role_score"], "assignment.role_score"),
            ("priority", "Приоритет проверки, не вероятность преступления", record["priority_score"], "priority_score"),
        ):
            add(key, label, _score(value), "explanations.jsonl." + source)
        add("contributions", "Вклады в приоритет в пунктах шкалы 0–100: seed_reach — охват исходных клиентов; role_support — поддержка роли; betweenness — положение между узлами; volume — объём",
            _numeric_mapping(record["contributions"]), "explanations.contributions")
        if not math.isclose(sum(record["contributions"].values()), record["priority_score"], abs_tol=1e-9):
            raise InvalidContext("Contributions mismatch")
        primary = next((c for c in assignment["candidates"] if c["role"] == role), None)
        add("rule", "Условия выбранной роли; для периферийной роли ни один кандидат не принят",
            None if primary is None else {"conditions": _numeric_mapping(primary["conditions"], boolean=True),
                                          "terms": _numeric_mapping(primary["terms"])}, "explanations.assignment.candidates")
        policy = record["policy"]
        if (not isinstance(policy, dict) or len(policy) > 20
                or any(not re.fullmatch(r"[a-z_]{1,60}", key) or type(value) not in (int, float)
                       or not math.isfinite(value) or not 0 <= value <= 1000
                       for key, value in policy.items())):
            raise InvalidContext("Invalid scoring policy")
        add("policy", "Параметры существующих правил скоринга; не менять", policy, "explanations.policy")
        add("caps", "Ограничения оценки роли, не ограничения приоритета", _numeric_mapping(assignment["caps"]), "explanations.assignment.caps")
        for key, label in (("in_degree", "Разных плательщиков"), ("out_degree", "Разных получателей"),
                           ("in_tx", "Входящих операций"), ("out_tx", "Исходящих операций"),
                           ("reachable_seeds", "Достижимость от исходных клиентов по направлению переводов"),
                           ("depth", "Минимальное колено обхода")):
            add(key, label, _integer(node.get(key), nullable=True), "graph.nodes." + key)
        for key, label in (("in_minor", "Входящая сумма в наблюдаемой сети"),
                           ("out_minor", "Исходящая сумма в наблюдаемой сети")):
            add(key, label, format_money(node.get(key)), "graph.nodes." + key)
        for key, label in (("is_seed", "Исходный клиент"), ("depth_boundary", "Граница обхода"),
                           ("isolated", "Нет наблюдаемых связей")):
            value = node.get(key)
            if value is not None and type(value) is not bool:
                raise InvalidContext("Invalid observation flag")
            add(key, label, value, "graph.nodes." + key)
        paths = []
        for raw in node.get("seed_paths", [])[:3]:
            path = [str(item) for item in raw]
            if (not path or len(path) > 5 or path[-1] != gid or path[0] not in self._nodes
                    or not self._nodes[path[0]]["is_seed"]
                    or any(pair not in self._edges for pair in zip(path, path[1:]))):
                raise InvalidContext("Invalid directed seed path")
            for item in path:
                if item not in aliases:
                    aliases[item] = f"Клиент_{chr(65 + len(aliases))}"
            paths.append([aliases[item] for item in path])
        add("paths", "До трёх примеров структурных направленных путей; последний участник — Клиент_A",
            paths, "graph.nodes.seed_paths")
        cluster = self._clusters[str(node["cluster_id"])]
        add("community", "Сообщество клиента (плотные связи, не доказанная преступная группа)",
            {"n_nodes": _integer(cluster["n_nodes"]), "n_seed": _integer(cluster["n_seed"])}, "clusters.csv")
        add("threshold", "Порог исходной выборки", format_money(self._config["min_amount_minor"]), "manifest.identity.config.min_amount_minor")
        required = {"partial_network", "date_only", "structural_paths", "not_crime_probability", "sampling_threshold"}
        required.update(assignment["limitations"])
        required.update(assignment["caps"])
        if node.get("depth") == 4:
            required.add("depth_boundary")
        if node.get("is_seed"):
            required.add("seed_inflow_incomplete")
        if node.get("isolated"):
            required.add("isolated")
        if any(f.value is None for f in facts if f.key != "rule"):
            required.add("missing_values")
        for code in sorted(required):
            if code not in LIMITATIONS:
                raise InvalidContext("Unknown limitation; update the adapter")
            add("limitation:" + code, "Обязательное ограничение", LIMITATIONS[code], "snapshot flags / analysis contract")
        # Fixed available actions, never claims invented by the model.
        for action, (availability, label) in CHECKS.items():
            add("action:" + action, "Доступная проверка",
                {"action": action, "availability": availability, "description": label}, "product capability contract")
        context = ClientContext(self.run_id, gid, tuple(facts), tuple((a, g) for g, a in aliases.items()))
        if len(json_bytes(context.packet())) > MAX_CONTEXT_BYTES:
            raise InvalidContext("Fact packet exceeds budget")
        return context
