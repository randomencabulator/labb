"use client";

// Diagrams as typed data: boxes and arrows, laid out by ELK and drawn with React Flow. The nodes are
// an object keyed by id, and every edge must name ids that are there: a typo is an error when the
// page builds, not a broken picture.
//
//   const flow = defineDiagram({
//     direction: "right",
//     nodes: {
//       user: { label: "Visitor", kind: "person" },
//       web: { label: "Web app", kind: "service", detail: "Next.js" },
//       db: { label: "Orders", kind: "data" },
//     },
//     edges: [
//       { from: "user", to: "web", label: "visits" },
//       { from: "web", to: "db", label: "reads", dashed: true },
//     ],
//   });
//   <Diagram diagram={flow} height={320} />

import { useEffect, useState } from "react";
import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode } from "elkjs/lib/elk-api";
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import {
  CubeIcon,
  DatabaseIcon,
  FlagIcon,
  NoteIcon,
  PlayIcon,
  QuestionIcon,
  UserIcon,
  type Icon,
} from "@phosphor-icons/react";
import { cn } from "@/lib/cn";

/** What a box stands for, which sets its look */
export type NodeKind = "default" | "start" | "end" | "decision" | "person" | "service" | "data" | "note";

export type DiagramNode = {
  label: string;
  kind?: NodeKind;
  /** A second, smaller line */
  detail?: string;
};

export type DiagramEdge<Id extends string = string> = {
  from: Id;
  to: Id;
  label?: string;
  /** A dashed line: optional, async, or a weaker link */
  dashed?: boolean;
  /** Moving dashes: something flowing right now */
  animated?: boolean;
};

export type DiagramSpec<Id extends string = string> = {
  /** Which way it flows: left to right (the default) or top to bottom */
  direction?: "right" | "down";
  nodes: Record<Id, DiagramNode>;
  edges: readonly DiagramEdge<Id>[];
};

/** A diagram whose edges are checked against its nodes' ids */
export function defineDiagram<const N extends Record<string, DiagramNode>>(spec: {
  direction?: "right" | "down";
  nodes: N;
  edges: readonly DiagramEdge<NoInfer<Extract<keyof N, string>>>[];
}): DiagramSpec<Extract<keyof N, string>> {
  return spec as DiagramSpec<Extract<keyof N, string>>;
}

const looks: Record<NodeKind, { box: string; icon?: Icon }> = {
  default: { box: "rounded-lg border-border bg-card" },
  start: { box: "rounded-full border-success bg-success/10", icon: PlayIcon },
  end: { box: "rounded-full border-border bg-muted", icon: FlagIcon },
  decision: { box: "rounded-lg border-warning bg-warning/15", icon: QuestionIcon },
  person: { box: "rounded-lg border-chart-5 bg-chart-5/10", icon: UserIcon },
  service: { box: "rounded-lg border-primary bg-accent", icon: CubeIcon },
  data: { box: "rounded-lg border-chart-2 bg-chart-2/10", icon: DatabaseIcon },
  note: { box: "rounded-md border-dashed border-border bg-background", icon: NoteIcon },
};

type BoxData = DiagramNode & { vertical: boolean };

function Box({ data }: NodeProps<Node<BoxData>>) {
  const look = looks[data.kind ?? "default"];
  const Glyph = look.icon;
  return (
    <div className={cn("flex h-full w-full items-center gap-2 border-2 px-3 text-left", look.box)}>
      <Handle type="target" position={data.vertical ? Position.Top : Position.Left} className="opacity-0" isConnectable={false} />
      {Glyph && <Glyph className="size-4 shrink-0 text-muted-foreground" />}
      <div className="min-w-0">
        <div className="truncate text-sm font-medium text-foreground">{data.label}</div>
        {data.detail && <div className="truncate text-xs text-muted-foreground">{data.detail}</div>}
      </div>
      <Handle type="source" position={data.vertical ? Position.Bottom : Position.Right} className="opacity-0" isConnectable={false} />
    </div>
  );
}

type Point = { x: number; y: number };
type RouteData = { points: Point[]; label?: string; labelAt?: Point };

