"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import type { Citation, RetrievalHit, TraceItem } from "@/lib/types";
import { useInspector } from "@/stores/inspector";
import { useViewer } from "@/stores/viewer";

/**
 * The evidence map: what the run read, drawn as a graph.
 *
 * The question sits at the centre. Every passage the retriever returned hangs
 * off it, and every passage hangs off the document it came from. Passages the
 * answer actually cited carry their reference number; the rest are drawn
 * fainter, because "retrieved but not used" is real information — it is the
 * difference between an answer that ignored a contradicting source and one
 * that never saw it.
 *
 * The layout is a small force simulation written here rather than pulled from
 * a library, because this deployment has no route to a CDN and a graph of at
 * most a few dozen nodes does not need one. Edges carry the retrieval method
 * and are weighted by score, so a strongly-matched hybrid hit is visibly a
 * shorter, brighter tether than a weak sparse one.
 */

type NodeKind = "query" | "doc" | "passage";

interface MapNode {
  id: string;
  kind: NodeKind;
  label: string;
  // simulation state
  x: number;
  y: number;
  vx: number;
  vy: number;
  fx?: number | null;
  fy?: number | null;
  // passage details
  docId?: string;
  page?: number;
  method?: string;
  score?: number;
  confidence?: number;
  n?: number | null;
  hit?: RetrievalHit;
  citation?: Citation;
  // doc details
  cited?: boolean;
  passages?: number;
}

interface MapEdge {
  source: string;
  target: string;
  kind: "retrieval" | "belongs";
  method?: string;
  weight: number;
}

const METHOD_COLOUR: Record<string, string> = {
  hybrid: "hsl(33 95% 58%)",
  dense: "hsl(205 75% 55%)",
  sparse: "hsl(150 60% 45%)",
};

function methodColour(method?: string): string {
  return METHOD_COLOUR[method ?? ""] ?? "hsl(215 12% 48%)";
}

/** Fold the run's retrieval trace and its citations into nodes and edges. */
function buildGraph(
  question: string,
  trace: TraceItem[],
  citations: Citation[],
  width: number,
  height: number,
): { nodes: MapNode[]; edges: MapEdge[] } {
  const citedByChunk = new Map(citations.map((c) => [c.chunk_id, c]));
  const nodes = new Map<string, MapNode>();
  const edges: MapEdge[] = [];
  const cx = width / 2;
  const cy = height / 2;

  nodes.set("query", {
    id: "query",
    kind: "query",
    label: question,
    x: cx,
    y: cy,
    vx: 0,
    vy: 0,
    fx: cx,
    fy: cy,
  });

  const hits: RetrievalHit[] = [];
  for (const item of trace) {
    if (item.kind === "retrieval") hits.push(...item.hits);
  }
  // A citation with no retrieval hit (possible when the trace was trimmed to
  // the top eight) still deserves a node.
  for (const citation of citations) {
    if (!hits.some((h) => h.chunk_id === citation.chunk_id)) {
      hits.push({
        chunk_id: citation.chunk_id,
        doc_id: citation.doc_id,
        doc_title: citation.doc_title,
        page: citation.page_no,
        score: citation.score,
        method: citation.retrieval_method,
        confidence: citation.confidence,
        bbox: citation.bbox,
        section_path: citation.section_path,
        snippet: citation.snippet,
      });
    }
  }

  // Retrieval scores are reciprocal-rank-fusion sums, which live around
  // 0.01–0.05 regardless of how good the match was. Only their ratio means
  // anything, so edges are weighted against the best hit in this run.
  const maxScore = hits.reduce((m, h) => Math.max(m, h.score), 0) || 1;

  let seed = 1;
  const jitter = () => {
    // Deterministic pseudo-random placement so the map does not reshuffle on
    // every render; the simulation settles it from here.
    seed = (seed * 9301 + 49297) % 233280;
    return seed / 233280 - 0.5;
  };

  for (const hit of hits) {
    if (nodes.has(hit.chunk_id)) continue;
    const citation = citedByChunk.get(hit.chunk_id);
    const docKey = hit.doc_id || hit.doc_title;

    if (!nodes.has(docKey)) {
      nodes.set(docKey, {
        id: docKey,
        kind: "doc",
        label: hit.doc_title || "untitled",
        docId: hit.doc_id,
        x: cx + jitter() * width * 0.8,
        y: cy + jitter() * height * 0.8,
        vx: 0,
        vy: 0,
        cited: false,
        passages: 0,
      });
    }
    const doc = nodes.get(docKey)!;
    doc.passages = (doc.passages ?? 0) + 1;
    if (citation) doc.cited = true;

    nodes.set(hit.chunk_id, {
      id: hit.chunk_id,
      kind: "passage",
      label: `p${hit.page}`,
      docId: hit.doc_id,
      page: hit.page,
      method: hit.method,
      score: hit.score,
      confidence: hit.confidence,
      n: citation?.n ?? null,
      hit,
      citation,
      x: cx + jitter() * width * 0.5,
      y: cy + jitter() * height * 0.5,
      vx: 0,
      vy: 0,
    });

    edges.push({
      source: "query",
      target: hit.chunk_id,
      kind: "retrieval",
      method: hit.method,
      weight: Math.max(0.15, Math.min(1, hit.score / maxScore)),
    });
    edges.push({ source: hit.chunk_id, target: docKey, kind: "belongs", weight: 1 });
  }

  return { nodes: [...nodes.values()], edges };
}

