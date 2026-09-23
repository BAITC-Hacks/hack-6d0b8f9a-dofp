import { useEffect, useState } from "react";
import { request, errorMessage, runPath } from "./api";
import { money, number, warningLabels } from "./types";

type Day = {
  date: string;
  in_minor: string;
  out_minor: string;
  in_tx: number;
  out_tx: number;
  self_tx: number;
  n_transactions: number;
};
type Activity = {
  gid: string;
  available: boolean;
  period: { start: string; end: string };
  days: Day[];
  totals: (Omit<Day, "date"> & { active_days: number }) | null;
  warnings: string[];
};
const dateLabel = (day: string) => new Date(day + "T00:00:00").toLocaleDateString("ru-RU");

export function DailyActivity({ run, gid }: { run: string; gid: string }) {
  const [data, setData] = useState<Activity | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    request<Activity>(`${runPath(run)}/nodes/${encodeURIComponent(gid)}/daily-activity`, controller.signal)
      .then(({ data }) => {
        if (controller.signal.aborted) return;
        setData(data);
        setSelected((data.days.find(day => day.n_transactions > 0) ?? data.days[0])?.date ?? "");
      })
      .catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [run, gid, retry]);

  if (error) return <div className="error-box" role="alert"><p>{error}</p><button className="button small" onClick={() => setRetry(value => value + 1)}>Повторить</button></div>;
  if (!data) return <p className="daily-message" role="status">Загружаем активность по дням…</p>;
  if (!data.available || !data.totals) return <div className="empty-state"><strong>История переводов недоступна</strong><p>Для этого расчёта не подключены операции. Это не означает, что переводов не было.</p></div>;

  const maximum = data.days.reduce((max, day) => [BigInt(day.in_minor), BigInt(day.out_minor), max].reduce((a, b) => a > b ? a : b), 0n);
  const height = (value: string) => maximum === 0n || BigInt(value) === 0n ? 0 : Math.max(1, Number(BigInt(value) * 1000n / maximum) / 10);
  const day = data.days.find(item => item.date === selected);
  return <section className="daily-activity" aria-label="Активность клиента по дням">
    <h4>Когда приходили и уходили деньги</h4>
    <p className="fine-print">{dateLabel(data.period.start)} — {dateLabel(data.period.end)}. Все переводы клиента из этого набора.</p>
    <div className="daily-totals">
      <div><span>Получено за период</span><strong>{money(data.totals.in_minor, true)}</strong></div>
      <div><span>Отправлено за период</span><strong>{money(data.totals.out_minor, true)}</strong></div>
    </div>
    <p className="fine-print">Операций: {number(data.totals.n_transactions)} · Дней с переводами: {number(data.totals.active_days)}</p>
    {data.totals.n_transactions === 0 && <p className="daily-message">В этой выборке за выбранный период переводов нет. Это не нулевой баланс счёта.</p>}
    <div className="daily-legend"><span>● Поступления</span><span>● Отправления</span></div>
    <p className="fine-print">Высота столбца: от 0 до {money(maximum.toString(), true)}. Выберите день. График можно прокрутить вправо.</p>
    <div className="daily-chart-scroll" tabIndex={0} aria-label="График переводов, прокрутка по дням">
      <div className="daily-chart" style={{ gridTemplateColumns: `repeat(${data.days.length}, minmax(28px, 1fr))` }}>
        {data.days.map(item => <button type="button" key={item.date} className="daily-day" aria-pressed={selected === item.date}
          onClick={() => setSelected(item.date)}
          aria-label={`${dateLabel(item.date)}: получено ${money(item.in_minor)}, отправлено ${money(item.out_minor)}, операций ${item.n_transactions}`}
          title={`${dateLabel(item.date)} · Получено ${money(item.in_minor)} · Отправлено ${money(item.out_minor)} · Операций ${item.n_transactions}`}>
          <span className="daily-bars" aria-hidden="true"><i style={{ height: `${height(item.in_minor)}%` }} /><i style={{ height: `${height(item.out_minor)}%` }} /></span>
          <span>{item.date.slice(8)}</span>
        </button>)}
      </div>
    </div>
    {day && <div className="daily-selected" aria-live="polite">
      <strong>{dateLabel(day.date)}</strong>
      <dl><dt>Получено</dt><dd>{money(day.in_minor)} · {day.in_tx} оп.</dd><dt>Отправлено</dt><dd>{money(day.out_minor)} · {day.out_tx} оп.</dd><dt>Всего операций</dt><dd>{day.n_transactions}</dd></dl>
      {day.self_tx > 0 && <p className="fine-print">Переводов самому себе: {day.self_tx}. Их суммы входят в оба направления, а в общем числе операций каждый учтён один раз.</p>}
    </div>}
    <details className="technical-details"><summary>Точные суммы за каждый день</summary>
      <div className="daily-table-scroll"><table className="daily-table"><caption>Наблюдаемые переводы за весь период</caption><thead><tr><th>Дата</th><th>Получено, ₸</th><th>Отправлено, ₸</th><th>Операций</th></tr></thead>
        <tbody>{data.days.map(item => <tr key={item.date}><th scope="row">{dateLabel(item.date)}</th><td>{money(item.in_minor)}</td><td>{money(item.out_minor)}</td><td>{item.n_transactions}</td></tr>)}</tbody>
        <tfoot><tr><th>Итого</th><td>{money(data.totals.in_minor)}</td><td>{money(data.totals.out_minor)}</td><td>{data.totals.n_transactions}</td></tr></tfoot></table></div>
    </details>
    <div className="limitations"><h4>Как читать график</h4><p>Это суммы переводов, а не остаток на счёте. Порядок операций внутри дня неизвестен.</p>
      {data.warnings.filter(item => ["seed_inflow_incomplete", "depth_boundary", "sampling_threshold", "intrabank_only"].includes(item)).map(item => <p key={item}>{warningLabels[item] ?? item}</p>)}
    </div>
  </section>;
}
