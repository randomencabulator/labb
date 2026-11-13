// The loop as a canvas: steps and the ways between them. A change is checked by the server like any other edit.
import { Background, Controls, type Connection, type Edge, Handle, type Node, type NodeProps, Position, ReactFlow, ReactFlowProvider, applyNodeChanges, type NodeChange, MarkerType, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Graph, layout } from "@dagrejs/dagre";
import { memo, useMemo, useState } from "react";
import type { EngineCatalog, Loop, LoopStep } from "../../api";

const WIDTH = 210;
const HEIGHT = 84;
const START = "start";
const END = "end";
// A long loop is panned rather than shrunk until its steps can't be read
const FIT_ZOOM = 0.7;

type BoxData = { step?: LoopStep; kindLabel: string; ways: (string | null)[]; about?: string; pseudo?: "start" | "end"; problem?: boolean };
type BoxNode = Node<BoxData, "box">;

function BoxView({ data, selected }: NodeProps<BoxNode>) {
  const { step, kindLabel, ways, pseudo, problem } = data;
  if (pseudo) {
    return (
      <div className={`loop-pseudo ${pseudo}`}>
        {pseudo === "end" && <Handle type="target" position={Position.Left} />}
        {pseudo === "start" ? "A message comes in" : "The reply is done"}
        {pseudo === "start" && <Handle type="source" position={Position.Right} id="out" />}
      </div>
    );
  }
  return (
    <div className={["loop-box", `kind-${step!.type}`, selected && "selected", problem && "has-problem"].filter(Boolean).join(" ")}>
      <Handle type="target" position={Position.Left} />
      <b>{step!.label || kindLabel}</b>
      {step!.label && <span className="muted">{kindLabel}</span>}
      {ways.length === 1 && ways[0] === null ? (
        <Handle type="source" position={Position.Right} id="out" />
      ) : (
        <div className="loop-ways">
          {ways.map(way => (
            <div key={way ?? "out"} className="loop-way">
              <span>{way}</span>
              <Handle type="source" position={Position.Right} id={way ?? "out"} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const NODE_TYPES = { box: memo(BoxView) };

/** Edges that go back along the path from the start: laying them out as forward ones tangles a loop */
function backEdges(loop: Loop) {
  const back = new Set<string>();
  const state = new Map<string, "open" | "done">();
  const walk = (id: string) => {
    state.set(id, "open");
    for (const edge of loop.edges.filter(e => e.source === id)) {
      if (state.get(edge.target) === "open") back.add(`${edge.source}>${edge.target}`);
      else if (!state.has(edge.target)) walk(edge.target);
    }
    state.set(id, "done");
  };
  walk(START);
  return back;
}

function arrange(loop: Loop): Loop {
  const graph = new Graph();
  graph.setGraph({ rankdir: "LR", nodesep: 70, ranksep: 90 });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const node of loop.nodes) graph.setNode(node.id, { width: WIDTH, height: HEIGHT });
  const back = backEdges(loop);
  for (const edge of loop.edges) {
    if (edge.source === START || edge.target === END || back.has(`${edge.source}>${edge.target}`)) continue;
    graph.setEdge(edge.source, edge.target);
  }
  layout(graph);
  return {
    ...loop,
    nodes: loop.nodes.map(node => {
      const placed = graph.node(node.id);
      return placed ? { ...node, position: { x: 240 + placed.x - WIDTH / 2, y: placed.y - HEIGHT / 2 } } : node;
    }),
  };
}

function newId(loop: Loop, base: string) {
  const taken = new Set(loop.nodes.map(n => n.id));
  for (let i = 1; ; i++) if (!taken.has(`${base}_${i}`)) return `${base}_${i}`;
}

const NEW_CONFIG: Record<string, Record<string, unknown>> = {
  route: { test: "reply_contains", value: "done" },
  model_step: { prompt: "Say what to ask here.\n\nThe request: {{user_message}}\nThe last reply: {{last_reply}}", save_as: "answer", as_note: false },
  note: { text: "Write the note here." },
  check: { command: "make test", timeout: 120 },
  python: { code: "# inputs has user_message, last_reply, messages and vars\nresult = {\"vars\": {}, \"note\": \"\"}" },
};

type Props = {
  loop: Loop;
  customized: boolean;
  catalog: EngineCatalog;
  readOnly: boolean;
  problems: string[];
  onChange: (loop: Loop) => void;
  onReset: () => void;
  sideOpen: boolean;
  onToggleSide: () => void;
};

export function LoopEditor(props: Props) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}

function Canvas({ loop, customized, catalog, readOnly, problems, onChange, onReset, sideOpen, onToggleSide }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const flow = useReactFlow();
  // After the steps were laid out anew, once they're on the canvas
  const refit = () => setTimeout(() => flow.fitView({ duration: 200, padding: 0.12, minZoom: FIT_ZOOM }), 60);
  const step = loop.nodes.find(n => n.id === selected) ?? null;
  const missing = Object.entries(catalog.steps).filter(([kind, info]) => !info.fixed || !loop.nodes.some(n => n.type === kind));
  const problemText = problems.join("\n");

  const nodes = useMemo<BoxNode[]>(() => {
    const xs = loop.nodes.map(n => n.position?.x ?? 0);
    const left = Math.min(...xs, 240);
    const right = Math.max(...xs, 240);
    const first = loop.edges.find(e => e.source === START)?.target;
    const firstY = loop.nodes.find(n => n.id === first)?.position?.y ?? 0;
    return [
      { id: START, type: "box", position: { x: left - 240, y: firstY + 12 }, draggable: false, selectable: false, data: { pseudo: "start", kindLabel: "", ways: [null] } },
      { id: END, type: "box", position: { x: right + 300, y: firstY + 12 }, draggable: false, selectable: false, data: { pseudo: "end", kindLabel: "", ways: [] } },
      ...loop.nodes.map((n, i) => {
        const info = catalog.steps[n.type];
        return {
          id: n.id, type: "box" as const, selected: n.id === selected, position: n.position ?? { x: 240 + i * 260, y: 0 }, draggable: !readOnly,
          data: { step: n, kindLabel: info?.label ?? n.type, ways: info?.ways ?? [null], problem: problemText.includes(n.label || info?.label || n.id) },
        };
      }),
    ];
  }, [loop, selected, catalog, readOnly, problemText]);

  const edges = useMemo<Edge[]>(() => loop.edges.map(e => ({
    id: `${e.source}:${e.handle ?? "out"}:${e.target}`, source: e.source, target: e.target, sourceHandle: e.handle ?? "out",
    label: e.handle ?? undefined, markerEnd: { type: MarkerType.ArrowClosed }, type: "smoothstep",
    animated: e.source === "llm_call" && e.handle === "tools",
  })), [loop]);

  const connect = (c: Connection) => {
    if (readOnly || !c.source || !c.target) return;
    const handle = c.sourceHandle === "out" || !c.sourceHandle ? null : c.sourceHandle;
    // One edge per way out: drawing a new one replaces the old
    onChange({ ...loop, edges: [...loop.edges.filter(e => !(e.source === c.source && (e.handle ?? null) === handle)), { source: c.source, target: c.target, handle }] });
  };

  const moved = (changes: NodeChange<BoxNode>[]) => {
    if (readOnly) return;
    const updated = applyNodeChanges(changes, nodes);
    const positions = changes.filter(c => c.type === "position" && !c.dragging);
    if (!positions.length) return;
    onChange({ ...loop, nodes: loop.nodes.map(n => ({ ...n, position: updated.find(u => u.id === n.id)?.position ?? n.position })) });
  };

  const add = (kind: string) => {
    const info = catalog.steps[kind];
    const id = info.fixed ? kind : newId(loop, kind);
    const right = Math.max(240, ...loop.nodes.map(n => n.position?.x ?? 0));
    onChange({ ...loop, nodes: [...loop.nodes, { id, type: kind, position: { x: right, y: 240 }, ...(NEW_CONFIG[kind] ? { config: NEW_CONFIG[kind] } : {}) }] });
    setSelected(id);
  };

  const removeStep = (id: string) => {
    onChange({ ...loop, nodes: loop.nodes.filter(n => n.id !== id), edges: loop.edges.filter(e => e.source !== id && e.target !== id) });
    setSelected(null);
  };

  const patch = (change: Partial<LoopStep>) => onChange({ ...loop, nodes: loop.nodes.map(n => (n.id === selected ? { ...n, ...change } : n)) });
  const setConfig = (key: string, value: unknown) => patch({ config: { ...(step?.config ?? {}), [key]: value } });

  return (
    <div className="loop-editor">
      <div className="loop-bar">
        {!readOnly && (
          <>
            <select aria-label="Start from a ready-made loop" value="" onChange={e => {
              const preset = catalog.presets[e.target.value];
              if (preset && (!customized || confirm(`Replace this loop with ${preset.label}?`))) {
                onChange(arrange(preset.loop));
                setSelected(null);
                refit();
              }
            }}>
              <option value="">Start from…</option>
              {Object.entries(catalog.presets).map(([key, preset]) => <option key={key} value={key}>{preset.label}: {preset.about}</option>)}
            </select>
            <select aria-label="Add a step" value="" onChange={e => e.target.value && add(e.target.value)}>
              <option value="">+ Add a step</option>
              {missing.map(([kind, info]) => <option key={kind} value={kind}>{info.label}{info.terminal_only ? " (terminal only)" : ""}</option>)}
            </select>
            <button type="button" onClick={() => { onChange(arrange(loop)); refit(); }}>Tidy up</button>
            {customized && <button type="button" onClick={() => { onReset(); setSelected(null); refit(); }}>Back to the default loop</button>}
          </>
        )}
        <label className="loop-cap" title="However the loop goes round, a reply makes at most this many model calls. An admin's limit can be lower.">
          Model calls per reply, at most
          <input type="number" min={1} max={1000} disabled={readOnly} value={loop.max_model_calls ?? catalog.max_model_calls}
            onChange={e => onChange({ ...loop, max_model_calls: Number(e.target.value) || 1 })} />
        </label>
        <button type="button" aria-pressed={sideOpen} onClick={() => { onToggleSide(); refit(); }}>{sideOpen ? "Hide the preview" : "Show the preview"}</button>
      </div>
      <div className="loop-area">
        <div className="loop-canvas">
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={NODE_TYPES} fitView fitViewOptions={{ padding: 0.12, minZoom: FIT_ZOOM }} minZoom={0.2} proOptions={{ hideAttribution: true }} onConnect={connect} onNodesChange={moved}
            onNodeClick={(_, node) => setSelected(node.id === START || node.id === END ? null : node.id)} onPaneClick={() => setSelected(null)}
            onEdgesDelete={deleted => !readOnly && onChange({ ...loop, edges: loop.edges.filter(e => !deleted.some(d => d.id === `${e.source}:${e.handle ?? "out"}:${e.target}`)) })}
            nodesConnectable={!readOnly} edgesReconnectable={false} deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
            onNodesDelete={deleted => deleted.forEach(n => n.id !== START && n.id !== END && !catalog.steps[loop.nodes.find(s => s.id === n.id)?.type ?? ""]?.required && removeStep(n.id))}>
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        {step && (
          <aside className="loop-inspector" aria-label="The selected step">
            <div className="loop-inspector-head">
              <b>{catalog.steps[step.type]?.label}</b>
              <button type="button" className="close" aria-label="Close" onClick={() => setSelected(null)}>×</button>
            </div>
            <p className="hint">{catalog.steps[step.type]?.about}</p>
            {catalog.steps[step.type]?.terminal_only && <p className="engine-warning">Terminal only: the web chat can't use an engine with this step.</p>}
            {catalog.steps[step.type]?.runs_python && !catalog.sandbox && <p className="engine-warning">In the web chat this needs the server's sandbox, which isn't set up. It works in the terminal.</p>}
            <fieldset disabled={readOnly} className="bare">
              {!catalog.steps[step.type]?.fixed && (
                <label className="field"><span>Name</span><input value={step.label ?? ""} placeholder={catalog.steps[step.type]?.label} onChange={e => patch({ label: e.target.value || undefined })} /></label>
              )}
              <StepFields step={step} loop={loop} catalog={catalog} set={setConfig} />
              {!catalog.steps[step.type]?.required && <button type="button" className="danger" onClick={() => removeStep(step.id)}>Remove this step</button>}
            </fieldset>
          </aside>
        )}
      </div>
    </div>
  );
}

function StepFields({ step, loop, catalog, set }: { step: LoopStep; loop: Loop; catalog: EngineCatalog; set: (key: string, value: unknown) => void }) {
  const config = step.config ?? {};
  const text = (key: string) => (config[key] as string | undefined) ?? "";
  const templates = <small className="muted">Can use {"{{user_message}}"}, {"{{last_reply}}"}, {"{{vars.name}}"}, {"{{today}}"}, {"{{workdir}}"}</small>;
  switch (step.type) {
    case "model_step":
      return (
        <>
          <label className="field"><span>What to ask</span><textarea rows={7} value={text("prompt")} onChange={e => set("prompt", e.target.value)} />{templates}</label>
          <label className="field"><span>Save the answer as</span><input value={text("save_as")} onChange={e => set("save_as", e.target.value)} /><small className="muted">Then it's {`{{vars.${text("save_as") || "name"}}}`}</small></label>
          <label className="check"><input type="checkbox" checked={Boolean(config.as_note)} onChange={e => set("as_note", e.target.checked)} /><span>Also give it to the model's next call as a note</span></label>
          <label className="field"><span>Model</span><input value={text("model")} placeholder="The run's model" onChange={e => set("model", e.target.value || undefined)} /></label>
        </>
      );
    case "note":
      return <label className="field"><span>The note</span><textarea rows={6} value={text("text")} onChange={e => set("text", e.target.value)} />{templates}</label>;
    case "check":
      return (
        <>
          <label className="field"><span>Command</span><input value={text("command")} onChange={e => set("command", e.target.value)} /><small className="muted">It runs without asking: it's yours. In plan mode it's skipped.</small></label>
          <label className="field"><span>Timeout (seconds)</span><input type="number" min={1} max={3600} value={Number(config.timeout ?? 120)} onChange={e => set("timeout", Number(e.target.value))} /></label>
        </>
      );
    case "python":
      return <label className="field"><span>Code</span><textarea className="code" rows={10} spellCheck={false} value={text("code")} onChange={e => set("code", e.target.value)} /><small className="muted">Reads <code>inputs</code>, sets <code>result</code>. In a process of its own, or the server's sandbox.</small></label>;
    case "route": {
      const test = text("test");
      return (
        <>
          <label className="field"><span>Goes the yes way when</span>
            <select value={test} onChange={e => set("test", e.target.value)}>
              {Object.entries(catalog.route_tests).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
          {(test === "var_contains" || test === "var_set") && <label className="field"><span>Saved value</span><input value={text("var")} onChange={e => set("var", e.target.value)} /></label>}
          {(test === "reply_contains" || test === "var_contains") && <label className="field"><span>Text</span><input value={text("value")} onChange={e => set("value", e.target.value)} /></label>}
          {test === "visits_over" && (
            <label className="field"><span>Which step</span>
              <select value={text("step") || step.id} onChange={e => set("step", e.target.value)}>
                {loop.nodes.map(n => <option key={n.id} value={n.id}>{n.label || catalog.steps[n.type]?.label}</option>)}
              </select>
            </label>
          )}
          {(test === "visits_over" || test === "model_calls_over") && <label className="field"><span>More than</span><input type="number" min={0} max={1000} value={Number(config.value ?? 0)} onChange={e => set("value", Number(e.target.value))} /></label>}
        </>
      );
    }
    default:
      return null;
  }
}
