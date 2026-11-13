// Studio: workflows of agents, skills, tools and actions; their runs; actions and secrets
import { useEffect, useMemo, useRef, useState } from "react";
import type { Agent, Model, Skill } from "../../api";
import { type Action, type Approval, type Catalog, type Run, type WorkflowSummary, studio } from "../../studioApi";
import { toast } from "../Toaster";
import { ActionsTab } from "./Actions";
import { Builder } from "./Builder";
import { DraftingRoom } from "./DraftingRoom";
import { RunList } from "./RunPanel";
import "./studio.css";

type Tab = "workflows" | "runs" | "actions";

type Props = {
  // The address after /studio: [] or ["runs"] or ["actions"] for a tab, ["workflows", id] for a workflow,
  // ["workflows", id, "runs", run id] with one of its runs open
  route: string[];
  onRoute: (route: string[], replace?: boolean) => void;
  agents: Agent[];
  models: Model[];
  skills: Skill[];
  onOpenChat: (threadId: string) => void;
  onWaitingChanged?: (count: number) => void;
};

function Waiting({ items, onAnswered, onOpen }: { items: Approval[]; onAnswered: () => void; onOpen: (a: Approval) => void }) {
  if (!items.length) return null;
  const answer = async (a: Approval, approved: boolean) => {
    try {
      await studio.approve(a.run_id, a.node_id, approved);
      onAnswered();
    } catch (e) {
      toast((e as Error).message);
    }
  };
  return (
    <section className="waiting-box">
      <h3>Waiting for you</h3>
      {items.map(a => (
        <div key={`${a.run_id}-${a.node_id}`} className="waiting-item">
          <div>
            <b>{a.workflow}</b> · {a.label}
            <p className="small">{a.message}</p>
          </div>
          <div className="row">
            <button type="button" className="primary" onClick={() => answer(a, true)}>Approve</button>
            <button type="button" onClick={() => answer(a, false)}>Reject</button>
            <button type="button" className="link" onClick={() => onOpen(a)}>Open</button>
          </div>
        </div>
      ))}
    </section>
  );
}

export default function Studio({ route, onRoute, agents, models, skills, onOpenChat, onWaitingChanged }: Props) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const tab: Tab = route[0] === "runs" || route[0] === "actions" ? route[0] : "workflows";
  const setTab = (next: Tab) => onRoute(next === "workflows" ? [] : [next]);
  const openId = route[0] === "workflows" ? (route[1] ?? null) : null;
  const openRun = openId && route[2] === "runs" ? (route[3] ?? null) : null;
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [actions, setActions] = useState<Action[]>([]);
  const [waiting, setWaiting] = useState<Approval[]>([]);
  const [creating, setCreating] = useState(false);
  const names = useMemo(() => ({
    agents: Object.fromEntries(agents.map(a => [a.id, `${a.emoji} ${a.name}`])),
    actions: Object.fromEntries(actions.map(a => [a.id, `${a.emoji} ${a.name}`])),
  }), [agents, actions]);

  const refresh = () => {
    studio.workflows().then(setWorkflows, e => toast(e.message));
    studio.actions().then(setActions, () => {});
    studio.waiting().then(w => (setWaiting(w), onWaitingChanged?.(w.length)), () => {});
  };
  useEffect(() => {
    studio.catalog().then(setCatalog, e => toast(e.message));
    refresh();
  }, []);
  useEffect(() => {
    if (tab === "runs" && !openId) studio.myRuns().then(setRuns, e => toast(e.message));
  }, [tab, openId]);

  // Back from a workflow: the list may have changed
  const wasOpen = useRef(openId);
  useEffect(() => {
    if (wasOpen.current && !openId) refresh();
    wasOpen.current = openId;
  }, [openId]);

  const open = (id: string | null, runId: string | null = null) =>
    onRoute(id ? ["workflows", id, ...(runId ? ["runs", runId] : [])] : []);

  if (!catalog) return <div className="studio-loading muted">Loading Studio…</div>;
  if (openId) {
    return (
      <Builder key={openId} id={openId} catalog={catalog} agents={agents} models={models} skills={skills}
        actions={actions} openRunId={openRun} onBack={() => open(null)} onOpenChat={onOpenChat}
        onRunChanged={runId => onRoute(["workflows", openId, ...(runId ? ["runs", runId] : [])], true)} />
    );
  }

  const run = async (w: WorkflowSummary) => open(w.id);

  return (
    <div className="studio">
      <div className="studio-head">
        <div className="studio-tabs" role="tablist">
          {(["workflows", "runs", "actions"] as Tab[]).map(t => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
              {t === "workflows" ? "Workflows" : t === "runs" ? "Runs" : catalog.is_admin ? <>Actions<span className="wide-only"> & secrets</span></> : "Actions"}
              {t === "workflows" && waiting.length > 0 && <span className="tab-count" title="Waiting for you">{waiting.length}</span>}
            </button>
          ))}
        </div>
        {tab === "workflows" && !creating && (
          <button type="button" className="primary new-button" onClick={() => setCreating(true)}>
            <span aria-hidden>+</span> New<span className="wide-only"> workflow</span>
          </button>
        )}
      </div>

      {tab === "workflows" && (
        <>
          <p className="muted intro">
            Workflows put your agents, skills, tools and actions to work together: one step after another, in parallel, with
            branches, approvals and Python. Run them by hand, on a schedule, from a webhook or the terminal (<code>labb run</code>).
          </p>
          <Waiting items={waiting} onAnswered={refresh} onOpen={a => open(a.workflow_id, a.run_id)} />
          {creating && <DraftingRoom catalog={catalog} names={names} onMade={id => (setCreating(false), open(id))} onCancel={() => setCreating(false)} />}
          {workflows.length === 0 && !creating ? (
            <div className="empty-state">
              <p>No workflows yet.</p>
              <button type="button" className="primary" onClick={() => setCreating(true)}>Make your first</button>
            </div>
          ) : (
            <div className="workflow-grid">
              {workflows.map(w => (
                <article key={w.id} className="workflow-card" onClick={() => run(w)}>
                  <div className="card-head">
                    <span className="emoji">{w.emoji}</span>
                    <b>{w.name}</b>
                  </div>
                  <p className="small muted">{w.description || `${w.steps} steps`}</p>
                  <div className="chips">
                    {w.published_version ? <span className="chip live">Live v{w.published_version}</span> : <span className="chip">Draft</span>}
                    {w.next_run && w.enabled && <span className="chip">Next {new Date(w.next_run).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</span>}
                    {w.webhook && <span className="chip">Webhook</span>}
                    {!w.enabled && <span className="chip off">Off</span>}
                    {w.shared && <span className="chip">{w.mine ? "Shared" : `By ${w.owner}`}</span>}
                  </div>
                </article>
              ))}
            </div>
          )}
        </>
      )}

      {tab === "runs" && <RunList runs={runs} onOpen={r => open(r.workflow_id, r.id)} empty="No runs yet. Open a workflow and test it." />}

      {tab === "actions" && <ActionsTab catalog={catalog} actions={actions} models={models} onChanged={refresh} />}
    </div>
  );
}
