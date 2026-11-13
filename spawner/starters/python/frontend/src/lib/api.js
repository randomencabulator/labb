// The app's own server, at /api/…: JSON both ways. A call that fails throws an Error with what the
// server said, so `catch (error) { toast.error(error.message) }` shows it.
//
//   const items = await api("/api/items");
//   const item = await api("/api/items", { method: "POST", body: { title: "Milk" } });
//   await api(`/api/items/${item.id}`, { method: "PATCH", body: { done: true } });
//   await api(`/api/items/${item.id}`, { method: "DELETE" });
export async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let data = text;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // Not JSON: the text itself
  }
  if (!response.ok) throw new Error(problem(data) || `${method} ${path} failed (${response.status})`);
  return data;
}

// What a failed call's answer says: FastAPI's `detail` (a text, or a list of what's wrong), Express's `error`
function problem(data) {
  const said = data && typeof data === "object" ? (data.detail ?? data.error) : data;
  if (Array.isArray(said)) return said.map(item => item.msg ?? String(item)).join("; ");
  return typeof said === "string" ? said.slice(0, 300) : "";
}
