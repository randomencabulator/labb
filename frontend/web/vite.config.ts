import { type ProxyOptions, defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, `npm run dev` serves the app and forwards the API to server.py
const api = "http://localhost:8000";
const API_PATHS = [
  "/chat", "/stop", "/threads", "/history", "/tool_result", "/models", "/cli_sessions", "/export", "/context", "/health", "/version",
  "/wheels", "/install.sh", "/sync", "/auth", "/keys", "/admin", "/usage", "/limits", "/artifacts", "/ollama", "/crawler", "/browser-api", "/documents",
  "/memories", "/notes", "/agents", "/engines", "/pcs", "/tasks", "/skills", "/studio", "/connections", "/shared",
];

// Some of the app's pages have the same addresses as API routes (/agents): loading one in the browser
// gets the app, as server/app.py's WebPages does
const pageLoad: ProxyOptions["bypass"] = req => {
  if (req.method !== "GET" || /\/(download|export|callback)$/.test(req.url?.split("?")[0] ?? "")) return undefined;
  const mode = req.headers["sec-fetch-mode"];
  return (mode ? mode === "navigate" : req.headers.accept?.includes("text/html")) ? "/index.html" : undefined;
};

export default defineConfig({
  plugins: [react()],
  // highlight.js with its common languages makes one ~550 kB bundle (170 kB gzipped); fine for this app
  build: { chunkSizeWarningLimit: 800 },
  server: {
    proxy: Object.fromEntries(API_PATHS.map(path => [path, { target: api, bypass: pageLoad }])),
  },
});
