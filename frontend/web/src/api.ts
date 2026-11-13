// The server's API (server.py): /chat streams newline-delimited JSON events

export type Stats = {
  model: string;
  duration_ms: number;
  first_token_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  generation_ms: number | null;
  llm_calls: number;
  tool_calls: number;
  stopped: boolean;
  // Newer replies: calls per tool, whether it failed, and when it ended
  tools?: Record<string, number>;
  error?: boolean;
  at?: string;
  // Which engine wrote it, as name@fingerprint, if not the default
  engine?: string;
};

export type ChatEvent =
  | { type: "thread"; thread_id: string }
  // From /history: what was typed, the names of attached files, and images as data URLs
  // `turn`: which turn of the chat it is, for a reply on a PC joined partway through
  | { type: "user"; content: string; files?: string[]; images?: string[]; turn?: number }
  | { type: "thinking"; content: string }
  | { type: "token"; content: string }
  | { type: "tool_call"; id?: string; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; id?: string; name: string | null; chars: number; first_line?: string; error?: boolean }
  | { type: "compact" }
  // The reply ended too early and goes on (`reason`: truncated, empty, announced, loop_unscheduled)
  | { type: "continue"; reason: string }
  // A model call failed for a moment and is tried again after `wait` seconds
  | { type: "retry"; wait: number; message: string }
  // Sent while nothing else is, so the connection stays open
  | { type: "ping" }
  // A tool that changes something (an MCP server's) waits for a yes: answer with answerApproval
  // About the browser, it has `view` (the chat, for the live view) and `handover` when it's the user's turn
  | { type: "approval"; id: string; title: string; detail: string; view?: string; handover?: boolean }
  // On a PC: the agent asks something, answer with answerQuestion; and what was answered, by anyone
  | { type: "question"; id: string; question: string; options: string[] }
  | { type: "answered"; id: string; allow?: boolean | null; text?: string | null }
  // After the first reply: the model titled the chat
  | { type: "title"; title: string }
  | { type: "error"; message: string }
  | ({ type: "stats" } & Stats)
  | { type: "done"; stats: Stats }
  | { type: "stopped"; stats: Stats };

// agent_id: the agent the chat is with; null for labb, the default assistant
// unread: a task wrote to it and it wasn't opened since
// shared: everyone on the server can read it at /shared/<thread id>
export type Chat = { thread_id: string; title: string; updated_at: string; pinned: boolean; agent_id: string | null; unread: boolean; shared: boolean };

// A labb session: synced from another PC (`synced`), or run on the server itself
// `pc_id`: it was started from the web page and runs on that PC, so it can go on from here
export type CliSession = { thread_id: string; machine: string; directory: string; title: string; updated_at: string; synced: boolean; pc_id?: string | null };

// vision: whether the model can see images; null if the server doesn't know.
// tools: false when the model can't call tools (search, documents, memories)
export type Model = { spec: string; name: string; vision: boolean | null; tools?: boolean };

export type Attachment = { name: string; kind: "text" | "image"; data: string; mime_type?: string };

// How much the model thinks before it answers (the server's llm.ANSWER_MODES)
export type AnswerMode = "fast" | "auto" | "thinking";

export type SendOptions = {
  model?: string;
  answerMode?: AnswerMode;
  // Replace the conversation from this user message on (editing a message, or regenerating)
  replaceFrom?: number;
  // With replaceFrom: the server keeps the replaced message's attachments
  keepAttachments?: boolean;
  attachments?: Attachment[];
  // A new chat's agent; a chat keeps the one it started with
  agentId?: string;
  // Trying out an agent as the editor has it; the chat isn't kept
  draft?: AgentFields;
  // A new chat on this connected PC, in this folder
  pc?: { id: string; folder: string };
  // The Web toggle: false leaves out web search and reading pages
  web?: boolean;
  // The Browser switch: true offers the browser tools
  browser?: boolean;
  // The Don't ask switch: the browser doesn't ask in this reply
  browserAuto?: boolean;
  // The engine to use from this message on; else the one the chat has
  engineId?: string;
  // Trying out an engine as the editor has it, or comparing saved ones; the chat isn't kept
  engineDraft?: Overlay;
  trial?: boolean;
  // The chat of one of your apps (thread app-<id>): it gets the app's tools
  appId?: string;
};

// Fired when the server says we're not logged in (any more): the page shows the login again
export const LOGGED_OUT = "labb:logged-out";

/**
 * fetch for the server's API: requests that change something carry the X-Labb header
 * (the server wants it with the session cookie, so other sites can't make them), and a
 * 401 means the session ended.
 */
async function request(path: string, init: RequestInit = {}) {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (method !== "GET" && method !== "HEAD") headers.set("X-Labb", "1");
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event(LOGGED_OUT));
  return response;
}

async function check(response: Response) {
  if (!response.ok) {
    // FastAPI puts the reason in `detail`
    const detail = await response
      .json()
      .then(body => body?.detail)
      .catch(() => null);
    throw new Error(typeof detail === "string" ? detail : `${response.status} ${response.statusText}`);
  }
  return response;
}

const json = (body: unknown) => ({ headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** A JSON request to the server's API, with its error as an Error; nothing for a 204 */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await check(await request(path, init));
  return (response.status === 204 ? undefined : await response.json()) as T;
}

/** Send a message and call `onEvent` for each event until the reply ends */
export async function streamChat(
  message: string,
  threadId: string | null,
  options: SendOptions,
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
) {
  const response = await check(
    await request("/chat", {
      method: "POST",
      ...json({
        message,
        thread_id: threadId,
        model: options.model,
        answer_mode: options.answerMode,
        replace_from: options.replaceFrom,
        keep_attachments: options.keepAttachments ?? false,
        attachments: options.attachments ?? [],
        agent_id: options.agentId,
        draft: options.draft,
        web: options.web ?? true,
        browser: options.browser ?? false,
        browser_auto: options.browserAuto ?? false,
        engine_id: options.engineId,
        engine_draft: options.engineDraft,
        trial: options.trial ?? false,
        pc_id: options.pc?.id,
        folder: options.pc?.folder,
        app_id: options.appId,
      }),
      signal,
    }),
  );
  await readEvents(response, onEvent);
}

/** Each line of a stream of events, until it ends */
async function readEvents(response: Response, onEvent: (event: ChatEvent) => void) {
  const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop()!;
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line));
  }
}

/** Join the reply being written in a chat, if there is one, from its start: after the page lost
 * its stream, or when it opens a chat that's answering (the reply goes on without a page) */
export async function followReply(threadId: string, onEvent: (event: ChatEvent) => void, signal: AbortSignal) {
  const response = await check(await request(`/chat/live?${new URLSearchParams({ thread_id: threadId })}`, { signal }));
  if (response.status !== 204) await readEvents(response, onEvent);
}

/** Join the reply being written in a session on a PC, if there is one, from its start */
export async function followPcReply(threadId: string, onEvent: (event: ChatEvent) => void, signal: AbortSignal) {
  const response = await check(await request(`/pcs/live?${new URLSearchParams({ thread_id: threadId })}`, { signal }));
  if (response.status !== 204) await readEvents(response, onEvent);
}

/** Answer what an agent on a PC asked (a `question` event) */
export async function answerQuestion(id: string, text: string) {
  await api("/chat/answer", { method: "POST", ...json({ id, text }) });
}

// Settings → Limits: each limit's place on the page, range, default and value
export type LimitInfo = {
  key: string;
  group: string;
  label: string;
  description: string;
  default: number;
  min: number;
  max: number;
  unit: string;
  value: number;
};

// `problems`: why a service's limits (the apps', the published pages') couldn't be shown
export type LimitsPage = { limits: LimitInfo[]; problems: string[] };

export const getLimits = () => api<LimitsPage>("/admin/limits?problems=true");

export const saveLimits = (values: Record<string, number | null>) =>
  api<LimitsPage>("/admin/limits?problems=true", { method: "PUT", ...json(values) });

// The Artifacts page (server/artifacts_routes.py): web pages built in your chats
export type ArtifactInfo = {
  name: string;
  title: string;
  created: string | null;
  thread: string | null;
  // The chat it came from, if it's still there
  chat: string | null;
  // The workflow whose Web page step made it
  workflow: string | null;
  // Where it's published; null before a build has worked
  url: string | null;
  build: { ok: boolean | null; at: string | null; error: string | null } | null;
};
export type ArtifactDetail = ArtifactInfo & { files: { path: string; size: number }[]; examples: number };

