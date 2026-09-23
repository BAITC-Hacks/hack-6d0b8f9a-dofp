import { useState } from "react";
import { money, number, score, roleInfo, warningLabels, capLabels, type NodeData } from "./types";

export function investigationSummary(node: NodeData, run: string, period: string): string {
  const caps = Object.keys(node.rule_details?.assignment?.caps ?? {}).map(key => capLabels[key] ?? key);
  return [
    `ID клиента: ${node.gid}`,
    `Период анализа: ${period}`,
    `Роль-гипотеза: ${roleInfo[node.role].label}`,
    `Приоритет проверки: ${score(node.priority_score)}/100; место ${node.rank} в общей очереди.`,
    `Поддержка роли: ${score(node.role_score)}/100. Эти оценки не являются вероятностью преступления.`,
    `Почему проверить: ${node.explanation ?? node.evidence}`,
    node.priority_reason ?? "",
    `Получено: ${money(node.metrics.in_minor)}; плательщиков: ${number(node.metrics.in_deg)}.`,
    `Отправлено: ${money(node.metrics.out_minor)}; получателей: ${number(node.metrics.out_deg)}.`,
    `Достижимых исходных клиентов: ${number(node.metrics.reachable_seeds)}. Это структурные связи, не доказательство движения одной суммы.`,
    `Ограничения: ${[...node.warnings.map(key => warningLabels[key] ?? key), ...caps].join(" ") || "Не переданы в расчёте."}`,
    "Роль требует проверки аналитиком; суммы переводов не являются остатком счёта.",
    `Расчёт: ${run}`,
  ].filter(Boolean).join("\n");
}

export function InvestigationIntro({ node, run, period, transactionCount, onTransactions, onPath, onExport }: {
  node: NodeData; run: string; period: string; transactionCount: number | null;
  onTransactions: () => void; onPath: () => void; onExport: () => void;
}) {
  const [copyState, setCopyState] = useState("");
  const [manualCopy, setManualCopy] = useState(false);
  const text = investigationSummary(node, run, period);
  const caps = Object.keys(node.rule_details?.assignment?.caps ?? {}).map(key => capLabels[key] ?? key);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopyState("Резюме скопировано");
      setManualCopy(false);
    } catch {
      setManualCopy(true);
      setCopyState("Выделите текст ниже и скопируйте вручную");
    }
  }
  return <section className="investigation-intro" aria-label="Причины проверки клиента">
    <h3>Почему этот клиент в очереди?</h3>
    <p className="client-explanation">{node.explanation ?? node.evidence}</p>
    <p className="priority-reason">{node.priority_reason}</p>
    <div className="investigation-actions">
      <button className="button primary" onClick={onTransactions}>Проверить переводы{transactionCount === null ? "" : ` (${transactionCount})`}</button>
      {!!node.paths.length && <button className="button" onClick={onPath}>Показать связь с исходными клиентами</button>}
    </div>
    <dl className="client-facts">
      <div><dt>Получено от клиентов: {number(node.metrics.in_deg)}</dt><dd>{money(node.metrics.in_minor, true)}</dd></div>
      <div><dt>Отправлено клиентам: {number(node.metrics.out_deg)}</dt><dd>{money(node.metrics.out_minor, true)}</dd></div>
      <div><dt>Есть пути от исходных клиентов</dt><dd>{number(node.metrics.reachable_seeds)}</dd></div>
    </dl>
    <div className="score-explanation">
      <div><strong>{score(node.priority_score)}<small>/100</small></strong><span>Приоритет проверки</span><p>Место {node.rank} в общей очереди. Кого полезнее изучить раньше.</p></div>
      <div><strong>{score(node.role_score)}<small>/100</small></strong><span>Поддержка роли</span><p>Насколько признаки подходят гипотезе с учётом ограничений.</p></div>
    </div>
    <p className="fine-print">Оценки отвечают на разные вопросы. Ни одна не означает вероятность преступления.</p>
    {!!caps.length && <p className="analysis-limit">Оценку роли ограничивают: {caps.join("; ").toLowerCase()}.</p>}
    <details className="result-actions"><summary>Передать результат проверки</summary>
      <p>Резюме — по выбранному клиенту. Excel — по всему набору.</p>
      <div className="investigation-actions"><button className="button small" onClick={copy}>Копировать резюме</button><button className="button small" onClick={onExport}>Excel всего набора</button></div>
      {copyState && <p role="status">{copyState}</p>}
      {manualCopy && <textarea aria-label="Текст резюме для копирования" readOnly value={text} onFocus={event => event.target.select()} rows={10} />}
    </details>
  </section>;
}
