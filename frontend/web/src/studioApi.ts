// Studio's API (server/studio/routes.py): workflows, runs, actions, secrets and the step catalog
import { api } from "./api";

export type FieldSpec = {
  name: string;
  label: string;
  kind: string;
  required: boolean;
  help: string;
  default: unknown;
  rows?: number;
  options?: [string, string][];
  min?: number;
  max?: number;
  // Only shown when other settings have these values
  show?: Record<string, unknown>;
};

export type NodeSpec = { label: string; category: string; icon: string; description: string; fields: FieldSpec[] };

export type Catalog = {
  nodes: Record<string, NodeSpec>;
  categories: [string, string][];
  tools: { name: string; description: string; args: string[] }[];
  events: { name: string; label: string }[];
  operators: { name: string; label: string }[];
  input_types: string[];
  schedule_kinds: string[];
  sandbox: boolean;
  is_admin: boolean;
};

export type StepNode = {
  id: string;
  key: string;
  type: string;
  label: string;
  position: { x: number; y: number };
  config: Record<string, unknown>;
  retries?: number;
  on_error?: "stop" | "continue";
};

export type StepEdge = { id: string; source: string; target: string; handle: string | null };
export type Definition = { nodes: StepNode[]; edges: StepEdge[] };
export type Problem = { node: string | null; message: string };

export type WorkflowSummary = {
  id: string;
  owner_id: string;
  owner: string | null;
  mine: boolean;
  name: string;
  emoji: string;
  description: string;
  published_version: number | null;
  shared: boolean;
  enabled: boolean;
  webhook: boolean;
  next_run: string | null;
  updated_at: string;
  steps: number;
};

export type Workflow = Omit<WorkflowSummary, "steps"> & {
  draft: Definition;
  problems: Problem[];
  versions: { version: number; published_at: string }[];
  schedule_text: string | null;
  tz: string;
};

export type StepState = {
  node_id: string;
  key: string;
  status: "running" | "done" | "skipped" | "failed" | "waiting" | "stopped";
  input: unknown;
  output: unknown;
  handle: string | null;
  error: string | null;
  trace: unknown[];
  attempt: number;
  started_at: string | null;
  finished_at: string | null;
  input_tokens: number;
  output_tokens: number;
};

export type RunStatus = "queued" | "running" | "waiting" | "done" | "failed" | "stopped";

export type Run = {
  id: string;
  workflow_id: string;
  user_id: string;
  version: number | null;
  definition?: Definition;
  trigger: string;
  inputs: Record<string, unknown>;
  status: RunStatus;
  error: string | null;
  result: unknown;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  resume_at: string | null;
  input_tokens: number;
  output_tokens: number;
  steps?: StepState[];
  workflow?: string;
  emoji?: string;
};

export type Approval = {
  run_id: string;
  workflow_id: string;
  workflow: string;
  node_id: string;
  label: string;
  message: string;
  since: string;
};

export type ActionInput = { name: string; label: string; description: string; required: boolean };

export type Action = {
  id: string;
  owner_id: string;
  owner: string | null;
  mine: boolean;
  kind: "http" | "prompt";
  name: string;
  emoji: string;
  description: string;
  config: Record<string, unknown> & { inputs?: ActionInput[] };
  shared: boolean;
};

export type Template = { id: string; name: string; emoji: string; description: string };

/** One change a model made to a workflow, for the person to read */
export type Change = {
  kind: "add" | "change" | "remove";
  node: string;
  label: string;
  text: string;
  details: { field: string; before: string; after: string }[];
};
export type Suggestion = { definition: Definition; changes: Change[]; reply: string; problems: Problem[] };
export type DraftQuestion = { question: string; why: string; options: string[] };
export type DraftTurn =
  | { kind: "questions"; questions: DraftQuestion[] }
  | ({ kind: "blueprint"; name?: string; emoji?: string } & Suggestion);

const tz = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

const get = <T,>(path: string) => api<T>(path);
const send = <T,>(path: string, method: string, body?: unknown) =>
  api<T>(path, { method, ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) });

