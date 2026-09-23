"""Show a full saved seed route in a local HTML file, without changing C's UI."""
import argparse
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from moneygraph.analytics.features import seed_reachability
from moneygraph.analytics.graph import build_graph, seed_path_view
from moneygraph.io.load import load_dataset


def money(value):
    major, minor = divmod(int(value), 100)
    return f"{major:,}".replace(",", " ") + f",{minor:02d} ₸"


def render_html(view):
    shapes, rows = [], []
    for step, node in enumerate(view["nodes"]):
        y = 30 + step * 145
        fill = "#d9eee7" if node["is_seed"] else "#e5ecf9"
        label = "Исходный клиент" if node["is_seed"] else "Клиент"
        if node["gid"] == view["focus_gid"]:
            label += " · выбранный"
        shapes.append(f'<rect x="170" y="{y}" width="390" height="68" rx="10" fill="{fill}" stroke="#758994"/>')
        shapes.append(f'<text x="365" y="{y + 24}" text-anchor="middle">{escape(label)}</text>')
        shapes.append(f'<text x="365" y="{y + 48}" text-anchor="middle" font-weight="bold">{escape(node["gid"])}</text>')
        if step < len(view["edges"]):
            edge = view["edges"][step]
            shapes.append(f'<line x1="365" y1="{y + 70}" x2="365" y2="{y + 137}" stroke="#177c70" stroke-width="3" marker-end="url(#arrow)"/>')
            shapes.append(f'<text x="390" y="{y + 98}">{money(edge["sum_minor"])}</text>')
            shapes.append(f'<text x="390" y="{y + 119}">Операций: {edge["n_tx"]}</text>')
            rows.append(f'<tr><td>{step + 1}</td><td>{edge["src"]}</td><td>{edge["dst"]}</td><td>{money(edge["sum_minor"])}</td><td>{edge["n_tx"]}</td></tr>')
    state = {"found": "Полная направленная цепочка", "no_saved_paths": "Сохранённых цепочек от исходных клиентов нет",
             "paths_unavailable": "Данные о цепочках недоступны"}[view["state"]]
    height = 120 + max(0, len(view["nodes"]) - 1) * 145
    path_label = "" if view["path_index"] is None else f' · Пример {view["path_index"] + 1} из {view["available_paths"]}'
    boundary = '<p>Маршрут содержит клиента на границе глубины выгрузки. Дальнейшие переводы могут быть неизвестны.</p>' if "depth_boundary" in view["warnings"] else ""
    period = view["period"]
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Цепочка переводов</title><style>
    body{{font:16px/1.5 system-ui,sans-serif;background:#f1f5f6;color:#19313c;margin:0;padding:24px}}
    main{{max-width:1100px;margin:auto;background:white;padding:28px;border-radius:16px}}h1{{font-size:27px}}h2{{font-size:19px}}
    .scroll{{overflow:auto}}svg{{display:block;margin:auto}}svg text{{font-size:15px;font-family:system-ui,sans-serif;fill:#19313c}}
    .notice{{background:#f5f7fa;padding:18px;border-radius:10px}}table{{border-collapse:collapse;white-space:nowrap;width:100%}}
    td,th{{text-align:left;padding:10px;border-bottom:1px solid #dce5e8}}small{{color:#526c79}}
    </style><main><small>ЛОКАЛЬНАЯ ПРОВЕРКА · ДАННЫЕ И ГРАФ</small>
    <h1>{state}</h1><p>Выбранный клиент: {escape(view["focus_gid"])}{path_label}<br>
    Период: {escape(period["start"])} — {escape(period["end"])}. Связей в цепочке: {view["total_edges"]}.</p>
    <div class="notice">Показаны реальные направления переводов. Суммы на стрелках — все наблюдаемые переводы между парой клиентов за период, а не сумма, прошедшая всю цепочку.</div>
    <div class="scroll"><svg xmlns="http://www.w3.org/2000/svg" width="760" height="{height}" viewBox="0 0 760 {height}" role="img" aria-label="Направленная цепочка сверху вниз. Точные связи приведены в таблице.">
    <defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="#177c70"/></marker></defs>
    {''.join(shapes)}</svg></div>
    <h2>Переводы, подтверждающие связи</h2><div class="scroll"><table><thead><tr><th>Шаг</th><th>Плательщик</th><th>Получатель</th><th>Сумма за период</th><th>Операций</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
    <section class="notice"><h2>Ограничения</h2><p>Это структурный маршрут. Хронологическая последовательность и движение одной и той же суммы не доказаны; точность дат в исходных данных — день.</p>
    <p>Показан сохранённый пример, а не все возможные маршруты. Порядок сверху вниз обозначает шаг цепочки, а не должность или иерархию.</p>{boundary}</section>
    <p>Файл содержит локальные финансовые данные. Не добавляйте его в Git.</p></main></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--gid", type=int)
    parser.add_argument("--path-index", type=int, help="Saved example index, starting at zero")
    parser.add_argument("--period-start", default="2026-07-01")
    parser.add_argument("--period-end", default="2026-07-31")
    args = parser.parse_args()
    destinations = [args.out / f"path-evidence.local.{ext}" for ext in ("json", "html")]
    if any(p.exists() for p in destinations):
        parser.error("Output already exists; choose another --out directory")
    data = load_dataset(args.data, period_start=args.period_start, period_end=args.period_end)
    graph = build_graph(data.nodes, data.edges)
    _, examples = seed_reachability(graph)
    gid, index = args.gid, args.path_index
    if gid is None:
        four_hop = [(g, i) for g in sorted(examples) for i, p in enumerate(examples[g]) if len(p) == 5]
        gid, automatic_index = four_hop[0] if four_hop else (min(graph), 0)
        if index is None:
            index = automatic_index
    if index is None:
        index = 0
    try:
        view = seed_path_view(graph, gid, examples.get(gid), path_index=index)
    except (KeyError, IndexError, ValueError) as exc:
        parser.error(f"Cannot show the requested path: {exc}")
    view["period"] = {"start": args.period_start, "end": args.period_end}
    view["input_sha256"] = data.quality["input_sha256"]
    args.out.mkdir(parents=True, exist_ok=True)
    contents = [json.dumps(view, ensure_ascii=False, allow_nan=False, indent=2) + "\n", render_html(view)]
    for path, content in zip(destinations, contents):
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
        print(path)


if __name__ == "__main__":
    main()