export const listArtifacts = () => api<{ available: boolean; artifacts: ArtifactInfo[] }>("/artifacts");
export const getArtifact = (name: string) => api<ArtifactDetail>(`/artifacts/${encodeURIComponent(name)}`);
const artifactFile = (name: string, path: string) =>
  `/artifacts/${encodeURIComponent(name)}/files/${path.split("/").map(encodeURIComponent).join("/")}`;
export const readArtifactFile = (name: string, path: string) => api<{ content: string }>(artifactFile(name, path));
export const writeArtifactFile = (name: string, path: string, content: string) =>
  api<{ path: string; size: number }>(artifactFile(name, path), { method: "PUT", ...json({ content }) });
export const deleteArtifactFile = (name: string, path: string) => api<void>(artifactFile(name, path), { method: "DELETE" });
// The same search as an app's files (AppSearch), over the page's own files
export const searchArtifactFiles = (name: string, q: string, options: AppSearchOptions, signal?: AbortSignal) =>
  api<AppSearch>(
    `/artifacts/${encodeURIComponent(name)}/search?${new URLSearchParams({ q, case: String(options.case), regex: String(options.regex), word: String(options.word) })}`,
    { signal },
  );
// Builds it and publishes it if that worked; `message` is the build's error when it didn't
export const publishArtifact = (name: string) =>
  api<{ ok: boolean; message: string }>(`/artifacts/${encodeURIComponent(name)}/publish`, { method: "POST" });
export const deleteArtifact = (name: string) => api<void>(`/artifacts/${encodeURIComponent(name)}`, { method: "DELETE" });

// Settings → Services (admins): switches and numbers for the server, the crawler, the browser and the sandbox
export type ServiceSetting = {
  key: string;
  group: string;
  label: string;
  description: string;
  kind: "bool" | "int" | "number";
  default: boolean | number;
  min: number;
  max: number;
  unit: string;
  value: boolean | number;
};
export type ServicesPage = { settings: ServiceSetting[]; problems: string[] };

export const getServices = () => api<ServicesPage>("/admin/services?problems=true");

export const saveServices = (values: Record<string, boolean | number | null>) =>
  api<ServicesPage>("/admin/services?problems=true", { method: "PUT", ...json(values) });

// The limits the pages check before sending, for anyone signed in
export type PublicLimits = {
  attachments_per_message: number;
  attachment_image_mb: number;
  attachment_text_chars: number;
  memory_chars: number;
  document_mb: number;
  agent_instructions_chars: number;
  task_prompt_chars: number;
};

export const getPublicLimits = () => api<PublicLimits>("/limits");

// Settings → Health: ok is true (working), false (not working) or null (a warning)
export type HealthCheck = { name: string; ok: boolean | null; detail: string };

export const getHealth = () => api<{ checks: HealthCheck[] }>("/admin/health");

// Settings → Health's resources (server/system.py): what everything uses now; a part that couldn't be read is null
export type ContainerUsage = {
  name: string;
  container: string;
  status: string;
  since: string | null;
  restarts: number;
  cpu?: number;
  memory?: number | null;
  memory_limit?: number | null;
};
export type AppUsage = { id: string; name: string; memory: number; cpu: number; containers: number };
export type SystemUsage = {
  problems: string[];
  host: {
    processors: number | null;
    cpu?: number;
    load?: number[];
    memory_total?: number;
    memory_used?: number;
    swap_total?: number;
    swap_used?: number;
    disk_total?: number;
    disk_used?: number;
    up_seconds?: number;
  } | null;
  server: {
    pid: number;
    up_seconds: number;
    threads: number;
    memory?: number;
    cpu_seconds: number;
    container_memory?: number;
    container_memory_limit?: number;
  } | null;
  database: { kind: string; bytes: number; chats: number } | null;
  ollama: { url: string; models: { name: string; memory: number; gpu_memory: number; until: string | null }[] } | null;
  containers: {
    project: string | null;
    services: ContainerUsage[];
    apps: { running: number; memory: number; cpu: number; top: AppUsage[] };
    at: string;
  } | null;
};

export const getSystem = () => api<SystemUsage>("/admin/system");

// Settings → MCP servers: the servers' settings, and each one's number of tools or why it couldn't connect
export type McpSettings = { servers: Record<string, unknown>; status: { servers: Record<string, number | string>; tools: string[] } };

export const getMcpSettings = () => api<McpSettings>("/admin/mcp");

export type McpField = { key: string; label: string; help: string; required: boolean; secret: boolean; default: string; choices: string[] };
export type McpOption = {
  kind: "remote" | "npm" | "pypi" | "oci";
  runtime: "npx" | "uvx" | "docker" | null;
  available: boolean;
  label: string;
  detail: string;
  config: Record<string, unknown>;
  fields: McpField[];
};
export type McpEntry = { id: string; title: string; suggested_name: string; description: string; version: string | null; url: string | null; featured: boolean; options: McpOption[] };
export type McpCatalog = { servers: McpEntry[]; next: string | null; error: string | null; runtimes: Record<string, boolean> };
export const getMcpCatalog = (q: string, cursor?: string | null) =>
  api<McpCatalog>(`/admin/mcp/catalog?${new URLSearchParams({ q, ...(cursor ? { cursor } : {}) })}`);
export const saveMcpSettings = (servers: Record<string, unknown>) =>
  api<McpSettings>("/admin/mcp", { method: "PUT", ...json({ servers }) });

/** Answer a tool call's question in the chat (an `approval` event) */
export async function answerApproval(id: string, allow: boolean, reason?: string) {
  await api("/chat/approve", { method: "POST", ...json({ id, allow, reason: reason || null }) });
}

// A chat someone shared: read-only for everyone on the server. `owner`: their username; `mine`: you shared it
export type SharedChat = { title: string; owner: string | null; mine: boolean; events: ChatEvent[] };

export const getSharedChat = (threadId: string) => api<SharedChat>(`/shared/${encodeURIComponent(threadId)}`);

export async function stopChat(threadId: string) {
  // 404 just means the reply already ended
  await request("/stop", { method: "POST", ...json({ thread_id: threadId }) });
}

export async function getThreads(): Promise<Chat[]> {
  return (await check(await request("/threads"))).json();
}

export async function updateThread(threadId: string, change: { title?: string; pinned?: boolean; shared?: boolean }) {
  await check(await request(`/threads/${encodeURIComponent(threadId)}`, { method: "PATCH", ...json(change) }));
}

export async function deleteThread(threadId: string) {
  await check(await request(`/threads/${encodeURIComponent(threadId)}`, { method: "DELETE" }));
}

export async function getHistory(threadId: string): Promise<ChatEvent[]> {
  return (await check(await request(`/history?thread_id=${encodeURIComponent(threadId)}`))).json();
}

/** The address that downloads a conversation as Markdown */
export function exportUrl(threadId: string) {
  return `/export?thread_id=${encodeURIComponent(threadId)}`;
}

export async function getToolResult(
  threadId: string,
  toolCallId: string,
): Promise<{ name: string; args: Record<string, unknown>; content: string | null }> {
  const query = new URLSearchParams({ thread_id: threadId, tool_call_id: toolCallId });
  return (await check(await request(`/tool_result?${query}`))).json();
}

/** What a chat's next model call would get (GET /context): tokens are estimates */
export type ContextInspection = {
  model: string;
  window: number;
  used: number;
  share: number;
  compact_at: number;
  will_compact: boolean;
  scale: number;
  summarized: number;
  total_messages: number;
  cleared: { count: number; chars: number };
  parts: { kind: "system" | "tools" | "summary" | "messages" | "recalled"; label: string; tokens: number }[];
  system: { label: string; tokens: number; text: string }[];
  next_to_clear: number | null;
  tools: { name: string; tokens: number; description: string; parameters: Record<string, unknown> }[];
  summary: string;
  messages: {
    index: number;
    role: string;
    name: string | null;
    tokens: number;
    chars: number;
    cleared: boolean;
    original_chars: number | null;
    recalled: boolean;
    reminder: boolean;
    images: number;
    text: string;
    tool_calls: { name: string; args: Record<string, unknown> }[];
  }[];
  // Each saved reply with the real tokens its call reported (null where it reported none)
  timeline: {
    index: number;
    input_tokens: number | null;
    output_tokens: number | null;
    cached_tokens: number | null;
    model: string | null;
    tool_calls: string[];
    summarized: boolean;
  }[];
};

/** What the next message would be sent with: the message box's choices, or a what-if's */
export type ContextOptions = { model?: string | null; answerMode?: AnswerMode; web?: boolean; browser?: boolean; engineId?: string };

