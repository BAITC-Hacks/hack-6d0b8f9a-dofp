import type { Envelope } from "./types";

export async function request<T>(
  path: string,
  signal?: AbortSignal,
): Promise<Envelope<T>> {
  const response = await fetch(path, {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    let message = `Ошибка сервера (${response.status})`;
    try {
      const body = await response.json();
      message =
        body.detail?.message ||
        (typeof body.detail === "string" ? body.detail : message);
    } catch {
      /* retain HTTP status */
    }
    throw new Error(message);
  }
  if (!response.headers.get("content-type")?.includes("application/json"))
    throw new Error(
      "API вернул неожиданный формат. Проверьте подключение сервера.",
    );
  return response.json();
}
export const runPath = (run: string) =>
  `/api/v1/runs/${encodeURIComponent(run)}`;
export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "Не удалось получить данные";
