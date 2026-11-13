// A workflow's graph in the builder: what a step can refer to, its ways out, new steps and layout
import { Graph, layout } from "@dagrejs/dagre";
import type { Catalog, Definition, StepEdge, StepNode } from "../../studioApi";

export const NODE_WIDTH = 230;
export const NODE_HEIGHT = 86;

/** The ways out of a step: null for the plain one */
export function handlesOf(node: StepNode): (string | null)[] {
  if (node.type === "condition") {
    if (node.config.mode === "model") return ((node.config.choices as string[]) ?? []).map(String);
    return ["true", "false"];
  }
  if (node.type === "approval") return ["approved", "rejected"];
  return [null];
}

/** The ids of the steps before a step */
export function ancestors(definition: Definition, id: string): Set<string> {
  const found = new Set<string>();
  const todo = [id];
  while (todo.length) {
    const current = todo.pop()!;
    for (const edge of definition.edges) {
      if (edge.target === current && !found.has(edge.source)) {
        found.add(edge.source);
        todo.push(edge.source);
      }
    }
  }
  return found;
}

/** What a step's settings can use: {{trigger.x}} for each input, {{key}} for each step before it */
export function referencesFor(definition: Definition, id: string): { ref: string; label: string }[] {
  const before = ancestors(definition, id);
  const node = definition.nodes.find(n => n.id === id);
  const refs: { ref: string; label: string }[] = [];
  const trigger = definition.nodes.find(n => n.type === "trigger");
  for (const input of (trigger?.config.inputs as { name: string; label?: string }[]) ?? []) {
    if (input.name) refs.push({ ref: `trigger.${input.name}`, label: `Input: ${input.label || input.name}` });
  }
  if (trigger?.config.webhook) refs.push({ ref: "trigger.body", label: "The webhook's whole JSON" });
  if (((trigger?.config.events as string[]) ?? []).length) refs.push({ ref: "trigger.event", label: "The event" });
  for (const other of definition.nodes) {
    if (before.has(other.id) && other.type !== "trigger") refs.push({ ref: other.key, label: other.label || other.key });
  }
  if (node?.type === "foreach") {
    refs.push({ ref: "item", label: "The item (in the step for each)" }, { ref: "index", label: "Its number, from 0" });
  }
  refs.push({ ref: "now", label: "The time now" });
  return refs;
}

export function uniqueKey(definition: Definition, base: string): string {
  const clean = base.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "step";
  const start = /^[a-z]/.test(clean) ? clean : `s_${clean}`;
  const taken = new Set(definition.nodes.map(n => n.key));
  if (!taken.has(start)) return start;
  for (let i = 2; ; i++) if (!taken.has(`${start}_${i}`)) return `${start}_${i}`;
}

export function newNode(catalog: Catalog, definition: Definition, type: string, position: { x: number; y: number }): StepNode {
  const spec = catalog.nodes[type];
  const config: Record<string, unknown> = {};
  for (const field of spec.fields) if (field.default !== null && field.default !== undefined) config[field.name] = structuredClone(field.default);
  const id = `n${Math.random().toString(36).slice(2, 9)}`;
  return { id, key: uniqueKey(definition, spec.label), type, label: spec.label, position, config };
}

export function edgeId(source: string, handle: string | null, target: string) {
  return `${source}-${handle ?? "out"}-${target}`;
}

/** Steps laid out left to right (or top to bottom), connections kept */
export function arrange(definition: Definition, direction: "LR" | "TB" = "LR"): Definition {
  const graph = new Graph();
  graph.setGraph({ rankdir: direction, nodesep: 40, ranksep: direction === "LR" ? 70 : 36 });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const node of definition.nodes) graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const edge of definition.edges) graph.setEdge(edge.source, edge.target);
  layout(graph);
  return {
    ...definition,
    nodes: definition.nodes.map(node => {
      const placed = graph.node(node.id);
      return placed ? { ...node, position: { x: placed.x - NODE_WIDTH / 2, y: placed.y - NODE_HEIGHT / 2 } } : node;
    }),
  };
}

/** Whether connecting these would make the steps go round in a circle */
export function wouldCycle(definition: Definition, source: string, target: string): boolean {
  return source === target || ancestors(definition, source).has(target);
}

/** A short line about what a step does, for its box on the canvas */
export function summary(node: StepNode, names: { agents: Record<string, string>; actions: Record<string, string> }): string {
  const c = node.config;
  const text = (value: unknown) => String(value ?? "").replace(/\s+/g, " ").trim();
  switch (node.type) {
    case "trigger": {
      const parts = [];
      const inputs = (c.inputs as unknown[]) ?? [];
      if (inputs.length) parts.push(`${inputs.length} input${inputs.length === 1 ? "" : "s"}`);
      if (c.schedule) parts.push("scheduled");
      if (c.webhook) parts.push("webhook");
      if (((c.events as unknown[]) ?? []).length) parts.push("on events");
      return parts.join(" · ") || "By hand";
    }
    case "agent":
      return `${c.agent_id ? names.agents[c.agent_id as string] ?? "An agent" : "labb"}: ${text(c.prompt)}`;
    case "prompt":
      return text(c.prompt);
    case "skill":
      return `/${text(c.skill)} ${text(c.prompt)}`;
    case "tool":
      return text(c.tool) || "Pick a tool";
    case "action":
      return names.actions[c.action_id as string] ?? "Pick an action";
    case "python":
      return text(c.code).slice(0, 80);
    case "condition":
      return c.mode === "model" ? text(c.question) : `${((c.rules as unknown[]) ?? []).length} rule(s)`;
    case "foreach":
      return `${text(c.items)} → ${text(c.step_type)}`;
    case "approval":
      return text(c.message);
    case "wait":
      return `${c.minutes} min`;
    case "transform":
      return text(c.template);
    case "chat":
      return text(c.title);
    case "document":
      return text(c.name);
    case "page":
      return `${c.same_page ? "One page: " : ""}${text(c.title) || text(c.prompt)}`;
    case "respond":
      return text(c.value);
    default:
      return "";
  }
}

export type Diff = "added" | "changed" | "removed";

/** How a changed workflow differs from the one before, step by step (positions don't count) */
export function diffSteps(before: Definition, after: Definition): Map<string, Diff> {
  const found = new Map<string, Diff>();
  const old = new Map(before.nodes.map(n => [n.id, n]));
  const kept = new Set(after.nodes.map(n => n.id));
  const same = (a: StepNode, b: StepNode) =>
    a.type === b.type && a.label === b.label && a.key === b.key && JSON.stringify(a.config) === JSON.stringify(b.config);
  for (const node of after.nodes) {
    const was = old.get(node.id);
    if (!was) found.set(node.id, "added");
    else if (!same(was, node)) found.set(node.id, "changed");
  }
  for (const node of before.nodes) if (!kept.has(node.id)) found.set(node.id, "removed");
  return found;
}

/** The steps in the order they run: by how far from the start, then top to bottom */
export function inOrder(definition: Definition): StepNode[] {
  const depth = new Map<string, number>();
  const of = (id: string, seen: Set<string> = new Set()): number => {
    if (depth.has(id)) return depth.get(id)!;
    const parents = definition.edges.filter(e => e.target === id && !seen.has(e.source)).map(e => e.source);
    const d = parents.length ? 1 + Math.max(...parents.map(p => of(p, new Set([...seen, id])))) : 0;
    depth.set(id, d);
    return d;
  };
  return [...definition.nodes].sort((a, b) => of(a.id) - of(b.id) || a.position.y - b.position.y);
}

export type { StepEdge };