// A path through ELK's points, its corners rounded
function pathOf(points: Point[]) {
  const [first, ...rest] = points;
  if (!first || !rest.length) return "";
  let d = `M ${first.x} ${first.y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const [a, b, c] = [points[i - 1]!, points[i]!, points[i + 1]!];
    const r = Math.min(8, Math.hypot(b.x - a.x, b.y - a.y) / 2, Math.hypot(c.x - b.x, c.y - b.y) / 2);
    const into = { x: b.x - Math.sign(b.x - a.x) * r, y: b.y - Math.sign(b.y - a.y) * r };
    const out = { x: b.x + Math.sign(c.x - b.x) * r, y: b.y + Math.sign(c.y - b.y) * r };
    d += ` L ${into.x} ${into.y} Q ${b.x} ${b.y} ${out.x} ${out.y}`;
  }
  const last = points[points.length - 1]!;
  return `${d} L ${last.x} ${last.y}`;
}

// An edge drawn along the route ELK found around the boxes, with its label where ELK put it
function Routed({ id, data, markerEnd, style }: EdgeProps<Edge<RouteData>>) {
  if (!data) return null;
  return (
    <>
      <BaseEdge id={id} path={pathOf(data.points)} markerEnd={markerEnd} style={style} />
      {data.label && data.labelAt && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan absolute rounded bg-background px-1 text-xs leading-[18px] whitespace-nowrap text-foreground"
            style={{ transform: `translate(-50%, -50%) translate(${data.labelAt.x}px, ${data.labelAt.y}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { box: Box };
const edgeTypes = { routed: Routed };
const elk = new ELK();

// ELK places boxes of a known size, so each is sized from its text
function sizeOf(node: DiagramNode) {
  const longest = Math.max(node.label.length, (node.detail?.length ?? 0) * 0.85);
  return { width: Math.min(280, Math.max(120, Math.round(longest * 7.5) + (node.kind && node.kind !== "default" ? 64 : 40))), height: node.detail ? 58 : 44 };
}

async function layout(diagram: DiagramSpec): Promise<{ nodes: Node<BoxData>[]; edges: Edge<RouteData>[] }> {
  const vertical = diagram.direction === "down";
  const ids = Object.keys(diagram.nodes);
  const input: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": vertical ? "DOWN" : "RIGHT",
      "elk.spacing.nodeNode": "36",
      "elk.layered.spacing.nodeNodeBetweenLayers": "48",
      "elk.spacing.edgeNode": "16",
      "elk.spacing.edgeEdge": "12",
      "elk.spacing.edgeLabel": "4",
      "elk.edgeRouting": "ORTHOGONAL",
      // Boxes in the order they're written, and a loop's edge back is the one drawn backwards
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.layered.cycleBreaking.strategy": "MODEL_ORDER",
    },
    children: ids.map((id) => ({ id, ...sizeOf(diagram.nodes[id]!) })),
    edges: diagram.edges.map((edge, i) => ({
      id: `e${i}`,
      sources: [edge.from],
      targets: [edge.to],
      labels: edge.label ? [{ text: edge.label, width: edge.label.length * 6.5 + 12, height: 18 }] : [],
    })),
  };
  const graph = await elk.layout(input);
  const nodes = (graph.children ?? []).map((child) => ({
    id: child.id,
    type: "box",
    position: { x: child.x ?? 0, y: child.y ?? 0 },
    data: { ...diagram.nodes[child.id]!, vertical },
    style: { width: child.width, height: child.height },
  }));
  const routes = new Map((graph.edges ?? []).map((edge) => [edge.id, edge]));
  const edges = diagram.edges.map((edge, i) => {
    const route = routes.get(`e${i}`);
    const section = route?.sections?.[0];
    const label = route?.labels?.[0];
    return {
      id: `e${i}`,
      source: edge.from,
      target: edge.to,
      type: "routed",
      data: {
        points: section ? [section.startPoint, ...(section.bendPoints ?? []), section.endPoint] : [],
        label: edge.label,
        labelAt: label ? { x: (label.x ?? 0) + (label.width ?? 0) / 2, y: (label.y ?? 0) + (label.height ?? 0) / 2 } : undefined,
      },
      animated: edge.animated,
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
      style: { stroke: "var(--muted-foreground)", strokeWidth: 1.5, strokeDasharray: edge.dashed ? "5 4" : undefined },
    };
  });
  return { nodes, edges };
}

export type DiagramProps = {
  diagram: DiagramSpec;
  /** Height in pixels (400); the width fills its container */
  height?: number;
  /** Zoom and pan controls in the corner */
  controls?: boolean;
  className?: string;
};

// ?theme= in the address (app/layout.tsx puts it on <html>), else the system's
function colorMode(): "light" | "dark" | "system" {
  const theme = document.documentElement.dataset.theme;
  return theme === "light" || theme === "dark" ? theme : "system";
}

/** Draws a diagram made with defineDiagram */
export function Diagram({ diagram, height = 400, controls = true, className }: DiagramProps) {
  const [laid, setLaid] = useState<{ nodes: Node<BoxData>[]; edges: Edge<RouteData>[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    layout(diagram).then(
      (result) => current && setLaid(result),
      (failed: unknown) => current && setError(String(failed)),
    );
    return () => {
      current = false;
    };
  }, [diagram]);

  return (
    <div className={cn("w-full overflow-hidden rounded-xl border bg-background", className)} style={{ height }}>
      {error ? (
        <p className="p-4 text-sm text-destructive">The diagram couldn't be laid out: {error}</p>
      ) : (
        laid && (
          <ReactFlow
            nodes={laid.nodes}
            edges={laid.edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            nodesConnectable={false}
            elementsSelectable={false}
            zoomOnScroll={false}
            preventScrolling={false}
            colorMode={colorMode()}
          >
            <Background gap={20} size={1} color="var(--border)" />
            {controls && <Controls showInteractive={false} />}
          </ReactFlow>
        )
      )}
    </div>
  );
}