export const studio = {
  catalog: () => get<Catalog>("/studio/catalog"),
  templates: () => get<Template[]>("/studio/templates"),
  /** The drafting room: questions, a blueprint, or the blueprint changed as asked (nothing is saved) */
  draft: (turn: { description?: string; answers?: { question: string; answer: string }[]; ask?: boolean;
    current?: Definition; request?: string }) => send<DraftTurn>("/studio/drafts", "POST", { ...turn, tz: tz() }),
  /** Try a workflow that isn't saved, once */
  tryDraft: (name: string, definition: Definition, inputs: Record<string, unknown>) =>
    send<Run>("/studio/try", "POST", { name, definition, inputs }),
  /** The draft changed as asked, or fixed for a failed run (not saved) */
  suggest: (id: string, fields: { prompt?: string; definition: Definition; selected?: string[]; run_id?: string }) =>
    send<Suggestion>(`/studio/workflows/${id}/suggest`, "POST", { ...fields, tz: tz() }),
  explain: (definition: Definition) => send<{ text: string }>("/studio/explain", "POST", { definition }),
  describe: (description: string) =>
    send<{ name: string; emoji: string; definition: Definition; problems: Problem[] }>("/studio/describe", "POST",
      { description, tz: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" }),

  workflows: () => get<WorkflowSummary[]>("/studio/workflows"),
  workflow: (id: string) => get<Workflow>(`/studio/workflows/${id}`),
  create: (fields: { name?: string; emoji?: string; description?: string; draft?: Definition; template?: string }) =>
    send<Workflow>("/studio/workflows", "POST", fields),
  update: (id: string, fields: Partial<Pick<Workflow, "name" | "emoji" | "description" | "draft" | "shared" | "enabled">>) =>
    send<Workflow>(`/studio/workflows/${id}`, "PATCH", fields),
  remove: (id: string) => send<void>(`/studio/workflows/${id}`, "DELETE"),
  duplicate: (id: string) => send<Workflow>(`/studio/workflows/${id}/duplicate`, "POST"),
  publish: (id: string) => send<Workflow>(`/studio/workflows/${id}/publish`, "POST"),
  webhook: (id: string, on: boolean) => send<{ on: boolean; path: string | null }>(`/studio/workflows/${id}/webhook?on=${on}`, "POST"),
  exportUrl: (id: string) => `/studio/workflows/${id}/export`,
  importWorkflow: (data: unknown) => send<Workflow>("/studio/import", "POST", data),

  run: (id: string, inputs: Record<string, unknown>, draft: boolean) =>
    send<Run>(`/studio/workflows/${id}/runs`, "POST", { inputs, draft }),
  runsOf: (id: string) => get<Run[]>(`/studio/workflows/${id}/runs`),
  myRuns: () => get<Run[]>("/studio/runs"),
  getRun: (id: string) => get<Run>(`/studio/runs/${id}`),
  stop: (id: string) => send<{ stopped: boolean }>(`/studio/runs/${id}/stop`, "POST"),
  approve: (id: string, nodeId: string, approved: boolean, comment = "") =>
    send<Run>(`/studio/runs/${id}/approve`, "POST", { node_id: nodeId, approved, comment }),
  rerun: (id: string, nodeId: string) => send<Run>(`/studio/runs/${id}/rerun`, "POST", { node_id: nodeId }),
  waiting: () => get<Approval[]>("/studio/waiting"),

  actions: () => get<Action[]>("/studio/actions"),
  createAction: (fields: Partial<Action>) => send<Action>("/studio/actions", "POST", fields),
  updateAction: (id: string, fields: Partial<Action>) => send<Action>(`/studio/actions/${id}`, "PATCH", fields),
  removeAction: (id: string) => send<void>(`/studio/actions/${id}`, "DELETE"),
  testAction: (id: string, args: Record<string, unknown>) =>
    send<{ ok: boolean; output?: unknown; error?: string; request?: unknown }>(`/studio/actions/${id}/test`, "POST", { args }),

  secrets: () => get<{ name: string; updated_at: string }[]>("/studio/secrets"),
  putSecret: (name: string, value: string) => send<{ name: string; updated_at: string }[]>(`/studio/secrets/${name}`, "PUT", { value }),
  removeSecret: (name: string) => send<void>(`/studio/secrets/${name}`, "DELETE"),
};
