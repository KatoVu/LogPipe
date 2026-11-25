// The component graph: callers on the left, what they depend on to the right. Small (one node per
// service), so it's laid out here in layers instead of pulling in a graph library.
import { useMemo, type ReactNode } from "react";

export type Health = "idle" | "ok" | "warn" | "error";

export interface GraphEdge {
  id: string;
  src: string;
  dst: string;
  relation: string;
  templates: number[];
  by: "llm" | "rules" | "manual" | "traces";
  status: string[];
  lines?: number;
  errors?: number;
  calls?: number; // traced links: calls and failed calls in the range
  call_errors?: number;
  ms?: number;
  health?: Health;
  evidence?: { id: number; text: string }[];
}

export interface GraphNode { id: string; lines?: number; errors?: number; health?: Health }

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats?: { calls: number; templates: number; prompt_chars: number; seconds: number };
}

export const HEALTH_COLOR: Record<Health, string> = {
  idle: "var(--line-strong)",
  ok: "var(--good)",
  warn: "var(--warn)",
  error: "var(--bad)",
};

const W = 168, H = 40, GAP_X = 96, GAP_Y = 28, PAD = 16;

/** Each node's column: the longest chain of callers above it (cycles are cut after n passes). */
function layers(nodes: string[], edges: GraphEdge[]): Map<string, number> {
  const col = new Map(nodes.map((n) => [n, 0]));
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false;
    for (const e of edges) {
      const want = (col.get(e.src) ?? 0) + 1;
      if (e.src !== e.dst && want > (col.get(e.dst) ?? 0) && want < nodes.length) {
        col.set(e.dst, want);
        moved = true;
      }
    }
    if (!moved) break;
  }
  return col;
}

/** Everything reachable from `start` following edges forward (down) or backward (up). */
export function reach(start: string, edges: GraphEdge[], down: boolean): Set<string> {
  const seen = new Set<string>();
  const todo = [start];
  while (todo.length) {
    const cur = todo.pop()!;
    for (const e of edges) {
      const [from, to] = down ? [e.src, e.dst] : [e.dst, e.src];
      if (from === cur && !seen.has(to) && to !== start) {
        seen.add(to);
        todo.push(to);
      }
    }
  }
  return seen;
}

export function GraphView({ data, selected, onNode, onEdge, edgeLabel }: {
  data: GraphData;
  selected?: string | null;
  onNode?: (id: string) => void;
  onEdge?: (e: GraphEdge) => void;
  edgeLabel?: (e: GraphEdge) => ReactNode;
}) {
  const layout = useMemo(() => {
    const names = data.nodes.map((n) => n.id);
    const col = layers(names, data.edges);
    const cols: string[][] = [];
    for (const n of names) (cols[col.get(n) ?? 0] ??= []).push(n);
    const pos = new Map<string, { x: number; y: number }>();
    cols.forEach((c, i) => c.sort().forEach((n, j) => pos.set(n, { x: PAD + i * (W + GAP_X), y: PAD + j * (H + GAP_Y) })));
    const width = PAD * 2 + Math.max(1, cols.length) * (W + GAP_X) - GAP_X;
    const height = PAD * 2 + Math.max(1, ...cols.map((c) => c?.length ?? 0)) * (H + GAP_Y) - GAP_Y;
    return { pos, width, height };
  }, [data]);

  const up = selected ? reach(selected, data.edges, false) : null; // breaks if `selected` fails
  const down = selected ? reach(selected, data.edges, true) : null; // what `selected` needs
  const byId = new Map(data.nodes.map((n) => [n.id, n]));

  return (
    <svg className="graph" viewBox={`0 0 ${layout.width} ${layout.height}`} width={layout.width} height={layout.height}
      role="img" aria-label={`Component graph: ${data.nodes.length} components, ${data.edges.length} links`}>
      <defs>
        {(Object.keys(HEALTH_COLOR) as Health[]).map((h) => (
          <marker key={h} id={`arrow-${h}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill={HEALTH_COLOR[h]} />
          </marker>
        ))}
      </defs>
      {data.edges.map((e) => {
        const a = layout.pos.get(e.src), b = layout.pos.get(e.dst);
        if (!a || !b) return null;
        const h = e.health ?? "idle";
        const back = b.x <= a.x; // a link to the same or an earlier column curves around
        const x1 = back ? a.x + W / 2 : a.x + W, y1 = back ? a.y + H : a.y + H / 2;
        const x2 = back ? b.x + W / 2 : b.x, y2 = back ? b.y + H : b.y + H / 2;
        const bend = back ? `C ${x1} ${y1 + 50}, ${x2} ${y2 + 50},` : `C ${x1 + GAP_X / 2} ${y1}, ${x2 - GAP_X / 2} ${y2},`;
        const faded = selected && !(e.src === selected || e.dst === selected || (up!.has(e.src) && (up!.has(e.dst) || e.dst === selected)) || (down!.has(e.dst) && (down!.has(e.src) || e.src === selected)));
        return (
          <g key={e.id} className={`graph-edge${onEdge ? " clickable" : ""}`} opacity={faded ? 0.2 : 1} onClick={() => onEdge?.(e)}>
            <title>{`${e.src} ${e.relation} ${e.dst}${e.status.length ? ` (${e.status.join(", ")})` : ""}: ${e.calls != null ? `${e.calls} calls, ${e.call_errors ?? 0} failed` : `${e.lines ?? 0} lines, ${e.errors ?? 0} error/warn`}`}</title>
            <path d={`M ${x1} ${y1} ${bend} ${x2} ${y2}`} fill="none" stroke="transparent" strokeWidth={14} />
            <path d={`M ${x1} ${y1} ${bend} ${x2} ${y2}`} fill="none" stroke={HEALTH_COLOR[h]} strokeWidth={h === "error" ? 3 : 2}
              strokeDasharray={e.by === "manual" ? "6 4" : undefined} markerEnd={`url(#arrow-${h})`} />
            {edgeLabel && <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 6} textAnchor="middle" className="graph-label">{edgeLabel(e)}</text>}
          </g>
        );
      })}
      {data.nodes.map((n) => {
        const p = layout.pos.get(n.id)!;
        const role = n.id === selected ? "selected" : up?.has(n.id) ? "up" : down?.has(n.id) ? "down" : selected ? "faded" : "";
        const h = byId.get(n.id)?.health ?? "idle";
        return (
          <g key={n.id} className={`graph-node ${role}${onNode ? " clickable" : ""}`} transform={`translate(${p.x},${p.y})`}
            onClick={() => onNode?.(n.id)} tabIndex={onNode ? 0 : undefined}
            onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " ") && onNode?.(n.id)}>
            <title>{`${n.id}: ${n.lines ?? 0} lines, ${n.errors ?? 0} error/warn`}</title>
            <rect width={W} height={H} rx={6} />
            <circle cx={14} cy={H / 2} r={5} fill={HEALTH_COLOR[h]} />
            <text x={26} y={H / 2 + 4}>{n.id.length > 20 ? n.id.slice(0, 19) + "…" : n.id}</text>
          </g>
        );
      })}
    </svg>
  );
}
