import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import { Crosshair, Minus, Plus } from "lucide-react";
import { roleInfo, money, type GraphData } from "./types";

const clusterColors = [
  "#168b6b",
  "#7185c7",
  "#d08a30",
  "#48a5b8",
  "#a184b8",
  "#9b7865",
];
export function NetworkGraph({
  data,
  selected,
  onSelect,
  colorMode,
}: {
  data: GraphData;
  selected: string | null;
  onSelect: (gid: string) => void;
  colorMode: "role" | "cluster";
}) {
  const [edgeTip, setEdgeTip] = useState("");
  const isPath = data.view_mode === "seed_path";
  const container = useRef<HTMLDivElement>(null);
  const core = useRef<cytoscape.Core | null>(null);
  const callback = useRef(onSelect);
  callback.current = onSelect;
  useEffect(() => {
    if (!container.current) return;
    setEdgeTip("");
    const graph = cytoscape({
      container: container.current,
      elements: [
        ...data.nodes.map((node, index) => ({
          data: {
            id: node.gid,
            label: isPath ? `${node.step_index === null ? "Клиент" : `Шаг ${node.step_index}`}\n${node.gid}` : node.gid,
            color:
              colorMode === "role"
                ? roleInfo[node.role].color
                : clusterColors[node.cluster_id % clusterColors.length],
            size: 22 + node.priority_score * 23,
            seed: node.is_seed ? 1 : 0,
            boundary: node.warnings.includes("depth_boundary") ? 1 : 0,
          },
          position: {
            x: isPath ? (node.step_index ?? 0) * 220 : (node.depth ?? index % 5) * 125 + (index % 2) * 25,
            y: isPath ? 0 : node.cluster_id * 130 + (index % 4) * 45,
          },
        })),
        ...data.edges.map((edge) => ({
          data: {
            id: `${edge.src}->${edge.dst}`,
            tip: `${edge.src} → ${edge.dst}: ${money(edge.sum_minor)} за период · операций: ${edge.n_tx}`,
            source: edge.src,
            target: edge.dst,
            width: Math.min(
              3.2,
              0.7 + Math.log10(Math.max(1, Number(edge.sum_minor) / 100)) / 4,
            ),
          },
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            width: "data(size)",
            height: "data(size)",
            label: "data(label)",
            color: "#536a60",
            "font-size": 10,
            "font-family": "Segoe UI, sans-serif",
            "text-wrap": "wrap",
            "text-valign": "bottom",
            "text-margin-y": 8,
            "border-width": 3,
            "border-color": "#ffffff",
            "overlay-opacity": 0,
          },
        },
        {
          selector: "node[seed = 1]",
          style: {
            shape: "round-rectangle",
            "border-color": "#375c4c",
            "border-width": 2,
          },
        },
        {
          selector: "node[boundary = 1]",
          style: {
            "border-style": "dashed",
            "border-color": "#677e72",
            "border-width": 2,
            "background-opacity": 0.45,
          },
        },
        {
          selector: "edge",
          style: {
            width: "data(width)",
            "line-color": isPath ? "#419176" : "#c2d1c9",
            "target-arrow-color": isPath ? "#419176" : "#b4c5bb",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "arrow-scale": 0.8,
            opacity: 0.85,
          },
        },
        { selector: ".faded", style: { opacity: 0.24 } },

        {
          selector: "node.focus",
          style: {
            "border-color": "#203d30",
            "border-width": 4,
            color: "#162d22",
            "font-weight": "bold",
            "font-size": 12,
          },
        },
        {
          selector: "edge.connected",
          style: {
            "line-color": "#419176",
            "target-arrow-color": "#419176",
            opacity: 1,
            width: 2,
          },
        },
        { selector: "edge.incoming", style: { "line-color": "#168b6b", "target-arrow-color": "#168b6b", "line-style": "solid", width: 2.5 } },
        { selector: "edge.outgoing", style: { "line-color": "#7957bd", "target-arrow-color": "#7957bd", "line-style": "dashed", width: 2.5 } },
      ],
      layout: isPath ? { name: "preset", fit: true, padding: 55 } : {
        name: "cose",
        animate: false,
        randomize: false,
        fit: true,
        padding: 45,
        nodeRepulsion: () => 9000,
        idealEdgeLength: () => 100,
        componentSpacing: 100,
        numIter: 400,
      } as cytoscape.LayoutOptions,
      minZoom: 0.18,
      maxZoom: 3,
      wheelSensitivity: 0.22,
    });
    core.current = graph;
    graph.on("tap", "node", (event) => callback.current(event.target.id()));
    graph.on("mouseover tap", "edge", (event) => setEdgeTip(event.target.data("tip")));
    graph.on("mouseout", "edge", () => setEdgeTip(""));
    const observer = new ResizeObserver(() => {
      graph.resize();
      if (isPath) graph.fit(undefined, 55);
    });
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      graph.destroy();
      core.current = null;
    };
  }, [data, colorMode]);
  useEffect(() => {
    const graph = core.current;
    if (!graph) return;
    graph.elements().removeClass("faded focus connected incoming outgoing");
    if (!selected) return;
    const target = graph.getElementById(selected);
    if (!target.length) return;
    if (isPath) {
      target.addClass("focus");
      return;
    }
    const neighbors = target.closedNeighborhood();
    graph.elements().difference(neighbors).addClass("faded");
    target.addClass("focus");
    target.connectedEdges().addClass("connected");
    target.incomers("edge").addClass("incoming");
    target.outgoers("edge").addClass("outgoing");
  }, [selected, data, colorMode]);
  const zoom = (factor: number) => {
    const graph = core.current;
    if (graph)
      graph.zoom({
        level: graph.zoom() * factor,
        renderedPosition: { x: graph.width() / 2, y: graph.height() / 2 },
      });
  };
  return (
    <div className="network-wrap">
      <div
        ref={container}
        className="network-canvas"
        role="img"
        aria-label={`Граф: клиентов — ${data.nodes.length}, направленных связей — ${data.edges.length}. Для выбора клиента также доступна очередь проверок.`}
      />
      {edgeTip && <div className="edge-tooltip" role="tooltip">{edgeTip}</div>}
      {!data.nodes.length && (
        <div className="graph-empty">Нет клиентов для выбранного фильтра</div>
      )}
      <div className="graph-controls">
        <button
          title="Увеличить"
          aria-label="Увеличить граф"
          onClick={() => zoom(1.25)}
        >
          <Plus size={16} />
        </button>
        <button
          title="Уменьшить"
          aria-label="Уменьшить граф"
          onClick={() => zoom(0.8)}
        >
          <Minus size={16} />
        </button>
        <span />
        <button
          title="Показать весь граф"
          aria-label="Вписать граф в экран"
          onClick={() => core.current?.fit(undefined, 45)}
        >
          <Crosshair size={17} />
        </button>
      </div>
      <div className="graph-hint">Перетаскивайте точки клиентов · Колесо — масштаб</div>
    </div>
  );
}