/** One tick of the simulation. Plain Verlet-ish integration with damping. */
function tick(nodes: MapNode[], edges: MapEdge[], width: number, height: number, alpha: number) {
  const byId = new Map(nodes.map((n) => [n.id, n]));

  // Repulsion between every pair. n² is fine at this size.
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i];
      const b = nodes[j];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 1) {
        dx = 0.5;
        dy = 0.5;
        d2 = 0.5;
      }
      const both = a.kind === "doc" && b.kind === "doc";
      const strength = (both ? 9000 : a.kind === "doc" || b.kind === "doc" ? 4600 : 2200) / d2;
      const d = Math.sqrt(d2);
      const fx = (dx / d) * strength * alpha;
      const fy = (dy / d) * strength * alpha;
      a.vx -= fx;
      a.vy -= fy;
      b.vx += fx;
      b.vy += fy;
    }
  }

  // Springs along edges. A better-scored retrieval pulls its passage closer.
  for (const edge of edges) {
    const a = byId.get(edge.source);
    const b = byId.get(edge.target);
    if (!a || !b) continue;
    const rest = edge.kind === "retrieval" ? 150 - 70 * edge.weight : 74;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const d = Math.max(1, Math.sqrt(dx * dx + dy * dy));
    const k = edge.kind === "retrieval" ? 0.035 : 0.06;
    const f = (d - rest) * k * alpha;
    const fx = (dx / d) * f;
    const fy = (dy / d) * f;
    a.vx += fx;
    a.vy += fy;
    b.vx -= fx;
    b.vy -= fy;
  }

  // Gravity to the centre keeps disconnected islands in frame.
  const cx = width / 2;
  const cy = height / 2;
  for (const n of nodes) {
    n.vx += (cx - n.x) * 0.004 * alpha;
    n.vy += (cy - n.y) * 0.004 * alpha;
  }

  for (const n of nodes) {
    if (n.fx != null && n.fy != null) {
      n.x = n.fx;
      n.y = n.fy;
      n.vx = 0;
      n.vy = 0;
      continue;
    }
    n.vx *= 0.82;
    n.vy *= 0.82;
    n.x += n.vx;
    n.y += n.vy;
    const pad = 28;
    n.x = Math.max(pad, Math.min(width - pad, n.x));
    n.y = Math.max(pad, Math.min(height - pad, n.y));
  }
}

