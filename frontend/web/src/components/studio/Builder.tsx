// The workflow builder: the steps (a canvas, or a list), Ask labb, and a panel for settings and runs
import {
  Background,
  type Connection,
  Controls,
  type Edge,
  type EdgeChange,
  MiniMap,
  type NodeChange,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { type DragEvent, type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Agent, Model, Skill } from "../../api";
import { type Action, type Catalog, type Change, type Definition, type Problem, type Run, type StepNode, type StepState, type Workflow, studio } from "../../studioApi";
import { toast } from "../Toaster";
import { type Diff, NODE_HEIGHT, NODE_WIDTH, arrange, diffSteps, edgeId, handlesOf, newNode, summary, uniqueKey, wouldCycle } from "./graph";
import { withRemoved } from "./Preview";
import { StepInspector, WorkflowInspector } from "./Inspector";
import { RunForm, RunList, RunPanel } from "./RunPanel";
import { StepList, StepPicker } from "./StepList";
import { StepBox, type StepBoxNode } from "./StepBox";

const NODE_TYPES = { step: StepBox };
const SAVE_DELAY = 700;
const HISTORY = 50;

/** A change Ask labb made, shown on the canvas until it's kept or discarded */
type Pending = {
  base: Definition;
  proposed: Definition;
  changes: Change[];
  reply: string;
  problems: Problem[];
  // What it was asked, or what it fixes
  asked: string;
};

type Props = {
  id: string;
  catalog: Catalog;
  agents: Agent[];
  models: Model[];
  skills: Skill[];
  actions: Action[];
  onBack: () => void;
  onOpenChat: (threadId: string) => void;
  // The run the address opens, and when another one is opened here (or it's closed)
  openRunId?: string | null;
  onRunChanged?: (runId: string | null) => void;
};

function BuilderView({ id, catalog, agents, models, skills, actions, onBack, onOpenChat, openRunId = null, onRunChanged }: Props) {
  const flow = useReactFlow();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [definition, setDefinition] = useState<Definition | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [saving, setSaving] = useState<"saved" | "saving" | "error">("saved");
  const [panel, setPanelOnly] = useState<"settings" | "runs" | "start" | "changes" | "explain">("settings");
  // On small screens the side panel is a sheet over the bottom of the screen: open, or just its tabs
  const [sheet, setSheet] = useState(false);
  const setPanel = (next: typeof panel) => (setPanelOnly(next), setSheet(true));
  // The steps on a canvas, or as a list (the default on phones)
  const [layout, setLayout] = useState<"canvas" | "list">(() => (window.matchMedia("(max-width: 640px)").matches ? "list" : "canvas"));
  const [adding, setAdding] = useState(false);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [prompt, setPrompt] = useState("");
  const [asking, setAsking] = useState(false);
  // Steps picked with Shift+click: Ask labb changes only those
  const [picked, setPicked] = useState<string[]>([]);
  const [explained, setExplained] = useState<{ of: string; text: string } | null>(null);
  const askBox = useRef<HTMLInputElement>(null);
  const [runId, setRunId] = useState<string | null>(openRunId ?? null);
  const [runSteps, setRunSteps] = useState<StepState[]>([]);
  const [runDraft, setRunDraft] = useState(true);
  const [starting, setStarting] = useState(false);
  const [webhookPath, setWebhookPath] = useState<string | null>(null);
  const [showProblems, setShowProblems] = useState(false);
  // Each step's size as drawn (React Flow measures them; the minimap needs them back)
  const [sizes, setSizes] = useState<Record<string, { width: number; height: number }>>({});
  const past = useRef<Definition[]>([]);
  const future = useRef<Definition[]>([]);
  const copied = useRef<StepNode | null>(null);
  const dirty = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    studio.workflow(id).then(
      loaded => {
        setWorkflow(loaded);
        setDefinition(loaded.draft);
        if (openRunId) setPanel("runs");
      },
      e => toast((e as Error).message),
    );
  }, [id]);

  // The address and the open run follow each other: Back and Forward open the run there, a run opened here goes there
  const shownRun = useRef(runId);
  useEffect(() => {
    if (openRunId === shownRun.current) return;
    shownRun.current = openRunId;
    setRunSteps([]);
    setRunId(openRunId);
    if (openRunId) setPanel("runs");
    else setPanelOnly(p => (p === "runs" ? p : "settings"));
  }, [openRunId]);
  useEffect(() => {
    if (runId === shownRun.current) return;
    shownRun.current = runId;
    onRunChanged?.(runId);
  }, [runId]);

  const readOnly = workflow ? !workflow.mine : true;
  const previewing = pending !== null;

  // Changes save on their own, a moment after the last one
  const save = useCallback((fields: Partial<Workflow>) => {
    if (!workflow) return;
    setSaving("saving");
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      try {
        const saved = await studio.update(workflow.id, fields);
        dirty.current = false;
        setWorkflow(current => (current ? { ...saved, draft: current.draft, name: current.name, emoji: current.emoji, description: current.description } : saved));
        setSaving("saved");
      } catch (e) {
        setSaving("error");
        toast((e as Error).message);
      }
    }, SAVE_DELAY);
  }, [workflow?.id]);

  /** Change the steps; `record` keeps the change for undo */
  const change = useCallback((next: Definition | ((d: Definition) => Definition), record = true) => {
    setDefinition(current => {
      if (!current || readOnly) return current;
      const updated = typeof next === "function" ? next(current) : next;
      if (updated === current) return current;
      if (record) {
        past.current = [...past.current.slice(-HISTORY + 1), current];
        future.current = [];
      }
      dirty.current = true;
      return updated;
    });
  }, [readOnly]);

  useEffect(() => {
    if (definition && dirty.current) save({ draft: definition });
  }, [definition]);

  const changeMeta = (fields: Partial<Workflow>) => {
    if (!workflow || readOnly) return;
    setWorkflow({ ...workflow, ...fields });
    save(fields);
  };

  const undo = () => {
    const previous = past.current.pop();
    if (!previous || !definition) return;
    future.current.push(definition);
    dirty.current = true;
    setDefinition(previous);
  };
  const redo = () => {
    const next = future.current.pop();
    if (!next || !definition) return;
    past.current.push(definition);
    dirty.current = true;
    setDefinition(next);
  };

  // --- What the canvas shows ------------------------------------------------------

  const names = useMemo(() => ({
    agents: Object.fromEntries(agents.map(a => [a.id, `${a.emoji} ${a.name}`])),
    actions: Object.fromEntries(actions.map(a => [a.id, `${a.emoji} ${a.name}`])),
  }), [agents, actions]);

  const problemsByNode = useMemo(() => {
    const found: Record<string, string[]> = {};
    for (const p of workflow?.problems ?? []) if (p.node) (found[p.node] ??= []).push(p.message);
    return found;
  }, [workflow?.problems]);

  const statusByNode = useMemo(() => new Map(runSteps.map(s => [s.node_id, s.status])), [runSteps]);

  const diffs = useMemo(() => (pending ? diffSteps(pending.base, pending.proposed) : new Map<string, Diff>()), [pending]);
  const view = useMemo(() => (pending ? withRemoved(pending.proposed, pending.base, diffs) : definition),
    [pending, diffs, definition]);

  const nodes: StepBoxNode[] = useMemo(() => (view?.nodes ?? []).map(step => ({
    id: step.id,
    type: "step" as const,
    position: step.position,
    measured: sizes[step.id],
    selected: step.id === selected,
    deletable: step.type !== "trigger" && !readOnly && !previewing,
    draggable: !previewing,
    data: {
      step,
      category: catalog.nodes[step.type]?.category ?? "logic",
      kindLabel: catalog.nodes[step.type]?.label ?? step.type,
      summary: summary(step, names),
      handles: handlesOf(step),
      problems: problemsByNode[step.id] ?? [],
      status: runId && !previewing ? statusByNode.get(step.id) : undefined,
      diff: diffs.get(step.id),
      picked: picked.includes(step.id),
    },
  })), [view, selected, catalog, names, problemsByNode, statusByNode, runId, readOnly, sizes, diffs, picked, previewing]);

  const edges: Edge[] = useMemo(() => (view?.edges ?? []).map(edge => {
    const taken = !previewing && runId && statusByNode.get(edge.target) && statusByNode.get(edge.target) !== "skipped" && statusByNode.get(edge.source) === "done";
    const gone = diffs.get(edge.source) === "removed" || diffs.get(edge.target) === "removed";
    const added = diffs.get(edge.source) === "added" || diffs.get(edge.target) === "added";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.handle ?? "out",
      label: edge.handle ?? undefined,
      animated: Boolean(runId && statusByNode.get(edge.target) === "running"),
      className: gone ? "diff-removed" : added ? "diff-added" : taken ? "taken" : undefined,
      deletable: !readOnly && !previewing,
    };
  }), [view, runId, statusByNode, readOnly, diffs, previewing]);

  const onNodesChange = (changes: NodeChange<StepBoxNode>[]) => {
    const measured: Record<string, { width: number; height: number }> = {};
    for (const c of changes) {
      if (c.type === "select" && c.selected) setSelected(c.id);
      if (c.type === "dimensions" && c.dimensions) measured[c.id] = c.dimensions;
    }
    if (Object.keys(measured).length) setSizes(current => ({ ...current, ...measured }));
    if (previewing) return;
    const moves = changes.filter(c => c.type === "position" && c.position) as { id: string; position: { x: number; y: number }; dragging?: boolean }[];
    const removed = changes.filter(c => c.type === "remove").map(c => (c as { id: string }).id);
    if (moves.length) {
      const finished = moves.some(m => m.dragging === false);
      change(d => ({ ...d, nodes: d.nodes.map(n => { const m = moves.find(x => x.id === n.id); return m ? { ...n, position: m.position } : n; }) }), finished);
    }
    if (removed.length) remove(removed);
  };

  const onEdgesChange = (changes: EdgeChange[]) => {
    const removed = new Set(changes.filter(c => c.type === "remove").map(c => (c as { id: string }).id));
    if (removed.size) change(d => ({ ...d, edges: d.edges.filter(e => !removed.has(e.id)) }));
  };

  const connectable = (c: Connection | Edge) => {
    if (!definition || !c.source || !c.target) return false;
    const target = definition.nodes.find(n => n.id === c.target);
    return target?.type !== "trigger" && !wouldCycle(definition, c.source, c.target);
  };

  const onConnect = (c: Connection) => {
    if (previewing || !connectable(c)) return;
    const handle = c.sourceHandle && c.sourceHandle !== "out" ? c.sourceHandle : null;
    change(d => ({
      ...d,
      edges: [
        ...d.edges.filter(e => !(e.source === c.source && e.target === c.target && e.handle === handle)),
        { id: edgeId(c.source!, handle, c.target!), source: c.source!, target: c.target!, handle },
      ],
    }));
  };

  const remove = (ids: string[]) => {
    const gone = new Set(ids.filter(i => definition?.nodes.find(n => n.id === i)?.type !== "trigger"));
    if (!gone.size) return;
    change(d => ({ nodes: d.nodes.filter(n => !gone.has(n.id)), edges: d.edges.filter(e => !gone.has(e.source) && !gone.has(e.target)) }));
    if (selected && gone.has(selected)) setSelected(null);
  };

  /** Add a step: after the selected one (connected to it), or where it was dropped */
  const add = (type: string, at?: { x: number; y: number }) => {
    if (!definition || readOnly || previewing) return;
    const from = definition.nodes.find(n => n.id === selected);
    const position = at ?? (from ? { x: from.position.x + 290, y: from.position.y } :
      flow.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 }));
    const node = newNode(catalog, definition, type, position);
    const ways = from ? handlesOf(from) : [];
    change(d => ({
      nodes: [...d.nodes, node],
      edges: from && !at && type !== "trigger" ? [...d.edges, { id: edgeId(from.id, ways[0] ?? null, node.id), source: from.id, target: node.id, handle: ways[0] ?? null }] : d.edges,
    }));
    setSelected(node.id);
    setPanel("settings");
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    const type = event.dataTransfer.getData("application/labb-step");
    if (type) add(type, flow.screenToFlowPosition({ x: event.clientX, y: event.clientY }));
  };

  /** Show the workflow from its start: all of it if it fits, else readable and from the left */
  const showStart = (steps: StepNode[], duration = 0) => {
    const canvas = document.querySelector(".builder .canvas")?.getBoundingClientRect();
    if (!canvas || !steps.length) return;
    const xs = steps.map(n => n.position.x), ys = steps.map(n => n.position.y);
    const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs) + NODE_WIDTH, Math.min(...ys), Math.max(...ys) + NODE_HEIGHT];
    const fit = Math.min((canvas.width - 80) / (maxX - minX), (canvas.height - 80) / (maxY - minY));
    // Small enough to see it all, but not so small the steps can't be read
    const readable = canvas.width < 600 ? 0.6 : 0.5;
    const zoom = Math.max(readable, Math.min(1, fit));
    const x = fit >= readable ? (canvas.width - (maxX - minX) * zoom) / 2 - minX * zoom : 40 - minX * zoom;
    flow.setViewport({ x, y: (canvas.height - (maxY - minY) * zoom) / 2 - minY * zoom, zoom }, { duration });
  };
  const shown = useRef(false);
  useEffect(() => {
    if (definition && !shown.current && layout === "canvas") {
      shown.current = true;
      requestAnimationFrame(() => requestAnimationFrame(() => showStart(definition.nodes)));
    }
  }, [definition, layout]);
  useEffect(() => {
    if (layout === "list") shown.current = false;
  }, [layout]);

  // This workflow's runs, when they're shown
  const loadRuns = useCallback(() => {
    studio.runsOf(id).then(setRuns, e => toast((e as Error).message));
  }, [id]);
  useEffect(() => {
    if (panel === "runs" && !runId) loadRuns();
  }, [panel, runId, loadRuns]);

  // In the list, the step being set up stays in sight above the sheet
  useEffect(() => {
    if (layout === "list" && selected) {
      requestAnimationFrame(() => document.querySelector(`.step-list [data-step="${selected}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
    }
  }, [selected, layout, sheet]);

  /** A step of the run, on the canvas or in the list */
  const focusStep = (nodeId: string) => {
    if (layout === "canvas") flow.fitView({ nodes: [{ id: nodeId }], padding: 0.8, maxZoom: 1, minZoom: 0.4, duration: 300 });
    else document.querySelector(`.step-list [data-step="${nodeId}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  const tidy = () => {
    if (!definition) return;
    const arranged = arrange(definition);
    change(arranged);
    requestAnimationFrame(() => showStart(arranged.nodes, 300));
  };

  // Keys: undo/redo, copy/paste a step
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest("input, textarea, select, [contenteditable]");
      if (typing || !(e.ctrlKey || e.metaKey) || previewing) return;
      const key = e.key.toLowerCase();
      if (key === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if (key === "y") {
        e.preventDefault();
        redo();
      } else if (key === "c" && selected) {
        copied.current = definition?.nodes.find(n => n.id === selected && n.type !== "trigger") ?? null;
      } else if (key === "v" && copied.current && definition) {
        e.preventDefault();
        const source = copied.current;
        const node = { ...structuredClone(source), id: `n${Math.random().toString(36).slice(2, 9)}`,
          key: uniqueKey(definition, source.key), position: { x: source.position.x + 40, y: source.position.y + 40 } };
        change(d => ({ ...d, nodes: [...d.nodes, node] }));
        setSelected(node.id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // --- Ask labb: changes in words, previewed before they're kept ----------------------

  const scope = picked.length ? picked : selected ? [selected] : [];
  const scopeLabels = scope.map(id => definition?.nodes.find(n => n.id === id)?.label).filter(Boolean);

  const ask = async (fields: { prompt?: string; run_id?: string }, asked: string) => {
    if (!workflow || !definition) return;
    setAsking(true);
    try {
      const from = pending?.proposed ?? definition;
      const result = await studio.suggest(workflow.id, { ...fields, definition: from, selected: fields.run_id ? [] : scope });
      if (!result.changes.length) {
        toast(result.reply || "Nothing to change there; try saying it another way");
        return;
      }
      setPending({ base: pending?.base ?? definition, proposed: result.definition, reply: result.reply, problems: result.problems,
        changes: [...(pending?.changes ?? []), ...result.changes], asked: pending ? `${pending.asked}, then ${asked}` : asked });
      setPrompt("");
      setPanel("changes");
      // Show what changed
      const touched = result.changes.map(c => ({ id: c.node }));
      requestAnimationFrame(() => requestAnimationFrame(() => layout === "canvas" ?
        flow.fitView({ nodes: touched, padding: 0.6, maxZoom: 1, minZoom: 0.3, duration: 300 }) :
        document.querySelector(`.step-list [data-step="${touched[0]?.id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" })));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setAsking(false);
    }
  };
  const submitPrompt = (event: FormEvent) => {
    event.preventDefault();
    if (prompt.trim() && !asking) ask({ prompt: prompt.trim() }, `“${prompt.trim()}”`);
  };
  const keep = () => {
    if (!pending) return;
    change(pending.proposed);
    setPending(null);
    setPicked([]);
    setPanel("settings");
    toast("Kept. Ctrl+Z takes it back.");
  };
  const discard = () => {
    setPending(null);
    setPanel("settings");
  };
  const fix = (failedRun: string) => ask({ run_id: failedRun }, "a fix for the failed run");

  const explain = async () => {
    setPanel("explain");
    if (!definition) return;
    const of = JSON.stringify({ nodes: definition.nodes.map(({ position: _, ...n }) => n), edges: definition.edges });
    if (explained?.of === of) return;
    setExplained({ of, text: "" });
    try {
      setExplained({ of, text: (await studio.explain(definition)).text });
    } catch (e) {
      setExplained(null);
      toast((e as Error).message);
    }
  };

  // "/" goes to Ask labb
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest("input, textarea, select, [contenteditable]");
      if (!typing && e.key === "/" && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        askBox.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // --- Running and publishing -----------------------------------------------------

  const startRun = async (inputs: Record<string, unknown>) => {
    if (!workflow) return;
    setStarting(true);
    try {
      clearTimeout(saveTimer.current);
      if (dirty.current && definition) await studio.update(workflow.id, { draft: definition });
      dirty.current = false;
      const run = await studio.run(workflow.id, inputs, runDraft);
      setRunSteps([]);
      setRunId(run.id);
      setPanel("runs");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const publish = async () => {
    if (!workflow || !definition) return;
    try {
      clearTimeout(saveTimer.current);
      await studio.update(workflow.id, { draft: definition });
      dirty.current = false;
      const published = await studio.publish(workflow.id);
      setWorkflow(published);
      setSaving("saved");
      toast(`Published v${published.published_version}`);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const setWebhook = async (on: boolean) => {
    if (!workflow) return;
    try {
      const result = await studio.webhook(workflow.id, on);
      setWebhookPath(result.path);
      setWorkflow({ ...workflow, webhook: on });
    } catch (e) {
      toast((e as Error).message);
    }
  };

  if (!workflow || !definition) return <div className="studio-loading muted">Opening…</div>;
  const node = definition.nodes.find(n => n.id === selected) ?? null;
  const problems = workflow.problems;

  return (
    <div className={`builder layout-${layout}`}>
      <div className="builder-bar">
        <button type="button" className="icon-button" aria-label="Back to workflows" onClick={onBack}>←</button>
        <span className="builder-title" title={workflow.name}>
          <span aria-hidden>{workflow.emoji}</span> {workflow.name}
        </span>
        <span className={`save-state ${saving}`}>{readOnly ? `Shared by ${workflow.owner}` : saving === "saving" ? "Saving…" : saving === "error" ? "Not saved" : "Saved"}</span>
        {problems.length > 0 && (
          <button type="button" className="problems-button" onClick={() => setShowProblems(!showProblems)}>
            {problems.length} to fix
          </button>
        )}
        <span className="spacer" />
        {!readOnly && (
          <>
            <span className="undo-group">
              <button type="button" className="icon-button bordered" onClick={undo} disabled={previewing || !past.current.length} title="Undo (Ctrl+Z)" aria-label="Undo">↶</button>
              <button type="button" className="icon-button bordered" onClick={redo} disabled={previewing || !future.current.length} title="Redo (Ctrl+Shift+Z)" aria-label="Redo">↷</button>
            </span>
            <button type="button" disabled={previewing} onClick={() => (setRunDraft(true), setPanel("start"))}>Test<span className="wide-only"> run</span></button>
            <button type="button" className="primary" onClick={publish} disabled={previewing || problems.length > 0} title={problems[0]?.message}>
              Publish{workflow.published_version ? ` (v${workflow.published_version})` : ""}
            </button>
          </>
        )}
        {workflow.published_version !== null && (
          <button type="button" onClick={() => (setRunDraft(false), setPanel("start"))}>Run</button>
        )}
      </div>
      {showProblems && problems.length > 0 && (
        <ul className="problem-list">
          {problems.map((p, i) => (
            <li key={i}>
              <button type="button" className="link" onClick={() => p.node && (setSelected(p.node), setPanel("settings"))}>
                {p.node ? definition.nodes.find(n => n.id === p.node)?.label ?? "A step" : "Workflow"}
              </button>
              : {p.message}
            </li>
          ))}
        </ul>
      )}
      <div className="builder-body">
        <div className="canvas-column">
        {pending && (
          <div className="preview-banner" role="status">
            <b>Preview: {pending.changes.length} change{pending.changes.length === 1 ? "" : "s"}</b>
            <span className="muted small preview-asked" title={pending.asked}>from {pending.asked}</span>
            <span className="preview-actions">
              <button type="button" onClick={discard}>Discard</button>
              <button type="button" onClick={() => askBox.current?.focus()}>Refine</button>
              <button type="button" className="primary" onClick={keep}>Keep</button>
            </span>
          </div>
        )}
        <div className="canvas-area">
        <div className="canvas-tools">
          {!readOnly && (
            <button type="button" className="add-step-button primary" disabled={previewing} aria-expanded={adding}
              onClick={() => setAdding(!adding)}>
              <span aria-hidden>+</span> Add step
            </button>
          )}
          <span className="segmented" role="group" aria-label="Show the steps">
            <button type="button" aria-pressed={layout === "canvas"} onClick={() => setLayout("canvas")}>Canvas</button>
            <button type="button" aria-pressed={layout === "list"} onClick={() => setLayout("list")}>List</button>
          </span>
          {!readOnly && layout === "canvas" && (
            <button type="button" className="canvas-button" onClick={tidy} disabled={previewing} title="Arrange the steps in order">Arrange</button>
          )}
        </div>
        {adding && (
          <StepPicker catalog={catalog} draggable={layout === "canvas"} onClose={() => setAdding(false)}
            after={node?.label ?? null}
            onAdd={type => (add(type), setAdding(false))} />
        )}
        {layout === "list" ? (
          <div className="step-list-scroll" onClick={e => e.target === e.currentTarget && setSelected(null)}>
            <StepList definition={view!} catalog={catalog} names={names} selected={selected} picked={picked}
              problems={problemsByNode} statuses={runId && !previewing ? statusByNode : new Map()} diffs={diffs}
              onSelect={(stepId, shift) => {
                if (shift && !readOnly) setPicked(list => (list.includes(stepId) ? list.filter(x => x !== stepId) : [...list, stepId]));
                else (setSelected(stepId), setPanel("settings"));
              }} />
          </div>
        ) : (
        <div className={`canvas${previewing ? " previewing" : ""}`} onDragOver={e => e.preventDefault()} onDrop={onDrop}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={connectable}
            onNodeClick={(e, n) => {
              if (e.shiftKey && !readOnly) setPicked(list => (list.includes(n.id) ? list.filter(x => x !== n.id) : [...list, n.id]));
              else (setSelected(n.id), setPanel("settings"));
            }}
            onPaneClick={() => setSelected(null)}
            nodesDraggable={!readOnly && !previewing}
            nodesConnectable={!readOnly && !previewing}
            deleteKeyCode={readOnly || previewing ? null : ["Backspace", "Delete"]}
            multiSelectionKeyCode={null}
            minZoom={0.2}
            proOptions={{ hideAttribution: true }}
            defaultEdgeOptions={{ type: "smoothstep" }}
          >
            <Background gap={18} size={1} />
            <MiniMap pannable zoomable nodeStrokeWidth={3} nodeBorderRadius={4} style={{ width: 150, height: 96 }}
              nodeColor={n => `var(--cat-${(n.data as { category?: string }).category ?? "logic"})`} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        )}
        </div>
        {!readOnly && (
          <form className="ask-bar" onSubmit={submitPrompt}>
            <div className="ask-scope">
              <span className="step-kind">Ask labb</span>
              {scopeLabels.length ? (
                <span className="scope-pill on">
                  Only {scopeLabels.length === 1 ? scopeLabels[0] : `${scopeLabels.length} steps`}
                  <button type="button" aria-label="Change the whole workflow" onClick={() => (setPicked([]), setSelected(null))}>×</button>
                </span>
              ) : (
                <span className="scope-pill">Whole workflow</span>
              )}
              <span className="muted small ask-hint">Shift+click steps to change only those · <kbd>/</kbd> to type here</span>
            </div>
            <div className="ask-line">
              <input ref={askBox} value={prompt} onChange={e => setPrompt(e.target.value)} disabled={asking}
                aria-label="Ask labb to change the workflow"
                placeholder={pending ? "Refine it: “keep posting to the chat after all”" : "Change it: “run at 7, and ask me before it emails”"} />
              <button type="submit" className="primary" disabled={asking || !prompt.trim()}>{asking ? "Thinking…" : "Preview"}</button>
            </div>
          </form>
        )}
        </div>
        <div className={`side-panel${sheet ? " open" : ""}`} role="complementary" aria-label="Settings and runs">
          <div className="side-tabs" role="tablist">
            {([["settings", node ? "Step" : "Workflow"], ...(pending ? [["changes", "Changes"]] : []), ["runs", "Runs"], ["explain", "Explain"]] as [typeof panel, string][]).map(([tab, label]) => (
              <button key={tab} type="button" role="tab" aria-selected={panel === tab || (tab === "runs" && panel === "start")}
                onClick={() => (panel === tab && sheet ? setSheet(false) : tab === "explain" ? explain() : setPanel(tab))}>
                {label}
                {tab === "runs" && runId && runSteps.some(s => s.status === "running" || s.status === "waiting") && <span className="live-dot" aria-label="running" />}
              </button>
            ))}
            <span className="spacer" />
            <button type="button" className="sheet-toggle icon-button" aria-label={sheet ? "Hide the panel" : "Show the panel"} onClick={() => setSheet(!sheet)}>
              {sheet ? "▾" : "▴"}
            </button>
          </div>
          <div className="side-body">
          {panel === "changes" && pending ? (
            <div className="inspector changes-panel">
              {pending.reply && <p className="explain-text">{pending.reply}</p>}
              <ul className="change-list">
                {pending.changes.map((c, i) => (
                  <li key={i} className={`change-${c.kind}`}>
                    <span className="change-kind">{c.kind === "add" ? "Added" : c.kind === "remove" ? "Removed" : "Changed"}</span>
                    <div>
                      <b>{c.label}</b>: {c.text}
                      {c.details.map((d, j) => (
                        <div key={j} className="change-detail small">
                          {d.field}: {d.before && <del>{d.before}</del>} {d.before && "→"} <ins>{d.after || "(empty)"}</ins>
                        </div>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
              {pending.problems.length > 0 && (
                <div className="problem small">After this, {pending.problems.length} thing{pending.problems.length === 1 ? "" : "s"} to fix: {pending.problems[0].message}</div>
              )}
              <p className="muted small">Steps it doesn't mention stay as they are. Keep makes it one step you can undo.</p>
            </div>
          ) : panel === "explain" ? (
            <div className="inspector">
              <h3>In plain words</h3>
              {explained?.text ? <p className="explain-text">{explained.text}</p> : <p className="muted">Reading the workflow…</p>}
            </div>
          ) : panel === "start" ? (
            <div className="inspector">
              <h3>{runDraft ? "Test the draft" : `Run v${workflow.published_version}`}</h3>
              <RunForm definition={definition} busy={starting} label={runDraft ? "Test run" : "Run"}
                onStart={startRun} onCancel={() => setPanel(runId ? "runs" : "settings")} />
            </div>
          ) : panel === "runs" && runId ? (
            <RunPanel runId={runId} onSteps={setRunSteps} onOpenChat={onOpenChat} onFix={readOnly || asking ? undefined : fix}
              onOpenRun={next => (setRunSteps([]), setRunId(next))} onFocusStep={focusStep}
              onBack={() => (setRunId(null), setRunSteps([]))}
              onClose={() => (setRunId(null), setRunSteps([]), setPanel("settings"))} />
          ) : panel === "runs" ? (
            <div className="inspector runs-panel">
              <div className="row">
                <h3>Runs</h3>
                <span className="spacer" />
                {!readOnly && <button type="button" onClick={() => (setRunDraft(true), setPanel("start"))}>Test run</button>}
              </div>
              <RunList runs={runs} showWorkflow={false} onOpen={r => (setRunSteps([]), setRunId(r.id))}
                empty="No runs yet. Test run tries the draft; Run runs the published version." />
            </div>
          ) : node ? (
            <StepInspector key={node.id} node={node} definition={definition} catalog={catalog}
              lists={{ agents, models, skills, actions }} problems={problemsByNode[node.id] ?? []} readOnly={readOnly || previewing}
              onChange={next => change(d => ({ ...d, nodes: d.nodes.map(n => (n.id === next.id ? next : n)) }))}
              onDelete={() => remove([node.id])} />
          ) : (
            <WorkflowInspector workflow={workflow} readOnly={readOnly} onChange={changeMeta} onWebhook={setWebhook} webhookPath={webhookPath}
              onExport={() => window.open(studio.exportUrl(workflow.id), "_blank")}
              onDuplicate={async () => {
                try {
                  const copy = await studio.duplicate(workflow.id);
                  toast(`Made ${copy.name}`);
                } catch (e) {
                  toast((e as Error).message);
                }
              }}
              onDelete={async () => {
                if (!window.confirm(`Delete ${workflow.name} and its runs?`)) return;
                await studio.remove(workflow.id).catch(e => toast(e.message));
                onBack();
              }} />
          )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function Builder(props: Props) {
  return (
    <ReactFlowProvider>
      <BuilderView {...props} />
    </ReactFlowProvider>
  );
}
