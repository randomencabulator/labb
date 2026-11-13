// Call labb from this app's server, as the app's owner, for what they let it use (on the app's
// page: Settings → Use labb). labb gives the app LABB_URL and LABB_API_KEY in its environment then.
//
// Only ever from the server: the key is the app's secret. A page in the browser asks this app's own
// server, which asks labb.
//
//   const labb = require("./labb");
//   const answer = await labb.ask("Write a haiku about autumn"); // { text, conversation, model, ... }
//   await labb.ask("Another", { conversation: answer.conversation });
//   await labb.searchDocuments("invoice from March");
//   await labb.recall("favourite food"); await labb.remember("Likes pears");
//   await labb.agents(); await labb.askAgent(agentId, "What's new?");

const URL = (process.env.LABB_URL || "").replace(/\/+$/, "");
const KEY = process.env.LABB_API_KEY || "";
// A model's answer can take a while (labb waits up to 5 minutes)
const TIMEOUT_MS = 330_000;

class LabbError extends Error {}

const available = () => Boolean(URL && KEY);

async function call(method, path, body) {
  if (!available()) throw new LabbError("This app can't use labb yet: its owner turns that on, on its page (Settings → Use labb)");
  let response;
  try {
    response = await fetch(URL + path, {
      method,
      headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (error) {
    throw new LabbError(`Couldn't reach labb at ${URL}: ${error.message}`);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new LabbError(`labb said ${response.status}: ${data?.detail ?? response.statusText}`);
  return data;
}

module.exports = {
  LabbError,
  available,
  call,
  /** A model's answer (no tools): { text, conversation, model, input_tokens, output_tokens } */
  ask: (message, { conversation, model } = {}) => call("POST", "/app-api/ask", { message, conversation, model }),
  agents: () => call("GET", "/app-api/agents"),
  /** One of the owner's agents answers, with its own tools */
  askAgent: (agentId, message, { conversation } = {}) =>
    call("POST", `/app-api/agents/${encodeURIComponent(agentId)}/ask`, { message, conversation }),
  searchDocuments: (query, k = 5) => call("POST", "/app-api/documents/search", { query, k }),
  memories: () => call("GET", "/app-api/memories"),
  recall: (query, k = 5) => call("POST", "/app-api/memories/recall", { query, k }),
  remember: text => call("POST", "/app-api/memories", { text }),
};