const contextQuery = (threadId: string, options: ContextOptions) =>
  new URLSearchParams({
    thread_id: threadId,
    ...(options.model ? { model: options.model } : {}),
    ...(options.answerMode ? { answer_mode: options.answerMode } : {}),
    ...(options.web === false ? { web: "false" } : {}),
    ...(options.browser ? { browser: "true" } : {}),
    ...(options.engineId ? { engine_id: options.engineId } : {}),
  });

/** `scale`: another inspection's, to compare with it */
export async function getContext(threadId: string, options: ContextOptions = {}, scale?: number): Promise<ContextInspection> {
  const query = contextQuery(threadId, options);
  if (scale !== undefined) query.set("scale", String(scale));
  return api(`/context?${query}`);
}

/** The next model call's whole input, before each provider's own formatting */
export async function getContextRaw(threadId: string, options: ContextOptions = {}): Promise<{ model: string; messages: unknown[]; tools: unknown[] }> {
  return api(`/context/raw?${contextQuery(threadId, options)}`);
}

export type SavedMessage = {
  index: number;
  role: string;
  name: string | null;
  text: string;
  images: number;
  tool_calls: { name: string; args: Record<string, unknown> }[];
};

/** A message as it's saved, by its index: what a summary or a cleared note stands in for */
export async function getSavedMessage(threadId: string, number: number): Promise<SavedMessage> {
  return api(`/context/message?${new URLSearchParams({ thread_id: threadId, number: String(number) })}`);
}

export type TraceSpan = {
  id: string;
  // The call it ran inside, if any: running at the same time without one is running in parallel
  parent: string | null;
  kind: "model" | "tool" | "step";
  name: string;
  // Milliseconds from the reply's start; `end` is null while it runs
  start: number;
  end: number | null;
  status: "running" | "ok" | "error" | "stopped";
  first_token?: number | null;
  model?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  detail?: string;
  error?: string;
};

export type ReplyTrace = { id: string; asked: string; started_at: string; elapsed: number; live: boolean; truncated: boolean; spans: TraceSpan[] };

// A goal (the server's tools/goal.py): what it is and how far it got
export type Goal = {
  objective: string;
  status: "active" | "complete" | "blocked" | "paused" | "budget_limited";
  budget: number | null;
  used: number;
  tokens?: number;
  round?: number;
  note?: string;
  created?: string;
};

// What a chat is working on now (GET /context/work)
export type ConversationWork = {
  todos: { id?: number; task: string; status: string; note?: string }[];
  open: number;
  goal: Goal | null;
  plan: string | null;
  plan_file: string | null;
};

/** The chat's todo list, goal and approved plan, as they stand; a PC session's are asked of its PC */
export async function getWork(threadId: string): Promise<ConversationWork> {
  return api(`/context/work?${new URLSearchParams({ thread_id: threadId })}`);
}

/** What each of the chat's replies did and when, with the one being written last */
export async function getTraces(threadId: string): Promise<ReplyTrace[]> {
  return api(`/context/traces?${new URLSearchParams({ thread_id: threadId })}`);
}

/** Summarize every turn not summarized yet, now; the inspection as it is afterwards */
export async function summarizeNow(threadId: string, options: ContextOptions = {}): Promise<ContextInspection> {
  return api("/context/summarize", {
    method: "POST",
    ...json({ thread_id: threadId, model: options.model ?? undefined, answer_mode: options.answerMode, web: options.web ?? true, browser: options.browser ?? false, engine_id: options.engineId }),
  });
}

export async function getModels(): Promise<{ default: string; browser?: boolean; models: Model[] }> {
  return (await check(await request("/models"))).json();
}

export type BrowserState = { tabs: { tab: number; title: string; url: string; current: boolean }[]; width: number; height: number };

const browserPath = (threadId: string, what: string) => `/browser-api/${encodeURIComponent(threadId)}/${what}`;

export async function browserState(threadId: string): Promise<BrowserState> {
  return (await check(await request(browserPath(threadId, "state")))).json();
}

/** What the chat's browser shows; null when it has no page open */
export async function browserFrame(threadId: string): Promise<Blob | null> {
  const response = await request(browserPath(threadId, "frame"));
  if (response.status === 404) return null;
  return (await check(response)).blob();
}

/** Something the user did in the live view */
export async function browserInput(threadId: string, event: Record<string, unknown>) {
  await check(await request(browserPath(threadId, "input"), { method: "POST", ...json({ event }) }));
}

/** The sites the browser may click and type on without asking first */
export async function listTrustedSites(): Promise<string[]> {
  return (await (await check(await request("/browser-api/trusted"))).json()).sites;
}

export async function trustSite(site: string): Promise<string[]> {
  return (await (await check(await request("/browser-api/trusted", { method: "POST", ...json({ site }) }))).json()).sites;
}

export async function distrustSite(site: string) {
  await check(await request(`/browser-api/trusted/${encodeURIComponent(site)}`, { method: "DELETE" }));
}

export async function browserSignOut() {
  await check(await request("/browser-api/profile", { method: "DELETE" }));
}

/** Delete the server's copy of a session another PC synced */
export async function deleteSyncedSession(threadId: string) {
  await check(await request(`/sync/${encodeURIComponent(threadId)}`, { method: "DELETE" }));
}

export async function getCliSessions(): Promise<CliSession[]> {
  return (await check(await request("/cli_sessions"))).json();
}

// --- Accounts ---------------------------------------------------------------

export type Role = "admin" | "member";
export type User = { id: string; username: string; role: Role; created_at: string; disabled: boolean };
export type ApiKey = {
  id: string;
  user_id: string;
  username: string;
  name: string;
  // The key's first characters, to tell which one it is
  shown: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};
export type Invite = { id: string; role: Role; created_at: string; expires_at: string; created_by: string | null };

export type AuthStatus = { setup_needed: boolean; setup_token_needed?: boolean; user: User | null };

export async function getStatus(): Promise<AuthStatus> {
  return (await check(await request("/auth/status"))).json();
}

/** The first account on a fresh server, which becomes the admin; `token` is the setup link's */
export async function setUp(username: string, password: string, token?: string | null): Promise<User> {
  return (await check(await request("/auth/setup", { method: "POST", ...json({ username, password, token }) }))).json();
}

export async function logIn(username: string, password: string): Promise<User> {
  return (await check(await request("/auth/login", { method: "POST", ...json({ username, password }) }))).json();
}

export async function logOut() {
  await check(await request("/auth/logout", { method: "POST" }));
}

export async function changePassword(current: string, next: string) {
  await check(await request("/auth/password", { method: "POST", ...json({ current, new: next }) }));
}

export async function getInvite(token: string): Promise<{ role: Role; expires_at: string }> {
  return (await check(await request(`/auth/invite/${encodeURIComponent(token)}`))).json();
}

export async function acceptInvite(token: string, username: string, password: string): Promise<User> {
  return (
    await check(await request(`/auth/invite/${encodeURIComponent(token)}`, { method: "POST", ...json({ username, password }) }))
  ).json();
}

/** labb --login waiting for approval under a code */
export async function getDeviceLogin(code: string): Promise<{ machine: string; expires_at: string }> {
  return (await check(await request(`/auth/device/${encodeURIComponent(code)}`))).json();
}

export async function answerDeviceLogin(code: string, approve: boolean) {
  await check(await request(`/auth/device/${encodeURIComponent(code)}`, { method: "POST", ...json({ approve }) }));
}

export async function listKeys(): Promise<ApiKey[]> {
  return (await check(await request("/keys"))).json();
}

/** A new key; `key` is only in this answer */
export async function createKey(name: string): Promise<ApiKey & { key: string }> {
  return (await check(await request("/keys", { method: "POST", ...json({ name }) }))).json();
}

