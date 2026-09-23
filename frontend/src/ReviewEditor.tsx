import { useEffect, useState } from "react";
import { errorMessage, request, runPath } from "./api";

type Review = { status: "not_started" | "in_progress" | "checked"; note: string; version: number; updated_at: string | null };
const labels = { not_started: "Не проверен", in_progress: "В работе", checked: "Проверен" };

export function ReviewEditor({ run, gid }: { run: string; gid: string }) {
  const path = `${runPath(run)}/nodes/${encodeURIComponent(gid)}/review`;
  const draftKey = `moneygraph:review-draft:${run}:${gid}`;
  const [saved, setSaved] = useState<Review | null>(null);
  const [draft, setDraft] = useState<Review | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    request<Review>(path, controller.signal).then(({ data }) => {
      if (controller.signal.aborted) return;
      setSaved(data);
      setError("");
      let restored: Review | null = null;
      try {
        const value = JSON.parse(localStorage.getItem(draftKey) ?? "null");
        if (value && value.status in labels && typeof value.note === "string" && value.note.length <= 4000 && Number.isInteger(value.version)) restored = value;
      } catch { /* A draft is optional; saved work remains on the server. */ }
      setDraft(current => current ?? restored ?? data);
      if (restored) setMessage("Восстановлен локальный черновик. Нажмите «Сохранить».");
    }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [path, draftKey, reload]);
  const dirty = !!draft && !!saved && (draft.note !== saved.note || draft.status !== saved.status);
  function change(values: Partial<Review>) {
    if (!draft) return;
    const next = { ...draft, ...values };
    setDraft(next);
    try { localStorage.setItem(draftKey, JSON.stringify(next)); setMessage("Черновик на этом компьютере. Для постоянного сохранения нажмите «Сохранить»."); }
    catch { setMessage("Черновик только на экране: сохраните перед переходом к другому клиенту."); }
  }
  async function save() {
    if (!draft || !saved) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(path, { method: "PUT", headers: { "Content-Type": "application/json", "X-Moneygraph-Client": "local-ui" },
        body: JSON.stringify({ status: draft.status, note: draft.note, version: draft.version }) });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Не удалось сохранить заметку.");
      setSaved(body.data); setDraft(body.data);
      try { localStorage.removeItem(draftKey); } catch { /* Server save already succeeded. */ }
      setMessage("Сохранено на этом компьютере отдельно от расчёта.");
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <details className="review-editor"><summary>Статус и заметка · {saved ? labels[saved.status] : "загрузка…"}{dirty ? " · черновик" : ""}</summary>
    <p>Только для этой пары: клиент и расчёт. Статус не меняет приоритет или роль.</p>
    {error && <p role="alert">{error}</p>}
    {draft && <>
      <label>Статус проверки<select value={draft.status} disabled={busy} onChange={event => change({ status: event.target.value as Review["status"] })}>
        {Object.entries(labels).map(([value,label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label>Заметка аналитика<textarea rows={4} maxLength={4000} value={draft.note} disabled={busy} onChange={event => change({ note: event.target.value })} /></label>
      <span>{draft.note.length} / 4000</span>
      <button className="button small" disabled={!saved || !dirty || busy} onClick={save}>{busy ? "Сохраняем…" : "Сохранить заметку"}</button>
      {saved && draft.version !== saved.version && <button className="button small" onClick={() => change({ version: saved.version })}>Использовать черновик поверх версии {saved.version}</button>}
    </>}
    {!!error && <button className="button small" onClick={() => setReload(value => value + 1)}>Обновить сохранённую версию</button>}
    {message && <p role="status">{message}</p>}
  </details>;
}
