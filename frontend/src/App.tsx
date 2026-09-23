import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Activity,
  ArrowDownLeft,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Boxes,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Clock3,
  Download,
  ExternalLink,
  Fingerprint,
  GitBranch,
  Info,
  Layers3,
  ListFilter,
  LoaderCircle,
  Network,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Users,
  Upload,
  X,
} from "lucide-react";
import { request, errorMessage, runPath, ApiError } from "./api";
import { NetworkGraph } from "./NetworkGraph";
import { ImportData } from "./ImportData";
import {
  money,
  number,
  roleInfo,
  score,
  warningLabels,
  capLabels,
  type Cluster,
  type GraphData,
  type NodeData,
  type Page,
  type Role,
  type Summary,
  type Transaction,
} from "./types";

function Badge({ role }: { role: Role }) {
  return (
    <span
      className="role-badge"
      style={{ "--role-color": roleInfo[role].color } as React.CSSProperties}
    >
      <i />
      {roleInfo[role].label}
    </span>
  );
}
function Loading({ text = "Загружаем данные" }: { text?: string }) {
  return (
    <div className="loading">
      <LoaderCircle size={22} className="spin" />
      <span>{text}</span>
    </div>
  );
}
function ErrorBox({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="error-box" role="alert">
      <Info size={20} />
      <p>{message}</p>
      {retry && (
        <button className="button small" onClick={retry}>
          <RefreshCw size={14} />
          Повторить
        </button>
      )}
    </div>
  );
}
function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <div className="modal-title">
        <h2>{title}</h2>
        <button className="icon-button" onClick={onClose} aria-label="Закрыть">
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export default function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [bootError, setBootError] = useState("");
  const [needsData, setNeedsData] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const [queue, setQueue] = useState<Page<NodeData> | null>(null);
  const [queueError, setQueueError] = useState("");
  const [queueLoading, setQueueLoading] = useState(false);
  const [role, setRole] = useState("");
  const [cluster, setCluster] = useState("");
  const [seedOnly, setSeedOnly] = useState(false);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<NodeData | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [transactions, setTransactions] = useState<Page<Transaction> | null>(
    null,
  );
  const [txPage, setTxPage] = useState(0);
  const [detailTab, setDetailTab] = useState<"overview" | "transactions">(
    "overview",
  );
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [graphError, setGraphError] = useState("");
  const [graphLoading, setGraphLoading] = useState(false);
  const [focus, setFocus] = useState(false);
  const [hops, setHops] = useState(1);
  const [colorMode, setColorMode] = useState<"role" | "cluster">("role");
  const [search, setSearch] = useState("");
  const [searchBusy, setSearchBusy] = useState(false);
  const searchRequest = useRef<AbortController | null>(null);
  const [notice, setNotice] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [modal, setModal] = useState<
    "help" | "clusters" | "exports" | "import" | null
  >(null);
  const run = summary?.run_id;
  const graphFocus = focus ? selected : null;
  const selectNode = useCallback((gid: string) => {
    setSelected(gid);
    setTxPage(0);
    setDetailTab("overview");
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setBootError("");
    setNeedsData(false);
    request<Summary>("/api/v1/runs/current", controller.signal)
      .then(async (response) => {
        const groups = await request<{ items: Cluster[] }>(
          `${runPath(response.run_id)}/clusters`,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setSummary({ ...response.data, run_id: response.run_id });
          setClusters(groups.data.items);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          if (error instanceof ApiError && error.code === "snapshot_not_loaded")
            setNeedsData(true);
          else setBootError(errorMessage(error));
        }
      });
    return () => controller.abort();
  }, [epoch]);

  useEffect(() => {
    if (!run) return;
    const controller = new AbortController();
    const query = new URLSearchParams({
      limit: "20",
      offset: String(page * 20),
      seed_only: String(seedOnly),
    });
    if (role) query.set("role", role);
    if (cluster) query.set("cluster_id", cluster);
    setQueueLoading(true);
    setQueueError("");
    request<Page<NodeData>>(`${runPath(run)}/nodes?${query}`, controller.signal)
      .then((response) => {
        setQueue(response.data);
        setSelected(
          (previous) => previous ?? response.data.items[0]?.gid ?? null,
        );
      })
      .catch((error) => {
        if (!controller.signal.aborted) setQueueError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setQueueLoading(false);
      });
    return () => controller.abort();
  }, [run, role, cluster, seedOnly, page, epoch]);

  useEffect(() => {
    if (!run || !selected) return;
    const controller = new AbortController();
    setDetailLoading(true);
    setDetailError("");
    setDetail(null);
    setTransactions(null);
    Promise.all([
      request<NodeData>(
        `${runPath(run)}/nodes/${encodeURIComponent(selected)}`,
        controller.signal,
      ),
      request<Page<Transaction>>(
        `${runPath(run)}/nodes/${encodeURIComponent(selected)}/transactions?limit=10&offset=${txPage * 10}`,
        controller.signal,
      ),
    ])
      .then(([node, tx]) => {
        setDetail(node.data);
        setTransactions(tx.data);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setDetailError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [run, selected, txPage, epoch]);

  useEffect(() => {
    if (!run) return;
    const controller = new AbortController();
    const query = new URLSearchParams({ limit: "250", hops: String(hops) });
    if (graphFocus) query.set("gid", graphFocus);
    if (cluster) query.set("cluster_id", cluster);
    setGraphLoading(true);
    setGraphError("");
    request<GraphData>(`${runPath(run)}/graph?${query}`, controller.signal)
      .then((response) => setGraph(response.data))
      .catch((error) => {
        if (!controller.signal.aborted) setGraphError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setGraphLoading(false);
      });
    return () => controller.abort();
  }, [run, graphFocus, hops, cluster, epoch]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 6500);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => () => searchRequest.current?.abort(), []);

  const lookup = async (event: FormEvent) => {
    event.preventDefault();
    if (!run || !search.trim()) return;
    searchRequest.current?.abort();
    const controller = new AbortController();
    searchRequest.current = controller;
    setSearchBusy(true);
    try {
      const response = await request<NodeData>(
        `${runPath(run)}/nodes/${encodeURIComponent(search.trim())}`,
        controller.signal,
      );
      selectNode(response.data.gid);
      setCluster("");
      setFocus(true);
    } catch (error) {
      if (!controller.signal.aborted) setNotice(errorMessage(error));
    } finally {
      if (!controller.signal.aborted) setSearchBusy(false);
    }
  };
  const resetFilters = () => {
    setRole("");
    setCluster("");
    setSeedOnly(false);
    setPage(0);
  };
  const reload = () => setEpoch((value) => value + 1);
  const changeCluster = (value: string) => {
    setCluster(value);
    setPage(0);
    setFocus(false);
    const first = clusters.find((group) => String(group.cluster_id) === value)
      ?.top_gids[0];
    if (first) selectNode(first);
  };
  const openCluster = (id: number) => {
    changeCluster(String(id));
    setRole("");
    setSeedOnly(false);
    setModal(null);
  };
  const period = summary?.period
    ? `${new Date(summary.period.start + "T00:00:00").toLocaleDateString("ru-RU", { day: "numeric", month: "short" })} — ${new Date(summary.period.end + "T00:00:00").toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" })}`
    : "Период не указан";

  return (
    <div className="app-shell">
      <aside className="rail" aria-label="Навигация">
        <a className="brand-mark" href="/" aria-label="TRACE — главная">
          <GitBranch size={26} />
        </a>
        <span className="rail-divider" />
        <button
          className="rail-button active"
          title="Расследование"
          aria-label="Расследование"
          onClick={() => setModal(null)}
        >
          <Network size={21} />
        </button>
        <button
          className="rail-button"
          title="Сообщества"
          aria-label="Сообщества"
          onClick={() => setModal("clusters")}
        >
          <Boxes size={21} />
        </button>
        <button
          className="rail-button"
          title="Выгрузки"
          aria-label="Выгрузки"
          onClick={() => setModal("exports")}
        >
          <ArrowDownToLine size={20} />
        </button>
        <div className="rail-bottom">
          <button
            className="rail-button"
            title="Как читать результаты"
            aria-label="Как читать результаты"
            onClick={() => setModal("help")}
          >
            <CircleHelp size={21} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="wordmark">
            TRACE<span>/</span>
            <span className="wordmark-description">
              Финансовые расследования
            </span>
          </div>
          <div className="topbar-right">
            <span className="local-status">
              <i />
              Локальный режим
            </span>
            <span className="topbar-separator" />
            <ShieldCheck size={17} />
            <span className="analyst-label">Рабочее место аналитика</span>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                <span />
                АНАЛИЗ ТРАНЗАКЦИОННОЙ СЕТИ
              </div>
              <h1>
                Граф денег<span className="heading-dot">.</span>
              </h1>
              <p>От отдельных переводов — к структуре связей.</p>
            </div>
            <div className="heading-actions">
              <button
                className="button upload-action"
                onClick={() => setModal("import")}
              >
                <Upload size={16} />
                Новые данные
              </button>
              <button className="button ghost" onClick={() => setModal("help")}>
                <CircleHelp size={16} />
                <span>Как пользоваться</span>
              </button>
              <button
                className="button primary"
                disabled={!run}
                onClick={() => setModal("exports")}
              >
                <Download size={16} />
                Экспорт результатов
              </button>
            </div>
          </div>
          {needsData ? (
            <div className="startup-error">
              <h2>Начните с загрузки данных</h2>
              <p>Выберите файлы клиентов, связей и переводов. Приложение построит карту и объяснит, кого стоит проверить сначала.</p>
              <button className="button primary" onClick={() => setModal("import")}>
                <Upload size={16} /> Загрузить первый набор
              </button>
            </div>
          ) : bootError ? (
            <div className="startup-error">
              <ErrorBox message={bootError} retry={reload} />
              <p>Нажмите «Новые данные» и выберите файлы для расчёта.</p>
            </div>
          ) : !summary ? (
            <Loading text="Подключаем расчёт…" />
          ) : (
            <>
              {summary.demo && (
                <div className="demo-banner">
                  <Info size={15} />
                  <strong>Демонстрационный снимок</strong>
                  <span>
                    Синтетические данные. Роли и оценки иллюстративные — это не
                    анализ выданного датасета.
                  </span>
                  <span className="demo-tag">DEMO</span>
                </div>
              )}
              <section className="stats" aria-label="Сводка расчёта">
                <Stat
                  icon={<Users size={19} />}
                  label="Клиенты в данных"
                  value={number(summary.stats.n_nodes)}
                  caption={`${number(summary.stats.n_seed)} исходных клиентов`}
                  accent
                />
                <Stat
                  icon={<GitBranch size={19} />}
                  label="Направленные связи"
                  value={number(summary.stats.n_edges)}
                  caption="Плательщик → получатель"
                />
                <Stat
                  icon={<Activity size={19} />}
                  label="Объём переводов"
                  value={money(summary.stats.sum_minor, true)}
                  caption={period}
                />
                <Stat
                  icon={<Layers3 size={19} />}
                  label="Группы клиентов"
                  value={number(summary.stats.n_clusters)}
                  caption="Связанные переводами клиенты"
                />
              </section>
              <div className="workspace-toolbar">
                <div className="workspace-title">
                  <span className="section-index">01</span>
                  <h2>Исследование сети</h2>
                  <span className="live-badge">
                    <i />
                    Расчёт загружен
                  </span>
                </div>
                <form className="global-search" onSubmit={lookup}>
                  <Search size={16} />
                  <input
                    aria-label="Найти по номеру клиента"
                    placeholder="Номер клиента"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    inputMode="numeric"
                  />
                  <button
                    aria-label="Найти"
                    disabled={searchBusy || !search.trim()}
                  >
                    {searchBusy ? (
                      <LoaderCircle size={15} className="spin" />
                    ) : (
                      <kbd>↵</kbd>
                    )}
                  </button>
                </form>
              </div>
              <section className="workspace">
                <aside
                  className="queue-panel panel"
                  aria-label="Очередь проверок"
                >
                  <div className="panel-heading">
                    <div>
                      <h3>Приоритет проверки</h3>
                      <span>
                        {queue ? `${number(queue.total)} клиентов` : "Загрузка…"} ·
                        по убыванию оценки
                      </span>
                    </div>
                    <ListFilter size={18} />
                  </div>
                  <div className="queue-filters">
                    <label className="select-wrap">
                      <SlidersHorizontal size={14} />
                      <select
                        aria-label="Фильтр по роли"
                        value={role}
                        onChange={(event) => {
                          setRole(event.target.value);
                          setPage(0);
                        }}
                      >
                        <option value="">Все роли</option>
                        {Object.entries(roleInfo).map(([key, value]) => (
                          <option key={key} value={key}>
                            {value.label}
                          </option>
                        ))}
                      </select>
                      <ChevronDown size={13} />
                    </label>
                    <label className="seed-filter">
                      <input
                        type="checkbox"
                        checked={seedOnly}
                        onChange={(event) => {
                          setSeedOnly(event.target.checked);
                          setPage(0);
                        }}
                      />
                      <span>Только исходные клиенты</span>
                    </label>
                  </div>
                  {(role || cluster || seedOnly) && (
                    <div className="active-filters">
                      <span>
                        {cluster
                          ? `Группа ${cluster}`
                          : "Фильтры применены"}
                      </span>
                      <button onClick={resetFilters}>
                        Сбросить <X size={12} />
                      </button>
                    </div>
                  )}
                  <div className="queue-columns">
                    <span>Клиент / роль</span>
                    <span>Приоритет</span>
                  </div>
                  <div
                    className={`queue-list ${queueLoading ? "is-loading" : ""}`}
                    aria-busy={queueLoading}
                  >
                    {queueError ? (
                      <ErrorBox message={queueError} retry={reload} />
                    ) : queue?.items.length ? (
                      queue.items.map((node) => (
                        <button
                          key={node.gid}
                          className={`queue-row ${node.gid === selected ? "selected" : ""}`}
                          onClick={() => selectNode(node.gid)}
                          aria-pressed={node.gid === selected}
                        >
                          <span className="queue-rank">
                            {String(node.rank).padStart(2, "0")}
                          </span>
                          <span className="queue-node">
                            <strong>
                              {node.gid}
                              {node.is_seed && (
                                <span
                                  className="seed-dot"
                                  title="Исходный клиент"
                                />
                              )}
                            </strong>
                            <span>
                              <i
                                style={{
                                  background: roleInfo[node.role].color,
                                }}
                              />
                              {roleInfo[node.role].short}
                            </span>
                          </span>
                          <span className="queue-score">
                            <strong>{score(node.priority_score)}</strong>
                            <span>
                              <i
                                style={{
                                  width: score(node.priority_score) + "%",
                                }}
                              />
                            </span>
                          </span>
                          <ChevronRight size={13} className="row-arrow" />
                        </button>
                      ))
                    ) : queueLoading ? (
                      <Loading />
                    ) : (
                      <div className="empty-state">
                        <Search size={25} />
                        <strong>Нет подходящих клиентов</strong>
                        <p>Попробуйте изменить фильтры.</p>
                        <button className="text-button" onClick={resetFilters}>
                          Сбросить фильтры
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="queue-footer">
                    <span>
                      {queue?.total
                        ? `${page * 20 + 1}–${Math.min((page + 1) * 20, queue.total)} из ${number(queue.total)}`
                        : "Нет результатов"}
                    </span>
                    <div>
                      <button
                        aria-label="Предыдущая страница клиентов"
                        disabled={page === 0 || queueLoading}
                        onClick={() => setPage((value) => value - 1)}
                      >
                        <ChevronLeft size={15} />
                      </button>
                      <button
                        aria-label="Следующая страница клиентов"
                        disabled={
                          !queue ||
                          (page + 1) * 20 >= queue.total ||
                          queueLoading
                        }
                        onClick={() => setPage((value) => value + 1)}
                      >
                        <ChevronRight size={15} />
                      </button>
                    </div>
                  </div>
                </aside>
                <section
                  className="graph-panel panel"
                  aria-label="Граф переводов"
                >
                  <div className="panel-heading">
                    <div>
                      <h3>
                        <Network size={17} />
                        Карта связей
                      </h3>
                      <span>
                        {focus
                          ? `Связи клиента ${selected ?? "—"}`
                          : "Направление движения средств"}
                      </span>
                    </div>
                    <div className="segmented">
                      <button
                        className={colorMode === "role" ? "active" : ""}
                        onClick={() => setColorMode("role")}
                      >
                        Роли
                      </button>
                      <button
                        className={colorMode === "cluster" ? "active" : ""}
                        onClick={() => setColorMode("cluster")}
                      >
                        Группы
                      </button>
                    </div>
                  </div>
                  <div className="graph-filterbar">
                    <select
                      aria-label="Группа на карте"
                      value={cluster}
                      onChange={(event) => {
                        changeCluster(event.target.value);
                      }}
                    >
                      <option value="">Все группы</option>
                      {clusters.map((group) => (
                        <option key={group.cluster_id} value={group.cluster_id}>
                          Группа {group.cluster_id} · {group.n_nodes}
                        </option>
                      ))}
                    </select>
                    <div className="scope-buttons">
                      <button
                        className={!focus ? "active" : ""}
                        onClick={() => setFocus(false)}
                      >
                        Вся сеть
                      </button>
                      <button
                        disabled={!selected}
                        className={focus && hops === 1 ? "active" : ""}
                        onClick={() => {
                          setFocus(true);
                          setHops(1);
                        }}
                      >
                        1 шаг
                      </button>
                      <button
                        disabled={!selected}
                        className={focus && hops === 2 ? "active" : ""}
                        onClick={() => {
                          setFocus(true);
                          setHops(2);
                        }}
                      >
                        2 шага
                      </button>
                    </div>
                  </div>
                  <div className="graph-content">
                    {graphError ? (
                      <ErrorBox message={graphError} retry={reload} />
                    ) : graph ? (
                      <NetworkGraph
                        data={graph}
                        selected={selected}
                        onSelect={selectNode}
                        colorMode={colorMode}
                      />
                    ) : (
                      <Loading text="Строим представление графа…" />
                    )}
                    {graphLoading && graph && (
                      <div className="graph-busy">
                        <LoaderCircle size={16} className="spin" />
                        Обновляем граф
                      </div>
                    )}
                  </div>
                  {graph?.truncated && (
                    <div className="truncation">
                      <Info size={14} />
                      Скрыто клиентов: {graph.hidden_nodes}, связей:{" "}
                      {graph.hidden_edges}. Выберите группу или связи одного клиента.
                    </div>
                  )}
                  <div className="graph-legend">
                    {colorMode === "role" ? (
                      Object.entries(roleInfo).map(([key, value]) => (
                        <span key={key}>
                          <i style={{ background: value.color }} />
                          {value.short}
                        </span>
                      ))
                    ) : (
                      <span>
                        <Layers3 size={13} />
                        Цвет обозначает группу; её номер — в карточке клиента
                      </span>
                    )}
                    <span>
                      <i className="legend-seed" />
                      Исходный клиент
                    </span>
                    <span>
                      <i className="legend-boundary" />
                      Граница обхода
                    </span>
                  </div>
                  <div className="graph-footer">
                    <span>
                      <i />
                      {number(graph?.nodes.length)} клиентов ·{" "}
                      {number(graph?.edges.length)} связей на экране
                    </span>
                    <span>Стрелка = перевод</span>
                  </div>
                </section>
                <aside
                  className="detail-panel panel"
                  aria-label="Карточка клиента"
                >
                  <div className="panel-heading">
                    <h3>Карточка клиента</h3>
                    <Fingerprint size={19} />
                  </div>
                  {detailLoading ? (
                    <Loading text="Получаем обоснования…" />
                  ) : detailError ? (
                    <ErrorBox message={detailError} retry={reload} />
                  ) : detail ? (
                    <>
                      <div className="node-identity">
                        <div className="identity-top">
                          <span className="micro-label">НОМЕР КЛИЕНТА</span>
                          <span className="cluster-chip">
                            Группа {detail.cluster_id}
                          </span>
                        </div>
                        <h2>
                          {detail.gid}
                          <button
                            className="icon-button"
                            title="Показать окрестность"
                            aria-label="Показать связи выбранного клиента"
                            onClick={() => {
                              setFocus(true);
                              setCluster("");
                            }}
                          >
                            <ExternalLink size={15} />
                          </button>
                        </h2>
                        <div className="identity-badges">
                          <Badge role={detail.role} />
                          {detail.is_seed && (
                            <span className="seed-badge">ИСХОДНЫЙ</span>
                          )}
                        </div>
                      </div>
                      <div
                        className="detail-tabs"
                        role="tablist"
                        aria-label="Разделы карточки"
                      >
                        <button
                          role="tab"
                          aria-selected={detailTab === "overview"}
                          className={detailTab === "overview" ? "active" : ""}
                          onClick={() => setDetailTab("overview")}
                        >
                          Обзор
                        </button>
                        <button
                          role="tab"
                          aria-selected={detailTab === "transactions"}
                          className={
                            detailTab === "transactions" ? "active" : ""
                          }
                          onClick={() => setDetailTab("transactions")}
                        >
                          Переводы{" "}
                          <span>
                            {transactions?.available ? transactions.total : "—"}
                          </span>
                        </button>
                      </div>
                      <div className="detail-scroll">
                        {detailTab === "overview" ? (
                          <>
                            <div className="priority-card">
                              <div>
                                <span>Приоритет проверки</span>
                                <strong>
                                  {score(detail.priority_score)}
                                  <small>/ 100</small>
                                </strong>
                              </div>
                              <div className="priority-track">
                                <i
                                  style={{
                                    width: score(detail.priority_score) + "%",
                                  }}
                                />
                              </div>
                              <p>Позиция {detail.rank} в общей очереди</p>
                            </div>
                            <div className="evidence-box">
                              <span className="micro-label">
                                <Info size={12} />
                                ЧТО ВИДНО ПО ПЕРЕВОДАМ
                              </span>
                              <p>{detail.explanation ?? detail.evidence}</p>
                            </div>
                            <div className="flow-grid">
                              <div>
                                <span>
                                  <ArrowDownLeft size={14} />
                                  Входящие
                                </span>
                                <strong>
                                  {money(detail.metrics.in_minor, true)}
                                </strong>
                                <small>
                                  от {number(detail.metrics.in_deg)}{" "}
                                  контрагентов
                                </small>
                              </div>
                              <div>
                                <span>
                                  <ArrowUpRight size={14} />
                                  Исходящие
                                </span>
                                <strong>
                                  {money(detail.metrics.out_minor, true)}
                                </strong>
                                <small>
                                  к {number(detail.metrics.out_deg)}{" "}
                                  контрагентам
                                </small>
                              </div>
                            </div>
                            <div className="confidence-row">
                              <span>Насколько ясна роль</span>
                              <strong>{score(detail.role_score)} / 100</strong>
                            </div>
                            <div className="confidence-track">
                              <i
                                style={{
                                  width: score(detail.role_score) + "%",
                                }}
                              />
                            </div>
                            <p className="fine-print">
                              Из 100 баллов: насколько ясно данные указывают на
                              эту роль. Это не вероятность нарушения.
                            </p>
                            {!!Object.keys(
                              detail.rule_details?.assignment?.caps ?? {},
                            ).length && (
                              <div className="limitations">
                                <h4>
                                  <Info size={14} />
                                  Почему оценка роли ограничена
                                </h4>
                                {Object.entries(
                                  detail.rule_details?.assignment?.caps ?? {},
                                ).map(([key, value]) => (
                                  <p key={key}>
                                    {capLabels[key] ?? key}: не выше{" "}
                                    {score(value)} / 100.
                                  </p>
                                ))}
                                <p>
                                  Высокий приоритет означает сильные сигналы для
                                  проверки. Конкретная роль при этом может быть
                                  неоднозначной.
                                </p>
                              </div>
                            )}
                            {!!detail.warnings.length && (
                              <div className="limitations">
                                <h4>
                                  <Info size={14} />
                                  Ограничения наблюдения
                                </h4>
                                {detail.warnings.map((item) => (
                                  <p key={item}>
                                    {warningLabels[item] ?? item}
                                  </p>
                                ))}
                              </div>
                            )}
                            {!!detail.paths.length && (
                              <div className="path-section">
                                <h4>Как к нему ведут исходные клиенты</h4>
                                {detail.paths.slice(0, 3).map((path, index) => (
                                  <div className="path" key={index}>
                                    {path.map((gid, i) => (
                                      <span key={`${gid}-${i}`}>
                                        <button onClick={() => selectNode(gid)}>
                                          {gid}
                                        </button>
                                        {i < path.length - 1 && (
                                          <ArrowRight size={11} />
                                        )}
                                      </span>
                                    ))}
                                  </div>
                                ))}
                                <p className="fine-print">
                                  Это примеры цепочек переводов, а не
                                  доказательство того, что по всей цепочке шли
                                  одни и те же деньги.
                                </p>
                              </div>
                            )}
                            <details className="technical-details">
                              <summary>Подробности расчёта</summary>
                            {!!detail.alternatives?.length && (
                              <div className="contributions">
                                <h4>Дополнительные признаки ролей</h4>
                                {detail.alternatives
                                  .filter((item) => item.role in roleInfo)
                                  .map((item) => (
                                    <div key={item.role}>
                                      <Badge role={item.role} />
                                      <strong>
                                        {score(item.support)} / 100
                                      </strong>
                                    </div>
                                  ))}
                              </div>
                            )}
                            {!!detail.contributions.length && (
                              <div className="contributions">
                                <h4>Из чего складывается приоритет</h4>
                                {detail.contributions.map((item) => (
                                  <div key={item.key}>
                                    <span>{item.label}</span>
                                    <strong>
                                      +
                                      {(item.value * 100).toLocaleString(
                                        "ru-RU",
                                        { maximumFractionDigits: 1 },
                                      )}
                                    </strong>
                                  </div>
                                ))}
                              </div>
                            )}
                              <p>Баллы дополнительных ролей показывают совпадение признаков до ограничений. Это не оценка уверенности.</p>
                              <p>{detail.evidence}</p>
                              <p>
                                Правило: {detail.rule_id ?? "Не назначено"}.
                                Номер группы: {detail.cluster_id}.
                              </p>
                            </details>
                          </>
                        ) : (
                          <div className="transactions">
                            <div className="tx-note">
                              <Clock3 size={14} />
                              Порядок операций внутри дня неизвестен.
                            </div>
                            {transactions?.available === false ? (
                              <div className="empty-state">
                                <Info size={25} />
                                <strong>Транзакции не подключены</strong>
                                <p>
                                  Передайте transactions.parquet вместе со
                                  снимком или через параметр --data.
                                </p>
                              </div>
                            ) : !transactions?.items.length ? (
                              <div className="empty-state">
                                <GitBranch size={25} />
                                <strong>В выборке нет переводов</strong>
                                <p>Клиент есть в списке, но его переводов в этих данных нет.</p>
                              </div>
                            ) : (
                              transactions.items.map((tx) => (
                                <div className="tx-row" key={tx.tx_ref}>
                                  <span
                                    className={`tx-direction ${tx.dst === detail.gid ? "incoming" : ""}`}
                                  >
                                    {tx.dst === detail.gid ? (
                                      <ArrowDownLeft size={16} />
                                    ) : (
                                      <ArrowUpRight size={16} />
                                    )}
                                  </span>
                                  <div>
                                    <button
                                      onClick={() =>
                                        selectNode(
                                          tx.dst === detail.gid
                                            ? tx.src
                                            : tx.dst,
                                        )
                                      }
                                    >
                                      {tx.dst === detail.gid ? "От" : "К"}{" "}
                                      {tx.dst === detail.gid ? tx.src : tx.dst}
                                    </button>
                                    <small>
                                      {new Date(
                                        tx.date + "T00:00:00",
                                      ).toLocaleDateString("ru-RU")}
                                    </small>
                                  </div>
                                  <strong>{money(tx.sum_minor)}</strong>
                                </div>
                              ))
                            )}
                            {!!transactions?.total && (
                              <div className="tx-pagination">
                                <button
                                  className="icon-button"
                                  aria-label="Предыдущие переводы"
                                  disabled={!txPage}
                                  onClick={() =>
                                    setTxPage((value) => value - 1)
                                  }
                                >
                                  <ChevronLeft size={16} />
                                </button>
                                <span>
                                  {txPage * 10 + 1}–
                                  {Math.min(
                                    (txPage + 1) * 10,
                                    transactions.total,
                                  )}{" "}
                                  / {transactions.total}
                                </span>
                                <button
                                  className="icon-button"
                                  aria-label="Следующие переводы"
                                  disabled={
                                    (txPage + 1) * 10 >= transactions.total
                                  }
                                  onClick={() =>
                                    setTxPage((value) => value + 1)
                                  }
                                >
                                  <ChevronRight size={16} />
                                </button>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                      <div className="detail-footer">
                        <ShieldCheck size={14} />
                        Гипотеза для проверки аналитиком
                      </div>
                    </>
                  ) : (
                    <div className="empty-state">
                      <Fingerprint size={28} />
                      <strong>Выберите узел</strong>
                      <p>Нажмите на граф или строку в очереди.</p>
                    </div>
                  )}
                </aside>
              </section>
              <footer className="page-footer">
                <span>
                  <Info size={13} />
                  {number(summary.stats.depth_boundary)} клиентов на границе
                  обхода. Отсутствие исходящих не означает удержание средств.
                </span>
                <button onClick={() => setModal("help")}>
                  <span className="run-id">{summary.run_id}</span>
                  <Check size={12} />
                </button>
              </footer>
            </>
          )}
        </main>
      </div>
      {notice && (
        <div className="toast" role="alert">
          <Info size={18} />
          <span>{notice}</span>
          <button
            className="icon-button"
            aria-label="Закрыть уведомление"
            onClick={() => setNotice("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {modal && (
        <Modal
          title={
            modal === "help"
              ? "Как читать результаты"
              : modal === "clusters"
                ? "Группы связанных клиентов"
                : modal === "import"
                  ? "Новые данные"
                  : "Экспорт результатов"
          }
          onClose={() => {
            if (!importBusy) setModal(null);
          }}
        >
          {modal === "import" ? (
            <ImportData
              period={summary?.period}
              onBusy={setImportBusy}
              onComplete={() => {
                setModal(null);
                setNotice("Новый набор загружен. Расчёт завершён.");
                setSelected(null);
                setDetail(null);
                setRole("");
                setCluster("");
                setSeedOnly(false);
                setPage(0);
                setFocus(false);
                setSearch("");
                setEpoch((value) => value + 1);
              }}
            />
          ) : modal === "help" ? (
            <div className="help-content">
              <p className="modal-intro">
                Начните с клиента в левой очереди. Справа прочитайте, что видно
                по его переводам. На карте стрелка показывает, кто кому отправил
                деньги.
              </p>
              <div className="help-grid">
                <div>
                  <strong>Что означает роль</strong>
                  <p>
                    «Сбор денег» — получает от многих; «Распределение» —
                    отправляет многим; «Передача дальше» — входящие и исходящие
                    суммы близки. «Связующий» соединяет разные части сети. Это
                    предположения, которые нужно проверить.
                  </p>
                </div>
                <div>
                  <strong>Приоритет</strong>
                  <p>
                    Подсказывает, кого посмотреть раньше. 100 баллов не означает
                    «100% нарушитель». Например, клиент может быть важен для
                    нескольких цепочек, даже если его роль пока неясна.
                  </p>
                </div>
                <div>
                  <strong>Насколько ясна роль</strong>
                  <p>
                    Отдельная оценка от 0 до 100. Если признаки подходят сразу
                    нескольким ролям или данных мало, оценка ограничивается.
                    Поэтому приоритет может быть 100, а оценка роли — 60.
                  </p>
                </div>
                <div>
                  <strong>Почему цепочка обрывается</strong>
                  <p>
                    Данные собраны не дальше четырёх переводов от исходных
                    клиентов. Если дальше стрелок нет, это не означает, что
                    человек оставил деньги себе.
                  </p>
                </div>
              </div>
              <div className="help-rule">
                <Info size={18} />
                <p>
                  <strong>Почему бывает 0 ₸?</strong> В группе показана сумма
                  переводов между её участниками, а не деньги на счетах. Один
                  клиент без наблюдаемых переводов остаётся в списке и получает
                  0 ₸. Переводы с другими группами подписаны отдельно.
                </p>
              </div>
              <p className="modal-intro">
                Исходный клиент — тот, от кого началось исследование. Группа —
                клиенты, которых алгоритм связал по переводам; это не
                доказательство сговора. Чтобы проанализировать другой набор,
                нажмите «Новые данные».
              </p>
              <div className="help-rule">
                <ShieldCheck size={18} />
                <p>
                  Суммы показывают переводы внутри выборки, а не полный баланс
                  клиента. Связь или принадлежность к сообществу не доказывает
                  правонарушение.
                </p>
              </div>
              {summary && (
                <dl className="run-metadata">
                  <dt>Расчёт</dt>
                  <dd>{summary.run_id}</dd>
                  <dt>Правила</dt>
                  <dd>{summary.rules_version ?? "Версия не указана"}</dd>
                  <dt>Период</dt>
                  <dd>{period}</dd>
                  <dt>Источник</dt>
                  <dd>
                    {summary.demo
                      ? "Синтетический демонстрационный снимок"
                      : "Подключённый снимок расчёта"}
                  </dd>
                </dl>
              )}
            </div>
          ) : modal === "clusters" ? (
            <div className="cluster-list">
              {clusters.length ? (
                clusters.map((group) => (
                  <button
                    key={group.cluster_id}
                    className="cluster-card"
                    onClick={() => openCluster(group.cluster_id)}
                  >
                    <div>
                      <span className="cluster-icon">
                        <Boxes size={20} />
                      </span>
                      <strong>Группа {group.cluster_id}</strong>
                      <ChevronRight size={18} />
                    </div>
                    <p>{group.description}</p>
                    {group.zero_explanation && (
                      <p className="zero-explanation">
                        {group.zero_explanation}
                      </p>
                    )}
                    <div className="cluster-money">
                      <span>Переводы внутри группы</span>
                      <strong>{money(group.sum_minor_internal)}</strong>
                    </div>
                    <div className="cluster-money secondary">
                      <span>Из других групп / в другие группы</span>
                      <span>
                        {money(group.incoming_minor, true)} /{" "}
                        {money(group.outgoing_minor, true)}
                      </span>
                    </div>
                    <footer>
                      <span>Клиентов: {number(group.n_nodes)}</span>
                      <span>Исходных: {number(group.n_seed)}</span>
                      <span>Операций: {number(group.n_operations)}</span>
                    </footer>
                  </button>
                ))
              ) : (
                <p className="modal-intro">
                  Сначала подключите завершённый расчёт.
                </p>
              )}
            </div>
          ) : (
            <div className="export-list">
              <a
                className={`export-item excel-export ${!run ? "disabled" : ""}`}
                href={
                  run ? `${runPath(run)}/reports/moneygraph.xlsx` : undefined
                }
                download
              >
                <span className="file-icon">XLSX</span>
                <span>
                  <strong>Открыть в Excel — рекомендуется</strong>
                  <small>
                    Русские заголовки и объяснения. Номера клиентов сохраняются
                    полностью. Все результаты в одном файле.
                  </small>
                  <code>moneygraph.xlsx</code>
                </span>
                <Download size={18} />
              </a>
              <p className="modal-intro">
                Ниже — исходные CSV по формату задания. Excel может неверно
                определить их кодировку и округлить длинные номера. Для обычного
                просмотра используйте файл XLSX выше.
              </p>
              <p className="modal-intro">
                Все файлы принадлежат расчёту <strong>{run ?? "—"}</strong>.
                Полная выборка сохраняется независимо от фильтров экрана.
              </p>
              {summary?.demo && (
                <div className="export-warning">
                  Это синтетические демонстрационные результаты.
                </div>
              )}
              {[
                [
                  "nodes_roles.csv",
                  "Роли всех клиентов",
                  "Роль, поддержка, кластер, приоритет и обоснование",
                ],
                [
                  "clusters.csv",
                  "Сообщества",
                  "Состав, оборот и гипотеза о структуре",
                ],
                [
                  "top_nodes.csv",
                  "Очередь приоритетов",
                  "Очередь проверки клиентов с объяснениями",
                ],
              ].map(([file, title, description]) => (
                <a
                  key={file}
                  className={`export-item ${!run ? "disabled" : ""}`}
                  href={run ? `${runPath(run)}/exports/${file}` : undefined}
                  download
                >
                  <span className="file-icon">CSV</span>
                  <span>
                    <strong>{title}</strong>
                    <small>{description}</small>
                    <code>{file}</code>
                  </span>
                  <Download size={18} />
                </a>
              ))}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}

function Stat({
  icon,
  label,
  value,
  caption,
  accent = false,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  caption: string;
  accent?: boolean;
}) {
  return (
    <div className={`stat-card ${accent ? "accent" : ""}`}>
      <div className="stat-top">
        <span>{label}</span>
        <span className="stat-icon">{icon}</span>
      </div>
      <strong>{value}</strong>
      <div className="stat-caption">
        {accent && <i />}
        {caption}
      </div>
    </div>
  );
}
