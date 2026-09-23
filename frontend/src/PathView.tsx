import { useEffect, useState } from "react";
import { request, runPath, errorMessage } from "./api";
import { NetworkGraph } from "./NetworkGraph";
import { money, type GraphData } from "./types";

type SavedPath = GraphData & {
  state: "found" | "no_saved_paths" | "paths_unavailable";
  path_index: number | null;
  available_paths: number | null;
  path: string[];
  warnings: string[];
};

export function PathView({ run, gid, initialIndex, onClose, onSelect }: {
  run: string; gid: string; initialIndex: number;
  onClose: () => void; onSelect: (gid: string) => void;
}) {
  const [index, setIndex] = useState(initialIndex);
  const [data, setData] = useState<SavedPath | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    request<SavedPath>(`${runPath(run)}/nodes/${encodeURIComponent(gid)}/path-view?path_index=${index}`, controller.signal)
      .then(response => { if (!controller.signal.aborted) setData(response.data); })
      .catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [run, gid, index, retry]);
  return <section className="path-view" aria-label="Полная сохранённая цепочка">
    <div className="path-view-toolbar"><strong>Клиент {gid}</strong><button className="button small" onClick={onClose}>Вернуться к сети</button></div>
    {error ? <div className="error-box" role="alert"><p>{error}</p><button className="button small" onClick={() => setRetry(value => value + 1)}>Повторить</button></div>
      : !data ? <p role="status">Загружаем сохранённую цепочку…</p> : <>
        {data.state === "found" ? <label className="path-choice">Сохранённый пример
          <select aria-label="Пример цепочки" value={index} onChange={event => { setData(null); setIndex(Number(event.target.value)); }}>
            {Array.from({ length: data.available_paths ?? 0 }, (_, i) => <option value={i} key={i}>Пример {i + 1} из {data.available_paths} · до четырёх переводов</option>)}
          </select>
        </label> : <p className="daily-message">{data.state === "paths_unavailable"
          ? "Данные о цепочках не подключены к этому расчёту."
          : "Для клиента нет сохранённых примеров цепочек. Это не доказывает отсутствие связей."}</p>}
        <div className="path-graph"><NetworkGraph data={data} selected={gid} onSelect={onSelect} colorMode="role" /></div>
        {data.state === "found" && <>
          <p className="path-caution">Структурная цепочка. Направления переводов подтверждены, но движение одной и той же суммы и хронологическая последовательность не доказаны.</p>
          <p className="fine-print">Шаг — положение в маршруте, а не роль или уровень участника. Суммы на стрелках относятся ко всему периоду; складывать их в «оборот цепочки» нельзя.</p>
          <ol className="path-edge-list">{data.edges.map(edge => <li key={`${edge.src}->${edge.dst}`} title={`${money(edge.sum_minor)} за период · операций: ${edge.n_tx}`}>
            <div><button onClick={() => onSelect(edge.src)}>{edge.src}</button><span> → </span><button onClick={() => onSelect(edge.dst)}>{edge.dst}</button></div>
            <strong>{money(edge.sum_minor)} за период · операций: {edge.n_tx}</strong>
          </li>)}</ol>
        </>}
        {data.warnings.includes("depth_boundary") && <p className="path-caution">В цепочке есть клиент на границе четвёртого шага: его дальнейшие переводы видны не полностью.</p>}
      </>}
  </section>;
}
