"""Generate a local HTML chart and exact JSON for role-A daily-activity review."""
import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from moneygraph.analytics.temporal import daily_activity
from moneygraph.io.load import load_dataset


def money(value):
    amount = int(value)
    sign = "−" if amount < 0 else ""
    major, minor = divmod(abs(amount), 100)
    return f"{sign}{major:,}".replace(",", " ") + f",{minor:02d} ₸"


def render_html(result):
    """Offline review only; client identifiers and financial data stay local."""
    days = result["days"]
    maximum = max((int(r[k]) for r in days for k in ("in_minor", "out_minor")), default=0)
    width = max(360, 48 * len(days) + 40)
    bars = []
    for index, day in enumerate(days):
        x = 20 + index * 48
        for offset, key, label, color in [(0, "in_minor", "Поступления", "#177c70"),
                                         (18, "out_minor", "Отправления", "#7957bd")]:
            value = int(day[key])
            height = max(1, value * 180 // maximum) if value else 0
            title = escape(f'{day["date"]}: {label} {money(value)}')
            bars.append(f'<rect x="{x + offset}" y="{210 - height}" width="16" height="{height}" fill="{color}"><title>{title}</title></rect>')
        bars.append(f'<text x="{x + 16}" y="232" text-anchor="middle">{day["date"][8:]}</text>')
    table = "".join(
        f'<tr><td>{d["date"]}</td><td>{money(d["in_minor"])}</td>'
        f'<td>{money(d["out_minor"])}</td><td>{d["in_tx"]} / {d["out_tx"]}</td>'
        f'<td>{d["n_transactions"]}</td></tr>' for d in days)
    totals = result["totals"]
    if not result["available"]:
        body = "<p>История операций недоступна. Нулевые суммы не рассчитаны.</p>"
    else:
        body = f'''<div class="totals">
          <p><span>Поступления</span><strong>{money(totals["in_minor"])}</strong></p>
          <p><span>Отправления</span><strong>{money(totals["out_minor"])}</strong></p>
          <p><span>Операций / активных дней</span><strong>{totals["n_transactions"]} / {totals["active_days"]}</strong></p>
        </div>
        <p class="legend"><b class="incoming">■ Поступления</b> <b class="outgoing">■ Отправления</b></p>
        <div class="chart"><svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="250" viewBox="0 0 {width} 250" role="img" aria-label="Поступления и отправления по дням. Точные значения в таблице ниже.">
        <line x1="12" y1="210" x2="{width - 10}" y2="210" stroke="#bac8ce"/>{''.join(bars)}</svg></div>
        <p>День месяца; самый высокий столбец — {money(maximum)}. Наведите на столбец для точной суммы. Дни без видимых операций показаны нулями.</p>
        <details><summary>Точные суммы и операции за каждый день</summary>
        <div class="chart"><table><thead><tr><th>Дата</th><th>Поступления</th><th>Отправления</th><th>Входящих / исходящих операций</th><th>Всего операций</th></tr></thead><tbody>{table}</tbody></table></div></details>'''
    flags = set(result["warnings"])
    limitations = ["Показаны только операции из предоставленной выборки. Нулевой день не означает отсутствие других переводов.",
                   "Точность дат — день: порядок операций внутри дня неизвестен. График не доказывает движение одной и той же суммы."]
    if "seed_inflow_incomplete" in flags:
        limitations.append("Это исходный клиент: входящие переводы могут быть представлены неполно.")
    if "depth_boundary" in flags:
        limitations.append("Граница глубины выгрузки: дальнейшие переводы неизвестны.")
    if "self_transfer" in flags:
        limitations.append("Перевод самому себе включён в оба направления, но в общем числе операций учитывается один раз.")
    warnings = "".join(f"<li>{escape(t)}</li>" for t in limitations)
    period = result["period"]
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Дневная активность клиента</title><style>
    body{{font:16px/1.5 system-ui,sans-serif;color:#19313c;background:#f1f5f6;margin:0;padding:24px}}
    main{{max-width:1120px;margin:auto;background:white;padding:28px;border-radius:16px}}h1{{margin:8px 0}}h2{{font-size:19px}}
    .eyebrow{{color:#526c79;font-size:13px}}.totals{{display:flex;gap:32px;flex-wrap:wrap}}.totals span,.totals strong{{display:block}}.totals strong{{font-size:24px}}
    .chart{{overflow:auto}}svg text{{font:12px system-ui;fill:#526c79}}.incoming{{color:#177c70}}.outgoing{{color:#7957bd}}.legend b{{margin-right:20px}}
    table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{text-align:right;padding:10px;border-bottom:1px solid #dce5e8}}th:first-child,td:first-child{{text-align:left}}
    summary{{cursor:pointer;font-weight:600;padding:12px 0}}.limits{{background:#f5f7fa;padding:16px;border-radius:10px;margin-top:24px}}li{{margin:8px 0}}
    </style><main><div class="eyebrow">ЛОКАЛЬНАЯ ПРОВЕРКА · ДАННЫЕ И ГРАФ</div>
    <h1>Когда клиент получал и отправлял деньги</h1>
    <p>ID клиента: {escape(result["gid"])}<br>Период: {escape(period["start"])} — {escape(period["end"])}</p>
    {body}<section class="limits"><h2>Как читать результат</h2><ul>{warnings}</ul>
    <p>Суммы относятся к наблюдаемым переводам и не показывают остаток на счёте.</p></section>
    <p>Этот файл содержит финансовые данные. Не добавляйте его в Git.</p></main></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--gid", help="Existing client ID; default: most observed operations, ties by gid")
    parser.add_argument("--period-start", default="2026-07-01")
    parser.add_argument("--period-end", default="2026-07-31")
    args = parser.parse_args()
    destinations = [args.out / f"daily-activity.local.{ext}" for ext in ("json", "html")]
    if any(p.exists() for p in destinations):
        parser.error("Output already exists; choose another --out directory")
    data = load_dataset(args.data, period_start=args.period_start, period_end=args.period_end)
    nodes = {str(n["gid"]): n for n in data.nodes.to_dict("records")}
    records = data.transactions.to_dict("records")
    if args.gid is None:
        counts = Counter()
        for tx in records:
            counts.update({str(tx["src"]), str(tx["dst"])})
        gid = min(nodes, key=lambda key: (-counts[key], int(key)))
    else:
        gid = args.gid
    if gid not in nodes:
        parser.error("Unknown gid in the supplied dataset")
    result = daily_activity(nodes[gid], records, period_start=args.period_start, period_end=args.period_end)
    result["input_sha256"] = data.quality["input_sha256"]
    args.out.mkdir(parents=True, exist_ok=True)
    contents = [json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n", render_html(result)]
    for path, content in zip(destinations, contents):
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
        print(path)


if __name__ == "__main__":
    main()
