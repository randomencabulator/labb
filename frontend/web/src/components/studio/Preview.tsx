// A workflow drawn read-only, as in the builder: the drafting room's blueprint, with what changed
import { type Edge, ReactFlow, ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";
import type { Catalog, Definition } from "../../studioApi";
import { type Diff, arrange, handlesOf, summary } from "./graph";
import { StepBox, type StepBoxNode } from "./StepBox";

const NODE_TYPES = { step: StepBox };

export type Names = { agents: Record<string, string>; actions: Record<string, string> };

/** The steps to draw for a changed workflow: the new ones, and the removed ones from before, faded */
export function withRemoved(definition: Definition, before: Definition | null, diffs: Map<string, Diff>): Definition {
  if (!before) return definition;
  const gone = before.nodes.filter(n => diffs.get(n.id) === "removed");
  const goneIds = new Set(gone.map(n => n.id));
  return {
    nodes: [...definition.nodes, ...gone],
    edges: [...definition.edges, ...before.edges.filter(e => goneIds.has(e.source) || goneIds.has(e.target))],
  };
}

export function StepNodes(definition: Definition, catalog: Catalog, names: Names, diffs: Map<string, Diff>, problems: Record<string, string[]> = {}): StepBoxNode[] {
  return definition.nodes.map(step => ({
    id: step.id,
    type: "step" as const,
    position: step.position,
    draggable: false,
    selectable: false,
    data: {
      step,
      category: catalog.nodes[step.type]?.category ?? "logic",
      kindLabel: catalog.nodes[step.type]?.label ?? step.type,
      summary: summary(step, names),
      handles: handlesOf(step),
      problems: problems[step.id] ?? [],
      diff: diffs.get(step.id),
    },
  }));
}

export function StepEdges(definition: Definition, diffs: Map<string, Diff>): Edge[] {
  return definition.edges.map(edge => {
    const removed = diffs.get(edge.source) === "removed" || diffs.get(edge.target) === "removed";
    const added = diffs.get(edge.source) === "added" || diffs.get(edge.target) === "added";
    return {
      id: edge.id, source: edge.source, target: edge.target, sourceHandle: edge.handle ?? "out", label: edge.handle ?? undefined,
      className: removed ? "diff-removed" : added ? "diff-added" : undefined,
    };
  });
}

function PreviewView({ definition, before, diffs, catalog, names }: {
  definition: Definition; before: Definition | null; diffs: Map<string, Diff>; catalog: Catalog; names: Names;
}) {
  // Top to bottom, to read in a narrow box
  const shown = useMemo(() => arrange(withRemoved(definition, before, diffs), "TB"), [definition, before, diffs]);
  const nodes = useMemo(() => StepNodes(shown, catalog, names, diffs), [shown, catalog, names, diffs]);
  const edges = useMemo(() => StepEdges(shown, diffs), [shown, diffs]);
  return (
    <ReactFlow key={shown.nodes.map(n => n.id).join()} nodes={nodes} edges={edges} nodeTypes={NODE_TYPES} fitView minZoom={0.1}
      fitViewOptions={{ padding: 0.12, maxZoom: 0.9, minZoom: 0.1 }}
      onInit={flow => requestAnimationFrame(() => flow.fitView({ padding: 0.12, maxZoom: 0.9, minZoom: 0.1 }))} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
      panOnScroll zoomOnScroll={false} proOptions={{ hideAttribution: true }} defaultEdgeOptions={{ type: "smoothstep" }} />
  );
}

export function Preview(props: Parameters<typeof PreviewView>[0]) {
  return (
    <div className="canvas preview-canvas">
      <ReactFlowProvider>
        <PreviewView {...props} />
      </ReactFlowProvider>
    </div>
  );
}