export async function revokeKey(id: string, asAdmin = false) {
  await check(await request(`${asAdmin ? "/admin/keys" : "/keys"}/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

// --- Admin ------------------------------------------------------------------

export async function listUsers(): Promise<User[]> {
  return (await check(await request("/admin/users"))).json();
}

export async function updateUser(id: string, change: { role?: Role; disabled?: boolean }): Promise<User> {
  return (await check(await request(`/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", ...json(change) }))).json();
}

export async function listInvites(): Promise<Invite[]> {
  return (await check(await request("/admin/invites"))).json();
}

/** A single-use invite; `url` is only in this answer */
export async function createInvite(role: Role): Promise<Invite & { url: string }> {
  return (await check(await request("/admin/invites", { method: "POST", ...json({ role }) }))).json();
}

export async function cancelInvite(id: string) {
  await check(await request(`/admin/invites/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

export async function listAllKeys(): Promise<ApiKey[]> {
  return (await check(await request("/admin/keys"))).json();
}

// --- Providers and models (Settings → Models, admins) ----------------------------------

// Fired after the model settings change, so the pickers ask for the list again
export const MODELS_CHANGED = "labb:models-changed";

export type ProviderKind = "ollama" | "openai" | "anthropic" | "openai_compatible";
export type From = "saved" | ".env" | "default";

export type Provider = {
  name: string;
  kind: ProviderKind;
  built_in: boolean;
  enabled: boolean;
  base_url: string;
  base_url_from: From;
  key_set: boolean;
  key_from: From | null;
  needs_key: boolean;
  // Whether a key can be set (Ollama Cloud's is optional; the Ollama server has none)
  takes_key: boolean;
  // Strict tool calls (the provider keeps arguments to the schema): null follows strict_default
  can_be_strict: boolean;
  strict_tools: boolean | null;
  strict_default: boolean;
};

export type ProviderFields = {
  base_url?: string;
  api_key?: string;
  clear_key?: boolean;
  enabled?: boolean;
  strict_tools?: boolean | null;
};
export type ProviderTest = { ok: true; models: string[]; count: number } | { ok: false; error: string };

export type ChosenModel = { spec: string; label: string; admins_only: boolean };
export type ModelSettings = {
  default: string | null;
  // Tokens to compact to; null for .env's CONTEXT_WINDOW (env_window) or each model's own
  context_window: number | null;
  current_window: number;
  env_window: number | null;
  // Tokens Ollama keeps in memory (num_ctx); null for .env's OLLAMA_NUM_CTX (env_num_ctx)
  ollama_num_ctx: number | null;
  current_num_ctx: number;
  env_num_ctx: number;
  offer: "all" | "chosen";
  chosen: ChosenModel[];
  current_default: string;
  available: string[];
};

export const listProviders = () => api<Provider[]>("/admin/providers");
export const saveProvider = (name: string, fields: ProviderFields) =>
  api<Provider[]>(`/admin/providers/${encodeURIComponent(name)}`, { method: "PUT", ...json(fields) });
export const removeProvider = (name: string) => api<void>(`/admin/providers/${encodeURIComponent(name)}`, { method: "DELETE" });
export const testProvider = (name: string, fields: ProviderFields) =>
  api<ProviderTest>(`/admin/providers/${encodeURIComponent(name)}/test`, { method: "POST", ...json(fields) });
export const getModelSettings = () => api<ModelSettings>("/admin/models");
export const saveModelSettings = (fields: Partial<Pick<ModelSettings, "default" | "context_window" | "ollama_num_ctx" | "offer" | "chosen">>) =>
  api<ModelSettings>("/admin/models", { method: "PUT", ...json(fields) });

// What models cost, for the usage stats: dollars per million tokens, [input, output, cached input, cache write]
// (the last two optional: the input price); keyed by a model's spec, name or pattern ("anthropic:claude-*")
export type Price = (number | null)[];
export type PriceSettings = { prices: Record<string, Price>; models: string[]; fillable: number };
export type PriceSuggestion = { price: Price; from: string };
export const getPrices = () => api<PriceSettings>("/admin/prices");
export const savePrices = (prices: Record<string, Price>) => api<PriceSettings>("/admin/prices", { method: "PUT", ...json({ prices }) });
export const suggestPrices = (models: string[]) =>
  api<{ suggestions: Record<string, PriceSuggestion>; source: string }>("/admin/prices/suggest", { method: "POST", ...json({ models }) });
export const repriceUsage = (everything = false) => api<{ updated: number }>("/admin/usage/reprice", { method: "POST", ...json({ everything }) });

// --- Integrations (server/connections.py) ------------------------------------

// An app people can connect (Google); `available` once an admin has set it up
export type AppConnection = {
  name: string;
  label: string;
  uses: string[];
  available: boolean;
  // status "expired": the app turned the connection down, so connect again.
  // missing: what the person didn't allow when connecting (some tools won't work)
  connection: { account: string; status: "ok" | "expired"; connected_at: string; missing: string[] } | null;
};

// Where to send the person to allow labb in; "paste": they paste the address they end up at
export type ConnectStart = { url: string; mode: "redirect" | "paste"; redirect_uri: string };

// An app's OAuth client, for admins (the secret is never sent back)
export type AppClient = {
  name: string;
  label: string;
  uses: string[];
  console: string;
  // How to make its OAuth client; the redirect URI comes after these
  steps: string[];
  id_hint: string;
  secret_hint: string;
  configured: boolean;
  client_id: string | null;
  source: "saved" | ".env" | null;
  redirect_uri: string;
  mode: "redirect" | "paste";
  public_url: string;
  connected_users: number;
};

export const listConnections = () => api<AppConnection[]>("/connections");
export const startConnection = (app: string) => api<ConnectStart>(`/connections/${encodeURIComponent(app)}/start`, { method: "POST" });
export const finishConnection = (app: string, url: string) =>
  api<AppConnection>(`/connections/${encodeURIComponent(app)}/finish`, { method: "POST", ...json({ url }) });
export const disconnectApp = (app: string) => api<void>(`/connections/${encodeURIComponent(app)}`, { method: "DELETE" });
export const listAppClients = () => api<AppClient[]>("/admin/apps");
export const saveAppClient = (app: string, fields: { client_id: string; client_secret?: string }) =>
  api<AppClient[]>(`/admin/apps/${encodeURIComponent(app)}`, { method: "PUT", ...json(fields) });
export const removeAppClient = (app: string) => api<void>(`/admin/apps/${encodeURIComponent(app)}`, { method: "DELETE" });

// --- Usage stats --------------------------------------------------------------

// A time range: `since`/`until` as ISO times (left out for everything), and the viewer's
// offset from UTC in minutes, so days and hours are local
export type UsageRange = { since?: string; until?: string; tz: number };

export type UsageTotals = {
  replies: number;
  input_tokens: number;
  output_tokens: number;
  duration_ms: number;
  tool_calls: number;
  stopped: number;
  errors: number;
  users: number;
  conversations: number;
  first: string | null;
  last: string | null;
  // Dollars, from the replies whose model had a price when they were recorded (null: none had); how many had one
  cost: number | null;
  priced_replies: number;
  // Of the input tokens: read from the provider's prompt cache, and written to it
  cache_read_tokens: number;
  cache_write_tokens: number;
};

export type Usage = {
  totals: UsageTotals;
  // `source` is "web" or the machine's name
  by_day: { day: string; source: string; replies: number; input_tokens: number; output_tokens: number; cost: number | null }[];
  by_model: {
    model: string;
    replies: number;
    input_tokens: number;
    output_tokens: number;
    avg_first_token_ms: number | null;
    max_first_token_ms: number | null;
    tokens_per_second: number | null;
    // How its tool calls came out: fixed by labb, rewritten with constrained decoding, still broken
    tool_calls: number;
    tool_repaired: number;
    tool_constrained: number;
    tool_failed: number;
    cost: number | null;
    priced_replies: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
  }[];
  by_source: { source: string; replies: number; input_tokens: number; output_tokens: number; last: string; cost: number | null }[];
  by_engine: { engine: string | null; replies: number; input_tokens: number; output_tokens: number; avg_duration_ms: number; llm_calls: number; tool_calls: number; errors: number }[];
  tools: { name: string; calls: number }[];
  by_hour: { hour: number; replies: number; avg_first_token_ms: number | null }[];
  // Your own usage only
  top_conversations?: { thread_id: string; title: string; source: string; replies: number; tokens: number; exists: boolean; cost: number | null }[];
  // The whole server only
  active_users?: { day: number; week: number; month: number };
  storage?: { bytes: number; chats: number; synced_sessions: number; cli_sessions: number; snapshots: number; usage: number };
};

export type UserUsage = {
  id: string;
  username: string;
  role: Role;
  disabled: boolean;
  replies: number;
  input_tokens: number;
  output_tokens: number;
  last_active: string | null;
  cost: number | null;
  models: string[];
  sources: string[];
};

export type GatewayUsage = {
  totals: { requests: number; failures: number; input_tokens: number; output_tokens: number; pages: number };
  by_model: { model: string; requests: number; failures: number; input_tokens: number; output_tokens: number; tokens_per_second: number | null }[];
  by_hour: { hour: number; requests: number; failures: number }[];
};

export type Machine = {
  key_id: string;
  name: string;
  username: string;
  last_used_at: string | null;
  version: string | null;
  machine: string | null;
  last_seen: string | null;
  outdated: boolean;
  requests: number;
  tokens: number;
  pages: number;
};

function rangeQuery(range: UsageRange, extra: Record<string, string> = {}) {
  const query = new URLSearchParams({ tz: String(range.tz), ...extra });
  if (range.since) query.set("since", range.since);
  if (range.until) query.set("until", range.until);
  return query.toString();
}

export async function getUsage(range: UsageRange): Promise<Usage> {
  return (await check(await request(`/usage?${rangeQuery(range)}`))).json();
}

/** Everyone's usage, or one user's */
export async function getServerUsage(range: UsageRange, userId?: string): Promise<Usage> {
  return (await check(await request(`/admin/usage?${rangeQuery(range, userId ? { user_id: userId } : {})}`))).json();
}

export async function getUsersUsage(range: UsageRange): Promise<UserUsage[]> {
  return (await check(await request(`/admin/usage/users?${rangeQuery(range)}`))).json();
}

export async function getGatewayUsage(range: UsageRange): Promise<GatewayUsage> {
  return (await check(await request(`/admin/usage/gateway?${rangeQuery(range)}`))).json();
}

export async function getMachines(range: UsageRange): Promise<{ server_version: string; machines: Machine[] }> {
  return (await check(await request(`/admin/usage/machines?${rangeQuery(range)}`))).json();
}

/** Download one of the usage endpoints as a CSV file */
export async function downloadUsageCsv(path: string, range: UsageRange, name: string, extra: Record<string, string> = {}) {
  const response = await check(await request(`${path}?${rangeQuery(range, extra)}`, { headers: { Accept: "text/csv" } }));
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `${name}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// --- Documents ----------------------------------------------------------------

// Searchable once `ready`; `pages` only for PDFs
export type DocumentInfo = {
  id: string;
  name: string;
  bytes: number;
  chars: number;
  pages: number | null;
  chunks: number;
  status: "indexing" | "ready" | "error";
  error: string | null;
  created_at: string;
};

// A document's text to read, page by page (`paged`: a PDF's pages; otherwise one page)
export type DocumentText = { id: string; name: string; pages: string[]; paged: boolean };

export const getDocumentText = (id: string) => api<DocumentText>(`/documents/${encodeURIComponent(id)}/text`);

export async function listDocuments(): Promise<DocumentInfo[]> {
  return (await check(await request("/documents"))).json();
}

/** Add a file (PDF or text) as base64; the server indexes it in the background */
export async function addDocument(name: string, data: string): Promise<DocumentInfo> {
  return (await check(await request("/documents", { method: "POST", ...json({ name, data }) }))).json();
}

export async function deleteDocument(id: string) {
  await check(await request(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

// --- Memories -----------------------------------------------------------------

// Pinned ones are in every conversation; the others are recalled when a message is about them
// reviewed: false for what the chat saved on its own, until you keep it (it isn't pinned into conversations till then)
export type Memory = { id: string; text: string; pinned: boolean; created_at: string; updated_at: string; reviewed: boolean };

export async function listMemories(): Promise<Memory[]> {
  return (await check(await request("/memories"))).json();
}

export async function addMemory(text: string, pinned: boolean): Promise<Memory & { existed?: boolean }> {
  return (await check(await request("/memories", { method: "POST", ...json({ text, pinned }) }))).json();
}

export async function updateMemory(id: string, change: { text?: string; pinned?: boolean; reviewed?: boolean }): Promise<Memory> {
  return (await check(await request(`/memories/${encodeURIComponent(id)}`, { method: "PATCH", ...json(change) }))).json();
}

export async function deleteMemory(id: string) {
  await check(await request(`/memories/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

export async function deleteAllMemories(): Promise<{ forgotten: number }> {
  return (await check(await request("/memories", { method: "DELETE" }))).json();
}

// --- Agents -------------------------------------------------------------------

// "mine": whoever chats, their own documents; or a list of the owner's documents
export type AgentDocuments = "mine" | "none" | string[];

export type Agent = {
  id: string;
  owner_id: string;
  owner: string | null;
  // Yours to change; the others are shared with everyone
  mine: boolean;
  name: string;
  emoji: string;
  description: string;
  instructions: string;
  // null: the server's default model
  model: string | null;
  // null: all the tools
  toolsets: string[] | null;
  documents: AgentDocuments;
  // "mine": whoever chats, their skills; or the names of skills the owner can use
  skills: "mine" | "none" | string[];
  // The agents it can ask, and the Studio actions it can use
  delegates: string[];
  actions: string[];
  memories: boolean;
  starters: string[];
  shared: boolean;
  created_at: string;
  updated_at: string;
  engine_id: string | null;
};

export type AgentFields = Partial<
  Pick<Agent, "name" | "emoji" | "description" | "instructions" | "model" | "toolsets" | "documents" | "skills" | "memories" | "starters" | "shared" | "delegates" | "actions" | "engine_id">
>;

export type ToolChoice = { name: string; label: string; description: string };

export async function listAgents(): Promise<Agent[]> {
  return (await check(await request("/agents"))).json();
}

export async function listAgentTools(): Promise<ToolChoice[]> {
  return (await check(await request("/agents/tools"))).json();
}

export async function createAgent(fields: AgentFields): Promise<Agent> {
  return (await check(await request("/agents", { method: "POST", ...json(fields) }))).json();
}

export async function updateAgent(id: string, fields: AgentFields): Promise<Agent> {
  return (await check(await request(`/agents/${encodeURIComponent(id)}`, { method: "PATCH", ...json(fields) }))).json();
}

export async function deleteAgent(id: string) {
  await check(await request(`/agents/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

export async function duplicateAgent(id: string): Promise<Agent> {
  return (await check(await request(`/agents/${encodeURIComponent(id)}/duplicate`, { method: "POST" }))).json();
}

/** Forget a chat that tried out an agent */
export async function endTrial(threadId: string) {
  await request(`/agents/try/${encodeURIComponent(threadId)}`, { method: "DELETE" });
}

// --- Tasks --------------------------------------------------------------------

// In the task's time zone; Monday is day 0
export type Schedule =
  | { kind: "once"; at: string }
  // Counted from `start`, which the server sets
  | { kind: "minutes" | "hours"; every: number; start?: string }
  | { kind: "daily" | "weekdays"; time: string }
  | { kind: "weekly"; days: number[]; time: string }
  | { kind: "days"; every: number; time: string; start?: string }
  // Days 29 to 31 are the last day in shorter months
  | { kind: "monthly"; day: number; time: string };

export type TaskRun = {
  id: string;
  task_id: string;
  thread_id: string | null;
  status: "queued" | "running" | "done" | "failed" | "stopped";
  error: string | null;
  trigger: "schedule" | "manual";
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  // For a task that keeps going until done: how its goal ended ("active": unfinished)
  goal: { status: Goal["status"]; note: string | null; used: number | null; budget: number | null } | null;
};

// The heartbeat (server/heartbeat.py): every so often, labb works through your to-dos on its own
export type HeartbeatSettings = {
  enabled: boolean;
  every_minutes: number;
  agent_id: string | null;
  per_beat: number;
  next_beat: string | null;
  // Why it turned itself off (failures in a row)
  disabled_reason: string | null;
  open: number;
  running: number;
  min_minutes: number;
};
export type HeartbeatTodo = {
  id: string;
  text: string;
  status: "open" | "running" | "done" | "failed";
  run_id: string | null;
  thread_id: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export const getHeartbeat = () => api<HeartbeatSettings>("/tasks/heartbeat");
export const saveHeartbeat = (fields: Partial<Pick<HeartbeatSettings, "enabled" | "every_minutes" | "agent_id" | "per_beat">>) =>
  api<HeartbeatSettings>("/tasks/heartbeat", { method: "PUT", ...json(fields) });
export const beatNow = () => api<{ queued: number }>("/tasks/heartbeat/beat", { method: "POST" });
export const listTodos = () => api<HeartbeatTodo[]>("/tasks/heartbeat/todos");
export const addTodo = (text: string) => api<HeartbeatTodo>("/tasks/heartbeat/todos", { method: "POST", ...json({ text }) });
export const changeTodo = (id: string, fields: { text?: string; reopen?: boolean }) =>
  api<HeartbeatTodo>(`/tasks/heartbeat/todos/${encodeURIComponent(id)}`, { method: "PATCH", ...json(fields) });
export const deleteTodo = (id: string) => api<void>(`/tasks/heartbeat/todos/${encodeURIComponent(id)}`, { method: "DELETE" });
export const clearDoneTodos = () => api<void>("/tasks/heartbeat/done", { method: "DELETE" });

export type Task = {
  id: string;
  name: string;
  prompt: string;
  agent_id: string | null;
  schedule: Schedule;
  // The schedule in words: "Weekdays at 08:00"
  when: string;
  tz: string;
  // Each run in a new chat, or always the same one
  mode: "new" | "same";
  thread_id: string | null;
  enabled: boolean;
  next_run: string | null;
  failures: number;
  // Why it turned itself off
  disabled_reason: string | null;
  last_run: TaskRun | null;
  // Keep going until done: each run is a goal the agent works on until it shows it's done
  goal: boolean;
};

export type TaskFields = Partial<Pick<Task, "name" | "prompt" | "agent_id" | "schedule" | "tz" | "mode" | "enabled" | "goal">>;

export async function listTasks(): Promise<Task[]> {
  return (await check(await request("/tasks"))).json();
}

export async function previewSchedule(schedule: Schedule, tz: string): Promise<{ when: string; next: string[] }> {
  return (await check(await request("/tasks/preview", { method: "POST", ...json({ schedule, tz }) }))).json();
}

export async function createTask(fields: TaskFields): Promise<Task> {
  return (await check(await request("/tasks", { method: "POST", ...json(fields) }))).json();
}

export async function updateTask(id: string, fields: TaskFields): Promise<Task> {
  return (await check(await request(`/tasks/${encodeURIComponent(id)}`, { method: "PATCH", ...json(fields) }))).json();
}

export async function deleteTask(id: string) {
  await check(await request(`/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

export async function runTask(id: string): Promise<TaskRun> {
  return (await check(await request(`/tasks/${encodeURIComponent(id)}/run`, { method: "POST" }))).json();
}

export async function listTaskRuns(id: string): Promise<TaskRun[]> {
  return (await check(await request(`/tasks/${encodeURIComponent(id)}/runs`))).json();
}

export async function stopTaskRun(id: string) {
  await check(await request(`/tasks/runs/${encodeURIComponent(id)}/stop`, { method: "POST" }));
}

// --- Skills -------------------------------------------------------------------

// A folder of instructions (SKILL.md) and the files it uses; `mine`, or shared by `owner`
export type Skill = {
  id: string;
  owner_id: string;
  owner: string | null;
  mine: boolean;
  name: string;
  description: string;
  shared: boolean;
  files: string[];
  created_at: string;
  updated_at: string;
};

/** The default agents (keys) and skills (names) you don't have, and how many there are */
export type MissingDefaults = { agents: string[]; skills: string[]; total: { agents: number; skills: number } };

export async function missingDefaults(): Promise<MissingDefaults> {
  return api("/defaults");
}

/** Add back the default agents and skills you deleted; what was added */
export async function restoreDefaults(): Promise<{ agents: string[]; skills: string[] }> {
  return api("/defaults/restore", { method: "POST" });
}

export async function listSkills(): Promise<Skill[]> {
  return (await check(await request("/skills"))).json();
}

export async function getSkill(id: string): Promise<Skill & { skill_md: string }> {
  return (await check(await request(`/skills/${encodeURIComponent(id)}`))).json();
}

/** A SKILL.md's text, or a zipped skill folder as base64; one with a name you have replaces it */
export async function addSkill(skill: { skill_md?: string; zip?: string; shared?: boolean }): Promise<Skill> {
  return (await check(await request("/skills", { method: "POST", ...json(skill) }))).json();
}

/** A SKILL.md the model writes from a description, or a change to `current`; nothing is saved */
export async function draftSkill(ask: string, current?: string): Promise<{ skill_md: string; name: string; description: string }> {
  return (await check(await request("/skills/draft", { method: "POST", ...json({ ask, current }) }))).json();
}

export async function updateSkill(id: string, change: { skill_md?: string; shared?: boolean }): Promise<Skill> {
  return (await check(await request(`/skills/${encodeURIComponent(id)}`, { method: "PATCH", ...json(change) }))).json();
}

export async function deleteSkill(id: string) {
  await check(await request(`/skills/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

export function skillDownloadUrl(id: string) {
  return `/skills/${encodeURIComponent(id)}/download`;
}

// --- Speech (the server's speech.py) ----------------------------------------------------

// Whether the server can listen (speech to text) and speak (text to speech)
export type SpeechInfo = { listen: boolean; speak: boolean; voice: string };

export type SpeechSettings = {
  listen: string | null;
  speak: string | null;
  voice: string;
  // Models and voices to pick from
  listen_options: string[];
  speak_options: string[];
  voices_options: string[];
};

export const getSpeech = () => api<SpeechInfo>("/speech");

/** What's said in a recording (base64) */
export const transcribe = (data: string, name: string) =>
  api<{ text: string }>("/speech/transcribe", { method: "POST", ...json({ data, name }) }).then(r => r.text);

/** Markdown read aloud, as MP3 */
/** MP3 of the text; `markdown: false` for a part from speechParts, which is already as it's said */
export async function speakText(text: string, { markdown = true, signal }: { markdown?: boolean; signal?: AbortSignal } = {}): Promise<Blob> {
  return (await check(await request("/speech/speak", { method: "POST", signal, ...json({ text, markdown }) }))).blob();
}

/** A reply as the parts it's read aloud in: a short first one, so it starts soon */
export async function speechParts(text: string, signal?: AbortSignal): Promise<string[]> {
  return (await (await check(await request("/speech/parts", { method: "POST", signal, ...json({ text }) }))).json()).parts;
}

// Settings → Models → Search (server/embedding.py): the model that embeds documents and memories
export type EmbeddingSettings = {
  model: string;
  default: string;
  // Whether Ollama answered with any models (else "pulled" can't be known)
  ollama: boolean;
  options: { model: string; about: string; pulled: boolean }[];
  // How many are embedded with the current model, of how many: after a change, everything is embedded again
  progress: Record<"documents" | "memories", { total: number; done: number }>;
};
export const getEmbeddingSettings = () => api<EmbeddingSettings>("/admin/embedding");
export const saveEmbeddingModel = (model: string) => api<EmbeddingSettings>("/admin/embedding", { method: "PUT", ...json({ model }) });

export const getSpeechSettings = () => api<SpeechSettings>("/admin/speech");
export const saveSpeechSettings = (fields: Partial<Pick<SpeechSettings, "listen" | "speak" | "voice">>) =>
  api<SpeechSettings>("/admin/speech", { method: "PUT", ...json(fields) });
export const testSpeech = () =>
  api<{ spoke: number | null; heard: string | null; error: string | null }>("/admin/speech/test", { method: "POST" });

// Engines: saved versions of the agent's core (server/engines.py, engine.py)
export type Overlay = {
  description?: string;
  prompts?: Record<string, string>;
  tool_prompts?: Record<string, string>;
  sections?: { off?: string[]; order?: string[]; add?: CustomSection[] };
  context?: Record<string, number | string[]>;
  input?: Record<string, boolean>;
  repair?: Record<string, boolean>;
  continue?: Record<string, number>;
  tools?: { allow?: string[]; deny?: string[]; descriptions?: Record<string, string>; custom?: CustomTool[] };
  model?: { model?: string; summary_model?: string; answer_mode?: string };
  loop?: Loop;
  hooks?: Record<string, HookEntry[]>;
  mcp?: Record<string, unknown>;
};
export type CustomSection = { id: string; label?: string; text: string; when?: string[] };
export type ToolParam = { name: string; description?: string; type?: string; required?: boolean };
export type CustomTool = { name: string; description: string; kind: string; command?: string; prompt?: string; action?: string; code?: string; model?: string; params?: ToolParam[] };
export type HookEntry = { command: string; matcher?: string; timeout?: number };
export type LoopStep = { id: string; type: string; label?: string; position?: { x: number; y: number }; config?: Record<string, unknown> };
export type LoopEdge = { source: string; target: string; handle?: string | null };
export type Loop = { nodes: LoopStep[]; edges: LoopEdge[]; max_model_calls?: number };

export type Engine = {
  id: string;
  slug: string;
  owner_id: string | null;
  owner: string | null;
  mine: boolean;
  built_in: boolean;
  shared: boolean;
  name: string;
  description: string;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  overlay: Overlay;
  changes: string[];
  // Why the web chat can't use it, if it can't
  web_problems: string[];
  runs_code: boolean;
};
export type EngineList = { engines: Engine[]; my_default: string | null; server_default: string | null };
export type EngineVersion = { version: number; overlay: Overlay; note: string; labb_version: string; created_at: string };
export type EngineChange = { where: string; before: unknown; after: unknown };
export type EngineCheck = { problems: string[]; web_problems: string[]; changes: EngineChange[] };
export type EngineStep = { label: string; ways: (string | null)[]; about: string; fixed?: boolean; required?: boolean; terminal_only?: boolean; runs_python?: boolean };
export type EngineCatalog = {
  prompts: Record<string, string>;
  context: Record<string, number | string[]>;
  ranges: Record<string, { min: number; max: number; whole: boolean }>;
  input: Record<string, boolean>;
  repair: Record<string, boolean>;
  continue: Record<string, number>;
  continue_fields: { key: string; label: string; about: string }[];
  max_continue: number;
  sections: { id: string; label: string; prompts: string[]; about: string }[];
  other_prompts: { key: string; label: string; about: string }[];
  context_fields: { key: string; label: string; about: string; unit?: string }[];
  switches: { section: "input" | "repair"; key: string; label: string; about: string }[];
  when: string[];
  toolsets: { name: string; label: string; prompt: string; terminal_only: boolean; tools: { name: string; description: string; params: string[] }[] }[];
  steps: Record<string, EngineStep>;
  route_tests: Record<string, string>;
  default_loop: Loop;
  presets: Record<string, { label: string; about: string; loop: Loop }>;
  max_model_calls: number;
  tool_kinds: Record<string, string>;
  param_types: string[];
  hook_events: string[];
  sandbox: boolean;
};
export type PromptSet = { id: string; name: string; prompts: string[]; updated_at: string };
export type EngineFields = Partial<Pick<Engine, "name" | "description" | "shared" | "overlay">> & { note?: string };

const sendJson = (method: string, body: unknown): RequestInit => ({ method, ...json(body) });

export const engineApi = {
  list: () => api<EngineList>("/engines"),
  catalog: () => api<EngineCatalog>("/engines/catalog"),
  get: (id: string) => api<Engine>(`/engines/${encodeURIComponent(id)}`),
  create: (fields: EngineFields) => api<Engine>("/engines", sendJson("POST", fields)),
  update: (id: string, fields: EngineFields) => api<Engine>(`/engines/${encodeURIComponent(id)}`, sendJson("PATCH", fields)),
  remove: (id: string) => api<void>(`/engines/${encodeURIComponent(id)}`, { method: "DELETE" }),
  duplicate: (id: string) => api<Engine>(`/engines/${encodeURIComponent(id)}/duplicate`, { method: "POST" }),
  versions: (id: string) => api<EngineVersion[]>(`/engines/${encodeURIComponent(id)}/versions`),
  restore: (id: string, version: number) => api<Engine>(`/engines/${encodeURIComponent(id)}/versions/${version}/restore`, { method: "POST" }),
  check: (overlay: Overlay) => api<EngineCheck>("/engines/check", sendJson("POST", { overlay })),
  preview: (overlay: Overlay, surface: string, model?: string | null) =>
    api<{ problems: string[]; seen: ContextInspection | null }>("/engines/preview", sendJson("POST", { overlay, surface, model: model ?? undefined })),
  suggest: (overlay: Overlay, ask: string, model?: string | null) =>
    api<{ overlay: Overlay; problems: string[]; changes: EngineChange[] }>("/engines/suggest", sendJson("POST", { overlay, ask, model: model ?? undefined })),
  setMyDefault: (id: string | null) => api<unknown>("/engines/my-default", sendJson("PUT", { engine_id: id })),
  setServerDefault: (id: string | null) => api<unknown>("/admin/engine-default", sendJson("PUT", { engine_id: id })),
  ofChat: (threadId: string) => api<{ engine_id: string | null }>(`/engines/chat/${encodeURIComponent(threadId)}`),
  promptSets: () => api<PromptSet[]>("/engines/prompt-sets"),
  savePromptSet: (id: string | null, name: string, prompts: string[]) =>
    id ? api<PromptSet>(`/engines/prompt-sets/${id}`, sendJson("PUT", { name, prompts })) : api<PromptSet>("/engines/prompt-sets", sendJson("POST", { name, prompts })),
  deletePromptSet: (id: string) => api<void>(`/engines/prompt-sets/${id}`, { method: "DELETE" }),
};

// PCs connected with labb connect (server/pcs.py); a session on one is written to like any chat
export type Pc = { id: string; name: string; folders: string[]; version: string | null; allow_all: boolean; last_seen: string; models: string[]; online: boolean };

export const listPcs = () => api<Pc[]>("/pcs");

// Notes: yours on the server, or a PC folder's (.labb/notes.md, asked of the PC)
export type Notes = { text: string; digest: string; topics: string[] };
// Whose notes: yours (null), a PC folder's, or the ones an app's chat keeps
export type NotesSource = { pcId: string; folder: string } | { appId: string } | null;
export type NotesChange = { action: "add" | "replace"; text: string; topic?: string; base?: string };

const notesPath = (source: NotesSource) =>
  !source ? "/notes" : "appId" in source ? `/apps/${encodeURIComponent(source.appId)}/notes` : `/pcs/${encodeURIComponent(source.pcId)}/notes`;

export const getNotes = (source: NotesSource) =>
  api<Notes>(source && "folder" in source ? `${notesPath(source)}?${new URLSearchParams({ folder: source.folder })}` : notesPath(source));

export const changeNotes = (source: NotesSource, change: NotesChange) =>
  api<Notes>(notesPath(source), { method: "POST", ...json(source && "folder" in source ? { ...change, folder: source.folder } : change) });
export const forgetPc = (id: string) => api<void>(`/pcs/${encodeURIComponent(id)}`, { method: "DELETE" });

// Apps: each a container of its own, started by the spawner, at an address of its own: `port` on labb's host
// status: Docker's (running, exited, restarting, created); detail: why it isn't running, or that it was restarted
export type App = {
  id: string;
  name: string;
  port: number;
  status: string;
  detail: string;
  restarts: number;
  created: string;
  // Which build of its files runs; legacy apps were made before apps had files
  build: number;
  legacy: boolean;
  memory_limit: string;
  // Bytes, while it runs (only for one app, not the list)
  memory_used?: number | null;
  // It has a database of its own (DATABASE_URL)
  database?: boolean;
  // It has a server (backend/); an app without one is its page only
  server?: boolean;
  // Which build of its page (frontend/, built by itself after each change) is served
  page?: number | null;
};
export const listApps = () => api<App[]>("/apps");
export const getApp = (id: string) => api<App>(`/apps/${encodeURIComponent(id)}`);
export const createApp = (name: string, starter: string, choices: Record<string, string>) =>
  api<App>("/apps", { method: "POST", ...json({ name, starter, choices }) });
/** A question a starter asks when an app is made (its colours, spacing and shadows, font) */
export type StarterChoice = {
  name: string;
  title: string;
  options: { name: string; title: string; description: string; default?: boolean; swatch?: string }[];
};
export type Starter = { name: string; title: string; description: string; choices: StarterChoice[] };
export const listStarters = () => api<Starter[]>("/apps/starters");
export type AppFile = { path: string; size: number };
const filePath = (id: string, path: string) =>
  `/apps/${encodeURIComponent(id)}/files/${path.split("/").map(encodeURIComponent).join("/")}`;
export const appFiles = (id: string) => api<AppFile[]>(`/apps/${encodeURIComponent(id)}/files`);
export const readAppFile = (id: string, path: string) => api<{ path: string; content: string }>(filePath(id, path));
export const writeAppFile = (id: string, path: string, content: string) =>
  api<AppFile>(filePath(id, path), { method: "PUT", ...json({ content }) });
// Where some text is in an app's files: the lines (ranges are [start, end] in `text`), and the files whose path has it
export type AppSearchOptions = { case: boolean; regex: boolean; word: boolean };
export type AppSearchMatch = { path: string; line: number; column: number; text: string; ranges: [number, number][] };
export type AppSearch = { matches: AppSearchMatch[]; files: string[]; more: boolean };
export const searchAppFiles = (id: string, q: string, options: AppSearchOptions, signal?: AbortSignal) =>
  api<AppSearch>(
    `/apps/${encodeURIComponent(id)}/search?${new URLSearchParams({ q, case: String(options.case), regex: String(options.regex), word: String(options.word) })}`,
    { signal },
  );
export const deleteAppFile = (id: string, path: string) => api<void>(filePath(id, path), { method: "DELETE" });
// A build of the app's files: if it worked, the new build runs in its place
export type Build = { build: number; ok: boolean | null; error: string | null; log: string; at?: string | null; building?: boolean };
export const buildApp = (id: string) =>
  api<{ ok: boolean; error: string | null; log: string; app: App }>(`/apps/${encodeURIComponent(id)}/build`, { method: "POST" });
export const lastBuild = (id: string) => api<Build>(`/apps/${encodeURIComponent(id)}/build`);
// Its page's last build: frontend/ is built and goes live by itself after each change
export type PageBuild = Build & { page: boolean; pending: boolean; served: number | null };
export const pageBuild = (id: string) => api<PageBuild>(`/apps/${encodeURIComponent(id)}/page`);
// An app's environment variables (a secret's value never comes back: null) and its data in /data
export type AppVariable = { name: string; value: string | null; secret: boolean };
export type AppSettings = { variables: AppVariable[]; data: { path: string; size: number; limit: number } };
export const appSettings = (id: string) => api<AppSettings>(`/apps/${encodeURIComponent(id)}/settings`);
/** Its variables, all of them; a secret with a null value keeps the one it has. The app starts again with them. */
export const saveAppSettings = (id: string, variables: AppVariable[]) =>
  api<AppSettings & { app: App }>(`/apps/${encodeURIComponent(id)}/settings`, { method: "PUT", ...json({ variables }) });
export const appDataUrl = (id: string) => `/apps/${encodeURIComponent(id)}/data/download`;
// Its files with their history as a .git (a git working copy), as a zip
export const appSourceUrl = (id: string) => `/apps/${encodeURIComponent(id)}/source/download`;
// Its chats on its page: sessions, each starting afresh (threads app-<id> and app-<id>--<session>), newest first
export type AppSession = { thread_id: string; title: string; updated_at: string };
export const listAppSessions = (id: string) => api<AppSession[]>(`/apps/${encodeURIComponent(id)}/sessions`);
/** The first message of a new session that starts again from a stuck one: what was asked there and where the app is */
export const appFreshStart = (id: string, thread: string) =>
  api<{ message: string }>(`/apps/${encodeURIComponent(id)}/fresh-start?thread=${encodeURIComponent(thread)}`);
// What an app may use of labb, with a key of its own (only its start is shown)
export type AppLabb = {
  scopes: string[];
  key: { shown: string; created_at: string; last_used_at: string | null } | null;
  url: string;
  available: { name: string; description: string }[];
};
export const appLabb = (id: string) => api<AppLabb>(`/apps/${encodeURIComponent(id)}/labb`);
/** What it may use; the first time it gets a key (and restarts), none and its key goes */
export const setAppLabb = (id: string, scopes: string[]) =>
  api<AppLabb>(`/apps/${encodeURIComponent(id)}/labb`, { method: "PUT", ...json({ scopes }) });
// Its versions: one is saved at each build; `unsaved` is the files changed since the last
export type AppVersion = { id: string; message: string; at: string; files: string[] };
export const appVersions = (id: string) => api<{ versions: AppVersion[]; unsaved: string[] }>(`/apps/${encodeURIComponent(id)}/versions`);
/** What a version changed, or ("unsaved") what changed since the last one, as a unified diff */
export const appVersionDiff = (id: string, version: string) =>
  api<{ diff: string }>(`/apps/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/diff`);
/** Its files as they were in that version, saved as a new one (changes not built are kept first); build to run them */
export const restoreAppVersion = (id: string, version: string) =>
  api<{ version: string | null; versions: AppVersion[]; unsaved: string[] }>(
    `/apps/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/restore`,
    { method: "POST" },
  );
// Its database: its own, on the apps' Postgres; tables, migrations, and SQL as the app (read-only unless `write`)
export type AppTable = {
  schema: string;
  name: string;
  kind: "table" | "view";
  // Counted exactly up to 10,000; rows_more past it
  rows: number;
  rows_more?: boolean;
  columns: { name: string; type: string; nullable: boolean; default: string | null }[];
};
export type AppDatabase =
  | { available: boolean; enabled: false }
  | {
      available: true;
      enabled: true;
      name: string;
      size: number;
      limit: number;
      tables: AppTable[];
      migrations: { name: string; applied_at: string }[];
      pending: string[];
      // Its API: /rest/ its tables and /auth/ sign-in for its own users, at its port + 100
      api: { enabled: boolean; port: number; status: string | null; signups: boolean | null; origin?: string; url?: string };
    };
export type QueryResult = { columns: string[]; rows: unknown[][]; truncated?: boolean; more?: boolean; rowcount?: number; written?: boolean; offset?: number };
const dbPath = (id: string) => `/apps/${encodeURIComponent(id)}/database`;
export const appDatabase = (id: string) => api<AppDatabase>(dbPath(id));
export const addAppDatabase = (id: string) =>
  api<AppDatabase & { app: App; migrated: { ok: boolean; log: string; error: string | null } }>(dbPath(id), { method: "POST" });
export const removeAppDatabase = (id: string) => api<{ app: App }>(dbPath(id), { method: "DELETE" });
export const appDatabaseRows = (id: string, schema: string, table: string, offset = 0, limit = 100) =>
  api<QueryResult>(`${dbPath(id)}/rows?${new URLSearchParams({ schema, table, offset: String(offset), limit: String(limit) })}`);
export const appDatabaseQuery = (id: string, sql: string, write = false) =>
  api<QueryResult>(`${dbPath(id)}/query`, { method: "POST", ...json({ sql, write }) });
export const appDatabaseDownloadUrl = (id: string) => `${dbPath(id)}/download`;
export const addAppApi = (id: string) => api<AppDatabase>(`/apps/${encodeURIComponent(id)}/api`, { method: "POST" });
export const removeAppApi = (id: string) => api<AppDatabase>(`/apps/${encodeURIComponent(id)}/api`, { method: "DELETE" });
export const setAppSignups = (id: string, signups: boolean) =>
  api<AppDatabase>(`/apps/${encodeURIComponent(id)}/api`, { method: "PATCH", ...json({ signups }) });
/** Someone who signed up on an app's API: the app's own user, not labb's */
export type AppUser = { id: string; email: string; disabled: boolean; created_at: string; last_sign_in_at: string | null; sessions: number };
export const appUsers = (id: string, offset = 0) =>
  api<{ users: AppUser[]; total: number; offset: number }>(`/apps/${encodeURIComponent(id)}/users?offset=${offset}`);
export const setAppUserDisabled = (id: string, user: string, disabled: boolean) =>
  api<void>(`/apps/${encodeURIComponent(id)}/users/${encodeURIComponent(user)}`, { method: "PATCH", ...json({ disabled }) });
export const removeAppUser = (id: string, user: string) =>
  api<void>(`/apps/${encodeURIComponent(id)}/users/${encodeURIComponent(user)}`, { method: "DELETE" });
export const newAppLabbKey = (id: string) => api<AppLabb>(`/apps/${encodeURIComponent(id)}/labb/key`, { method: "POST" });
export const deleteApp = (id: string) => api<void>(`/apps/${encodeURIComponent(id)}`, { method: "DELETE" });
export const changeApp = (id: string, action: "stop" | "start" | "restart") =>
  api<App>(`/apps/${encodeURIComponent(id)}/${action}`, { method: "POST" });
/** The end of what the app wrote, with Docker's timestamps */
export const appLogs = (id: string, tail = 300) =>
  api<{ lines: string }>(`/apps/${encodeURIComponent(id)}/logs?tail=${tail}`);
/** Where to open an app (its own port on labb's host); `ready` is false when it didn't answer in time */
export const openApp = (id: string) =>
  api<{ url: string; origin: string; ready: boolean }>(`/apps/${encodeURIComponent(id)}/open`, { method: "POST" });

// --- The inbox (server/inbox.py) ---------------------------------------------------------

export type InboxLevel = "info" | "success" | "warning" | "error";
// What happened, for whom: `link` is the page it's about; `key` names what it's about, and a newer
// one with the same key replaces it while it's unread (keeping its id)
export type InboxItem = {
  id: string;
  kind: "task" | "studio" | "app" | "document" | "account" | "health" | string;
  title: string;
  body: string;
  link: string | null;
  key: string | null;
  level: InboxLevel;
  created_at: string;
  read: boolean;
};
// The live stream's events (GET /inbox/stream)
export type InboxEvent = { type: "notification"; item: InboxItem; unread: number } | { type: "unread"; unread: number };

export async function listInbox(unread = false): Promise<{ items: InboxItem[]; unread: number }> {
  return (await check(await request(`/inbox${unread ? "?unread=true" : ""}`))).json();
}

/** Mark some read, or all of them without `ids`; how many are still unread */
export async function markInboxRead(ids?: string[]): Promise<{ unread: number }> {
  return (await check(await request("/inbox/read", { method: "POST", ...json(ids ? { ids } : {}) }))).json();
}

export async function deleteInboxItem(id: string) {
  await check(await request(`/inbox/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

/** Delete the ones already read */
export async function clearReadInbox() {
  await check(await request("/inbox", { method: "DELETE" }));
}

/** Follow the inbox live; the browser reconnects by itself. Returns the function that stops it. */
export function followInbox(onEvent: (event: InboxEvent) => void, onOpen?: () => void): () => void {
  const source = new EventSource("/inbox/stream");
  source.onmessage = message => {
    try {
      onEvent(JSON.parse(message.data));
    } catch {
      // Not one of ours
    }
  };
  if (onOpen) source.onopen = onOpen;
  return () => source.close();
}