export function EvidenceMap({
  question,
  trace,
  citations,
  running,
  className,
}: {
  question: string;
  trace: TraceItem[];
  citations: Citation[];
  running: boolean;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 480, height: 420 });
  const graphRef = useRef<{ nodes: MapNode[]; edges: MapEdge[] }>({ nodes: [], edges: [] });
  const [, setFrame] = useState(0);
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const alphaRef = useRef(1);
  const maxScoreRef = useRef(1);
  const showCitation = useInspector((s) => s.showCitation);
  const showHit = useInspector((s) => s.showHit);
  const openDocument = useViewer((s) => s.openDocument);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const measure = () =>
      setSize({ width: Math.max(320, element.clientWidth), height: Math.max(320, element.clientHeight) });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Rebuild when the evidence changes; keep positions of nodes we already had
  // so a new passage arriving mid-run does not reshuffle the whole map.
  const signature = useMemo(
    () =>
      `${trace.filter((t) => t.kind === "retrieval").length}:${citations.map((c) => c.chunk_id).join(",")}`,
    [trace, citations],
  );
  useEffect(() => {
    const previous = new Map(graphRef.current.nodes.map((n) => [n.id, n]));
    const next = buildGraph(question, trace, citations, size.width, size.height);
    for (const node of next.nodes) {
      const old = previous.get(node.id);
      if (old && node.kind !== "query") {
        node.x = old.x;
        node.y = old.y;
      }
    }
    graphRef.current = next;
    maxScoreRef.current =
      next.nodes.reduce((m, n) => Math.max(m, n.kind === "passage" ? (n.score ?? 0) : 0), 0) || 1;
    alphaRef.current = 1;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, size.width, size.height, question]);

  // The animation loop. Runs while the layout is still settling or while the
  // run is live (so new nodes ease in), then stops to save the CPU.
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      const { nodes, edges } = graphRef.current;
      if (nodes.length && (alphaRef.current > 0.01 || drag.current)) {
        tick(nodes, edges, size.width, size.height, alphaRef.current);
        alphaRef.current = Math.max(0.005, alphaRef.current * 0.97);
        setFrame((f) => f + 1);
      } else if (running) {
        setFrame((f) => f + 1);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [size.width, size.height, running]);

  const { nodes, edges } = graphRef.current;
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const hovered = hover ? byId.get(hover) : null;

  function toLocal(event: React.PointerEvent) {
    const rect = containerRef.current!.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left - view.x) / view.k,
      y: (event.clientY - rect.top - view.y) / view.k,
    };
  }

  function onPointerDown(node: MapNode, event: React.PointerEvent) {
    if (node.kind === "query") return;
    const p = toLocal(event);
    drag.current = { id: node.id, dx: node.x - p.x, dy: node.y - p.y };
    node.fx = node.x;
    node.fy = node.y;
    alphaRef.current = Math.max(alphaRef.current, 0.3);
    (event.target as Element).setPointerCapture(event.pointerId);
  }

  function onPointerMove(event: React.PointerEvent) {
    if (!drag.current) return;
    const node = byId.get(drag.current.id);
    if (!node) return;
    const p = toLocal(event);
    node.fx = p.x + drag.current.dx;
    node.fy = p.y + drag.current.dy;
    alphaRef.current = Math.max(alphaRef.current, 0.2);
  }

  function onPointerUp() {
    if (!drag.current) return;
    const node = byId.get(drag.current.id);
    if (node) {
      node.fx = null;
      node.fy = null;
    }
    drag.current = null;
  }

  function onWheel(event: React.WheelEvent) {
    const rect = containerRef.current!.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1;
    setView((v) => {
      const k = Math.max(0.5, Math.min(2.5, v.k * factor));
      return { k, x: mx - (mx - v.x) * (k / v.k), y: my - (my - v.y) * (k / v.k) };
    });
  }

  function activate(node: MapNode) {
    if (node.kind === "passage" && node.hit) {
      if (node.citation) showCitation(node.citation);
      else showHit(node.hit, null);
    } else if (node.kind === "doc" && node.docId) {
      openDocument(node.docId, 1);
      useInspector.getState().setTab("source");
      useInspector.getState().setSourceMode("page");
    }
  }

  const passages = nodes.filter((n) => n.kind === "passage").length;
  const docs = nodes.filter((n) => n.kind === "doc").length;

  if (!passages) {
    return (
      <div className={cn("flex h-full items-center justify-center p-6 text-center", className)}>
        <div className="max-w-[16rem]">
          <div className="mx-auto mb-3 h-10 w-10 rounded-full border border-dashed border-border-strong" />
          <p className="text-xs font-medium text-fg-muted">Nothing read yet</p>
          <p className="mt-1 text-2xs leading-relaxed text-fg-subtle">
            When the agent retrieves passages, they appear here as a map — each one tethered to
            the question and to the document it came from.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={cn("relative h-full w-full select-none overflow-hidden evidence-canvas", className)}
      onWheel={onWheel}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerUp}
    >
      <svg width={size.width} height={size.height} className="block">
        <defs>
          <filter id="node-glow" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="3.5" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <radialGradient id="query-fill">
            <stop offset="0%" stopColor="hsl(33 95% 68%)" />
            <stop offset="100%" stopColor="hsl(33 95% 50%)" />
          </radialGradient>
        </defs>

        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {/* edges */}
          {edges.map((edge) => {
            const a = byId.get(edge.source);
            const b = byId.get(edge.target);
            if (!a || !b) return null;
            const passage = edge.kind === "retrieval" ? b : a;
            const cited = passage.n != null;
            const involved =
              hover != null && (hover === a.id || hover === b.id || hovered?.docId === a.id || hovered?.docId === b.id);
            const colour = edge.kind === "retrieval" ? methodColour(edge.method) : "hsl(215 12% 48%)";
            const width = edge.kind === "retrieval" ? 0.8 + edge.weight * 2.2 : 1;
            return (
              <line
                key={`${edge.source}-${edge.target}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={colour}
                strokeWidth={involved ? width + 1 : width}
                strokeOpacity={
                  involved ? 0.95 : edge.kind === "retrieval" ? (cited ? 0.55 : 0.18) : cited ? 0.4 : 0.15
                }
                strokeDasharray={edge.kind === "belongs" ? "2 3" : undefined}
                className={cited && running ? "edge-live" : undefined}
              />
            );
          })}

          {/* document nodes */}
          {nodes
            .filter((n) => n.kind === "doc")
            .map((n) => {
              const r = 13 + Math.min(10, (n.passages ?? 1) * 2);
              const active = hover === n.id || hovered?.docId === n.id;
              return (
                <g
                  key={n.id}
                  transform={`translate(${n.x} ${n.y})`}
                  className="cursor-grab active:cursor-grabbing"
                  onPointerDown={(e) => onPointerDown(n, e)}
                  onPointerEnter={() => setHover(n.id)}
                  onPointerLeave={() => setHover(null)}
                  onClick={() => activate(n)}
                >
                  <circle r={r + 6} fill="hsl(222 18% 12%)" stroke={n.cited ? "hsl(33 95% 58% / 0.35)" : "hsl(222 14% 28%)"} strokeWidth={1} />
                  <circle
                    r={r}
                    fill={n.cited ? "hsl(222 16% 20%)" : "hsl(222 16% 15%)"}
                    stroke={n.cited ? "hsl(33 95% 58%)" : "hsl(215 12% 40%)"}
                    strokeWidth={active ? 2 : 1.25}
                    filter={active ? "url(#node-glow)" : undefined}
                  />
                  <text
                    textAnchor="middle"
                    dy="0.35em"
                    fontSize={9}
                    fontWeight={600}
                    fill={n.cited ? "hsl(33 95% 68%)" : "hsl(215 14% 65%)"}
                    className="pointer-events-none font-mono"
                  >
                    {n.passages}
                  </text>
                  <text
                    textAnchor="middle"
                    y={r + 16}
                    fontSize={9.5}
                    fill="hsl(215 14% 72%)"
                    className="pointer-events-none"
                  >
                    {truncate(n.label, 26)}
                  </text>
                </g>
              );
            })}

          {/* passage nodes */}
          {nodes
            .filter((n) => n.kind === "passage")
            .map((n) => {
              const cited = n.n != null;
              const active = hover === n.id;
              const colour = methodColour(n.method);
              const shaky = (n.confidence ?? 1) < 0.85;
              return (
                <g
                  key={n.id}
                  transform={`translate(${n.x} ${n.y})`}
                  className="cursor-pointer"
                  onPointerDown={(e) => onPointerDown(n, e)}
                  onPointerEnter={() => setHover(n.id)}
                  onPointerLeave={() => setHover(null)}
                  onClick={() => activate(n)}
                >
                  {cited ? (
                    <circle r={13} fill={colour} fillOpacity={0.12} className={running ? "node-pulse" : undefined} />
                  ) : null}
                  <circle
                    r={cited ? 8 : 5.5}
                    fill={cited ? colour : "hsl(222 16% 16%)"}
                    stroke={shaky ? "hsl(38 92% 55%)" : colour}
                    strokeWidth={active ? 2.5 : cited ? 1.5 : 1.25}
                    strokeDasharray={shaky ? "2 1.5" : undefined}
                    opacity={cited ? 1 : 0.7}
                    filter={active || cited ? "url(#node-glow)" : undefined}
                  />
                  {cited ? (
                    <text
                      textAnchor="middle"
                      dy="0.35em"
                      fontSize={8.5}
                      fontWeight={700}
                      fill="hsl(24 40% 8%)"
                      className="pointer-events-none font-mono"
                    >
                      {n.n}
                    </text>
                  ) : null}
                </g>
              );
            })}

          {/* the question */}
          {nodes
            .filter((n) => n.kind === "query")
            .map((n) => (
              <g key={n.id} transform={`translate(${n.x} ${n.y})`}>
                <circle r={26} fill="hsl(33 95% 58% / 0.10)" className="node-pulse" />
                <circle r={17} fill="url(#query-fill)" filter="url(#node-glow)" />
                <text
                  textAnchor="middle"
                  dy="0.35em"
                  fontSize={10}
                  fontWeight={700}
                  fill="hsl(24 40% 8%)"
                  className="pointer-events-none"
                >
                  Q
                </text>
              </g>
            ))}
        </g>
      </svg>

      {/* hover card */}
      {hovered ? (
        <div
          className="pointer-events-none absolute z-10 max-w-[17rem] rounded-md border border-border-strong bg-surface/95 p-2 text-2xs shadow-xl backdrop-blur"
          style={{
            left: Math.min(size.width - 280, hovered.x * view.k + view.x + 16),
            top: Math.min(size.height - 120, hovered.y * view.k + view.y + 12),
          }}
        >
          {hovered.kind === "query" ? (
            <p className="text-fg-muted">{hovered.label}</p>
          ) : hovered.kind === "doc" ? (
            <>
              <p className="font-medium text-fg">{hovered.label}</p>
              <p className="mt-0.5 text-fg-subtle">
                {hovered.passages} passage{hovered.passages === 1 ? "" : "s"} retrieved
                {hovered.cited ? " · cited" : " · not cited"}
              </p>
            </>
          ) : (
            <>
              <p className="flex items-center gap-1.5 font-medium text-fg">
                {hovered.n != null ? (
                  <span className="rounded bg-accent/20 px-1 font-mono text-accent">[{hovered.n}]</span>
                ) : (
                  <span className="rounded bg-surface-raised px-1 text-fg-subtle">not cited</span>
                )}
                <span className="truncate">{hovered.hit?.doc_title}</span>
                <span className="tnum shrink-0 text-fg-subtle">p{hovered.page}</span>
              </p>
              <p className="mt-1 line-clamp-3 leading-snug text-fg-muted">{hovered.hit?.snippet}</p>
              <p className="mt-1 flex gap-2 text-fg-subtle">
                <span style={{ color: methodColour(hovered.method) }}>{hovered.method}</span>
                <span className="tnum">match {Math.round(((hovered.score ?? 0) / maxScoreRef.current) * 100)}%</span>
                {(hovered.confidence ?? 1) < 0.85 ? (
                  <span className="tnum text-warn">OCR {((hovered.confidence ?? 0) * 100).toFixed(0)}%</span>
                ) : null}
              </p>
            </>
          )}
        </div>
      ) : null}

      {/* legend */}
      <div className="pointer-events-none absolute bottom-2 left-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-border bg-surface/80 px-2 py-1 text-2xs text-fg-subtle backdrop-blur">
        <span className="tnum">
          {docs} doc{docs === 1 ? "" : "s"} · {passages} passage{passages === 1 ? "" : "s"} · {citations.length} cited
        </span>
        {(["hybrid", "dense", "sparse"] as const).map((m) => (
          <span key={m} className="flex items-center gap-1">
            <span className="h-1.5 w-3 rounded-full" style={{ background: METHOD_COLOUR[m] }} />
            {m}
          </span>
        ))}
        <span className="text-fg-subtle/70">drag · scroll to zoom</span>
      </div>
    </div>
  );
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
