import { useEffect, useRef, useState } from "react";
import { request, runPath, errorMessage } from "./api";

type Status = { visible: boolean; ready: boolean; code: string; message: string; provider?: string; model?: string; external?: boolean };
type Fact = { id: string; label: string; value: unknown; source: string };
type Statement = { text: string; fact_ids: string[]; action?: string; availability?: string };
type Explanation = {
  client_id: string;
  summary: Statement[]; reasons: Statement[]; alternative_hypotheses: Statement[];
  next_checks: Statement[]; limitations: Statement[]; facts: Fact[];
  metadata: { snapshot_id: string; provider: string; model: string; cached: boolean; human_review_required: boolean };
};

function factText(value: unknown): string {
  if (value === null || value === undefined) return "Неизвестно";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

export function AIAssistant({ run, gid, onTransactions, onPath, onCommunity }: {
  run: string; gid: string; onTransactions: () => void; onPath: () => void; onCommunity: () => void;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [data, setData] = useState<Explanation | null>(null);
  const [fact, setFact] = useState<Fact | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const pending = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const factPanel = useRef<HTMLDivElement>(null);
  const root = `${runPath(run)}/nodes/${encodeURIComponent(gid)}`;
  useEffect(() => {
    const controller = new AbortController();
    setStatus(null); setError("");
    request<Status>(`${root}/ai-status`, controller.signal).then(result => {
      if (!controller.signal.aborted) setStatus(result.data);
    }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [root, reload]);
  useEffect(() => () => { generation.current++; pending.current?.abort(); }, [root]);
  useEffect(() => { if (fact) factPanel.current?.focus(); }, [fact]);

  async function generate() {
    if (busy) return;
    const id = ++generation.current;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true); setError(""); setFact(null); setData(null);
    const timeout = setTimeout(() => controller.abort(), 125000);
    try {
      const response = await fetch(`${root}/ai-explanation`, {
        method: "POST", signal: controller.signal,
        headers: { "X-Moneygraph-Client": "local-ui", Accept: "application/json" },
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.message ?? "AI не ответил. Попробуйте позже.");
      if (body.run_id !== run || body.data?.client_id !== gid || body.data?.metadata?.snapshot_id !== run)
        throw new Error("Ответ относится к другому расчёту или клиенту и не показан.");
      if (generation.current === id && !controller.signal.aborted) setData(body.data);
    } catch (error) {
      if (generation.current === id) setError(controller.signal.aborted
        ? "Время ожидания истекло. Обычное объяснение и операции доступны."
        : errorMessage(error));
    } finally {
      clearTimeout(timeout);
      if (generation.current === id) setBusy(false);
    }
  }

  function statements(title: string, items: Statement[]) {
    return <section className="ai-section"><h4>{title}</h4><ul>{items.map((item, index) => <li key={index}>
      <p>{item.text}</p>
      <div className="ai-citations">{item.fact_ids.map(id => <button key={id} className="ai-citation"
        aria-label={`Показать факт ${id}`} onClick={() => setFact(data?.facts.find(value => value.id === id) ?? null)}>{id}</button>)}</div>
      {item.availability === "request_data" && <small>Нужны дополнительные данные — в этой выборке их нет.</small>}
      {item.availability === "in_product" && item.action === "inspect_transactions" && <button className="button small" onClick={onTransactions}>Открыть операции</button>}
      {item.availability === "in_product" && item.action === "inspect_paths" && <button className="button small" onClick={onPath}>Открыть цепочку</button>}
      {item.availability === "in_product" && item.action === "inspect_community" && <button className="button small" onClick={onCommunity}>Показать группу на графе</button>}
    </li>)}</ul></section>;
  }

  return <section className="ai-assistant" aria-label="AI-помощник клиента">
    <span className="ai-preview">Тестовая функция</span>
    <h3>Записка AI по фактам расчёта</h3>
    <p>AI помогает прочитать факты. Он не меняет роль или приоритет и не устанавливает нарушения.</p>
    <p className="fine-print">«Клиент_A» в записке — выбранный клиент {gid}. Остальные обозначения — участники его связей.</p>
    {status ? <>
      {status.ready ? <p className="ai-destination">{status.external
        ? `По нажатию факты о суммах и связях будут переданы провайдеру ${status.provider}. ID заменяются обозначениями, но финансовые сведения остаются в запросе.`
        : "По нажатию факты будут отправлены модели на этом компьютере."} Модель: {status.model}.</p>
        : <p role="status">{status.message} Обычное объяснение доступно во вкладке «Обзор».</p>}
      <div className="investigation-actions">
        <button className="button primary" disabled={!status.ready || busy} onClick={generate}>{busy ? "AI составляет и проверяет записку…" : data ? "Получить записку повторно" : "Объяснить с AI"}</button>
        {!status.ready && <button className="button small" onClick={() => setReload(value => value + 1)}>Проверить настройки снова</button>}
      </div>
    </> : <p role="status">Проверяем доступность AI…</p>}
    {busy && <p role="status">Два этапа: составление записки и проверка её утверждений. Можно продолжать исследование — старый ответ не появится у другого клиента.</p>}
    {error && <div className="ai-error" role="alert"><p>{error}</p><button className="button small" disabled={busy} onClick={() => setReload(value => value + 1)}>Обновить состояние AI</button></div>}
    {fact && <div className="ai-fact" role="region" aria-label={`Факт ${fact.id}`} ref={factPanel} tabIndex={-1}>
      <strong>{fact.id} · {fact.label}</strong><pre>{factText(fact.value)}</pre><small>Источник: {fact.source}</small>
      <button className="text-button" onClick={() => setFact(null)}>Закрыть факт</button>
    </div>}
    {data && <div className="ai-result" aria-label="Записка AI">
      <p className="ai-review">Проверьте выводы по ссылкам F… — это исходные факты расчёта. Записка требует проверки аналитиком.</p>
      <p className="fine-print">{data.metadata.provider} · {data.metadata.model} · {data.metadata.cached ? "Сохранённый ответ, без нового вызова модели" : "Новый ответ модели"}</p>
      {statements("Кратко о клиенте", data.summary)}
      {statements("Почему проверить", data.reasons)}
      {statements("Другие возможные объяснения", data.alternative_hypotheses)}
      {statements("Что проверить дальше", data.next_checks)}
      {statements("Ограничения выводов", data.limitations)}
    </div>}
  </section>;
}
