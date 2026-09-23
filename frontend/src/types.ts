export type Role =
  | "consolidator"
  | "transit"
  | "distributor"
  | "terminal"
  | "coordinator"
  | "peripheral";
export interface NodeData {
  gid: string;
  role: Role;
  role_score: number;
  priority_score: number;
  cluster_id: number;
  evidence: string;
  depth: number | null;
  is_seed: boolean;
  rank: number;
  metrics: Record<string, number | string | null>;
  warnings: string[];
  contributions: { key: string; label: string; value: number }[];
  paths: string[][];
  rule_id: string | null;
  alternatives?: { role: Role; support: number; rule_id?: string }[];
}
export interface Edge {
  src: string;
  dst: string;
  sum_minor: string;
  n_tx: number;
}
export interface GraphData {
  nodes: NodeData[];
  edges: Edge[];
  total_nodes: number;
  total_edges: number;
  hidden_nodes: number;
  hidden_edges: number;
  truncated: boolean;
  focus_gid: string | null;
  hops: number;
}
export interface Cluster {
  cluster_id: number;
  n_nodes: number;
  n_seed: number;
  sum_minor_internal: string;
  top_gids: string[];
  hypothesis: string;
}
export interface Summary {
  run_id: string;
  demo?: boolean;
  rules_version?: string;
  period?: { start: string; end: string };
  stats: {
    n_nodes: number;
    n_edges: number;
    n_seed: number;
    n_clusters: number;
    sum_minor: string;
    depth_boundary: number;
  };
  quality?: Record<string, unknown>;
}
export interface Transaction {
  src: string;
  dst: string;
  date: string;
  sum_minor: string;
  tx_ref: string;
}
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  available?: boolean;
}
export interface Envelope<T> {
  run_id: string;
  schema_version: string;
  data: T;
  warnings: string[];
}

export const roleInfo: Record<
  Role,
  { label: string; short: string; color: string }
> = {
  coordinator: { label: "Координатор", short: "Координатор", color: "#d08a30" },
  consolidator: {
    label: "Консолидация",
    short: "Консолидация",
    color: "#168b6b",
  },
  transit: { label: "Транзит", short: "Транзит", color: "#7185c7" },
  distributor: {
    label: "Распределитель",
    short: "Распределитель",
    color: "#48a5b8",
  },
  terminal: {
    label: "Конечный получатель",
    short: "Получатель",
    color: "#a184b8",
  },
  peripheral: { label: "Периферия", short: "Периферия", color: "#94a6a0" },
};
export const warningLabels: Record<string, string> = {
  depth_boundary: "Колено 4: дальнейшие переводы видны не полностью.",
  partial_network: "Выборка отражает только часть финансовой сети.",
  intrabank_only: "В выборке представлены только внутрибанковские переводы.",
  outgoing_sample:
    "Сеть собрана обходом исходящих переводов от исходных узлов.",
  self_transfer:
    "Есть перевод узла самому себе; он не считается отдельным контрагентом.",
  incoming_date_unavailable: "Дата последнего входящего перевода недоступна.",
  active_in_days_unavailable: "Число дней с входящими переводами недоступно.",
  seed_inflow_incomplete:
    "Входящие переводы исходного узла видны не полностью.",
  isolated: "В выгрузке нет связей этого узла.",
  out_exceeds_observed_in:
    "Исходящие превышают наблюдаемые входящие. Это не полный баланс.",
  month_end_window:
    "До конца периода недостаточно времени для проверки дальнейшего движения.",
  date_only: "Известна дата, но не порядок операций внутри дня.",
  sampling_threshold: "Переводы меньше 5 000 ₸ не входят в выборку.",
};
export function money(
  minor: string | number | null | undefined,
  compact = false,
): string {
  if (minor === null || minor === undefined || minor === "") return "—";
  try {
    const value = BigInt(String(minor));
    const negative = value < 0n;
    const absolute = negative ? -value : value;
    if (compact && absolute >= 100_000_000n)
      return `${negative ? "−" : ""}${(Number(absolute / 100_000n) / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} млн ₸`;
    if (compact && absolute >= 100_000n)
      return `${negative ? "−" : ""}${(Number(absolute / 100n) / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} тыс. ₸`;
    const whole = (absolute / 100n).toLocaleString("ru-RU");
    const fraction = absolute % 100n;
    return `${negative ? "−" : ""}${whole}${fraction ? "," + fraction.toString().padStart(2, "0") : ""} ₸`;
  } catch {
    return "—";
  }
}
export const number = (value: unknown) =>
  value === undefined || value === null
    ? "—"
    : Number(value).toLocaleString("ru-RU");
export const score = (value: number) => Math.round(value * 100).toString();
