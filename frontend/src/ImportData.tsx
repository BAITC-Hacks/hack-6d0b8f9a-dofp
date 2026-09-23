import { useState } from "react";
import { LoaderCircle, Upload, CheckCircle2 } from "lucide-react";

const names = ["nodes.parquet", "edges.parquet", "transactions.parquet"];
type Job = {
  id: string;
  state: string;
  message: string;
  run_id: string | null;
};

async function call(
  path: string,
  method = "GET",
  body?: BodyInit,
  json = false,
): Promise<Job> {
  const response = await fetch("/api/v1/imports" + path, {
    method,
    body,
    headers: {
      "X-Moneygraph-Client": "local-ui",
      ...(json ? { "Content-Type": "application/json" } : {}),
    },
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : "Проверьте даты и выбранные файлы.",
    );
  return result;
}

export function ImportData({
  onComplete,
  onBusy,
  period,
}: {
  onComplete: () => void;
  onBusy: (busy: boolean) => void;
  period?: { start: string; end: string };
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [start, setStart] = useState(period?.start ?? "");
  const [end, setEnd] = useState(period?.end ?? "");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const valid =
    files.length === 3 &&
    names.every((name) => files.some((file) => file.name === name));
  async function run() {
    setError("");
    if (!valid) {
      setError("Выберите ровно три файла с указанными именами.");
      return;
    }
    if (files.some((file) => file.size > 40 * 1024 * 1024)) {
      setError("Каждый файл должен быть не больше 40 МБ.");
      return;
    }
    setBusy(true);
    onBusy(true);
    try {
      setMessage("Готовим загрузку…");
      let job = await call(
        "",
        "POST",
        JSON.stringify({ period_start: start, period_end: end }),
        true,
      );
      for (const file of files) {
        setMessage("Загружаем " + file.name + "…");
        await call(
          `/${job.id}/files/${encodeURIComponent(file.name)}`,
          "PUT",
          file,
        );
      }
      job = await call(`/${job.id}/run`, "POST");
      while (job.state === "running") {
        setMessage(job.message);
        await new Promise((resolve) => setTimeout(resolve, 750));
        job = await call("/" + job.id);
      }
      if (job.state !== "complete") throw new Error(job.message);
      onComplete();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить данные.");
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <form
      className="import-content"
      onSubmit={(e) => {
        e.preventDefault();
        void run();
      }}
    >
      <p className="modal-intro">
        Выберите три файла одного набора. Они останутся на этом компьютере.
        После проверки приложение построит новый граф и откроет результат.
      </p>
      <label className="upload-picker">
        <Upload size={24} />
        <strong>Выбрать файлы данных</strong>
        <span>nodes.parquet · edges.parquet · transactions.parquet</span>
        <input
          aria-label="Файлы нового набора"
          type="file"
          accept=".parquet"
          multiple
          disabled={busy}
          onChange={(e) => {
            const selected = Array.from(e.target.files ?? []);
            setFiles(selected);
            setError(selected.length && (selected.length !== 3 || !names.every(name => selected.some(file => file.name === name)))
              ? "Выберите одновременно три файла: nodes.parquet, edges.parquet и transactions.parquet."
              : "");
          }}
        />
      </label>
      <ul className="file-checklist">
        {names.map((name, i) => (
          <li key={name}>
            <CheckCircle2
              size={16}
              color={files.some((f) => f.name === name) ? "#168b6b" : "#abb4af"}
            />
            <span>
              <strong>
                {["Клиенты", "Связи между клиентами", "Отдельные переводы"][i]}
              </strong>
              <small>{name}</small>
            </span>
          </li>
        ))}
      </ul>
      <div className="date-inputs">
        <label>
          Начало периода
          <input
            type="date"
            value={start}
            required
            disabled={busy}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          Конец периода
          <input
            type="date"
            min={start}
            value={end}
            required
            disabled={busy}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
      </div>
      <p className="fine-print">
        Проверьте, что даты охватывают все переводы. Лимиты: 40 МБ на файл, 10
        000 клиентов, 50 000 связей и 200 000 переводов. Обычные XLSX и CSV на
        вход пока не поддерживаются.
      </p>
      <details className="technical-details">
        <summary>Какой формат нужен</summary>
        <p>
          Клиенты: gid, depth, is_seed. Связи: src, dst, sum_kzt, n_tx, depth.
          Переводы: src, dst, date, sum_kzt. Номера — целые int64, суммы —
          тенге, даты — без времени. Поиск начинается от исходных клиентов и
          ограничен четырьмя шагами. Набор должен соответствовать выборке
          переводов от 5 000 ₸.
        </p>
      </details>
      {error && (
        <p className="import-error" role="alert">
          {error} Текущий результат не заменён.
        </p>
      )}
      {busy && (
        <p className="import-progress" role="status">
          <LoaderCircle className="spin" size={18} />
          {message}
        </p>
      )}
      <button
        className="button primary"
        disabled={!valid || !start || !end || busy}
        type="submit"
      >
        {busy ? "Выполняется расчёт…" : "Загрузить и рассчитать"}
      </button>
    </form>
  );
}
